"""Seven-stage compression orchestration for a single PDF.

The pipeline is the only place that writes output files. Every stage mutates an
open document in place; the save is atomic (temp file in the destination
directory) and the never-grow guardrail decides whether the compressed bytes or
a faithful copy of the original are promoted.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from ..config import CompressionConfig
from ..formatting import format_bytes, format_percent
from ..i18n import t
from ..logging_setup import get_logger
from .analyzer import CategoryBreakdown, audit_pdf_bytes, calculate_effective_image_dpis
from .deduplicator import DeduplicationStats, deduplicate_resources
from .font_optimizer import FontOptimizationStats, optimize_fonts
from .image_optimizer import ImageOptimizationStats, optimize_all_images
from .signing import SigningError, sign_pdf_file
from .stream_optimizer import StreamOptimizationStats, recompress_content_streams, strip_structural_bloat
from .unlock import PasswordRequiredError, UnsupportedProtectionError, open_and_unlock_pdf

log = get_logger()

TEMP_PREFIX = ".ozempdf_"
# OneDrive's sync engine, antivirus scanners and the search indexer take
# short-lived handles on files they are syncing or inspecting, so a write can
# be denied for a moment and succeed immediately afterwards. Only access
# denials are retried, so a genuine permission problem still fails.
LOCK_RETRY_DELAYS = (0.05, 0.15, 0.4)


def _with_lock_retry(operation, *args, **kwargs):
    """Run *operation*, retrying brief access denials from cloud/lock holders."""
    for delay in (*LOCK_RETRY_DELAYS, None):
        try:
            return operation(*args, **kwargs)
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)



@dataclass(slots=True)
class FileCompressionResult:
    """Everything the app knows about one file after a run."""

    input_path: str
    output_path: str
    success: bool
    error_message: str = ""
    page_count: int = 0
    original_bytes: int = 0
    compressed_bytes: int = 0
    before_breakdown: CategoryBreakdown = field(default_factory=CategoryBreakdown)
    after_breakdown: CategoryBreakdown = field(default_factory=CategoryBreakdown)
    images_inspected: int = 0
    images_optimized: int = 0
    opaque_smasks_stripped: int = 0
    images_downsampled: int = 0
    images_indexed: int = 0
    duplicate_images_merged: int = 0
    font_streams_optimized: int = 0
    cmaps_optimized: int = 0
    signatures_flattened: int = 0
    was_encrypted: bool = False
    protection_kind: str = "none"
    protection_removed: bool = False
    required_password: bool = False
    kept_original_guardrail: bool = False
    already_optimal: bool = False
    signed: bool = False
    output_mode: str = "suffix"
    elapsed_seconds: float = 0.0

    @property
    def bytes_saved(self) -> int:
        """Bytes removed, floored at zero; a failed file produced no output."""
        if not self.success:
            return 0
        return max(0, self.original_bytes - self.compressed_bytes)

    @property
    def reduction_percent(self) -> float:
        """Percentage of the original removed, 0 when nothing was saved or the file failed."""
        if self.original_bytes <= 0:
            return 0.0
        return round(self.bytes_saved / self.original_bytes * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        """JSON shape documented in Appendix B.1 (plus the D-12/D-17 fixes)."""
        return {
            "input_path": self.input_path,
            "output_path": self.output_path,
            "success": self.success,
            "error_message": self.error_message,
            "page_count": self.page_count,
            "original_bytes": self.original_bytes,
            "compressed_bytes": self.compressed_bytes,
            "bytes_saved": self.bytes_saved,
            "reduction_percent": self.reduction_percent,
            "before_breakdown": self.before_breakdown.to_dict(),
            "after_breakdown": self.after_breakdown.to_dict(),
            "images_inspected": self.images_inspected,
            "images_optimized": self.images_optimized,
            "opaque_smasks_stripped": self.opaque_smasks_stripped,
            "images_downsampled": self.images_downsampled,
            "images_indexed": self.images_indexed,
            "duplicate_images_merged": self.duplicate_images_merged,
            "fonts_optimized": self.font_streams_optimized,
            "cmaps_optimized": self.cmaps_optimized,
            "signatures_flattened": self.signatures_flattened,
            "was_encrypted": self.was_encrypted,
            "protection_kind": self.protection_kind,
            "protection_removed": self.protection_removed,
            "required_password": self.required_password,
            "kept_original_guardrail": self.kept_original_guardrail,
            "already_optimal": self.already_optimal,
            "signed": self.signed,
            "output_mode": self.output_mode,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def compress_pdf_file(
    input_path: Path | str,
    output_path: Path | str,
    config: CompressionConfig | None = None,
) -> FileCompressionResult:
    """Compress one PDF, never raising: failures come back as a result.

    Args:
        input_path: Source document.
        output_path: Destination, which may equal *input_path* for overwrite mode.
        config: Knobs; defaults to the balanced preset.

    Returns:
        A :class:`FileCompressionResult` describing the outcome.
    """
    started = time.perf_counter()
    cfg = (config or CompressionConfig()).copy_with()
    source = Path(input_path).expanduser()
    target = Path(output_path).expanduser()
    result = FileCompressionResult(
        input_path=str(source),
        output_path=str(target),
        success=False,
        output_mode=cfg.output_mode.value,
    )

    if not source.is_file():
        result.error_message = t("errors.input_missing")
        result.elapsed_seconds = time.perf_counter() - started
        return result

    try:
        result.original_bytes = source.stat().st_size
    except OSError as exc:
        result.error_message = str(exc)
        result.elapsed_seconds = time.perf_counter() - started
        return result

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error_message = t("errors.output_unwritable", reason=str(exc))
        result.elapsed_seconds = time.perf_counter() - started
        return result

    temp: Path | None = None
    pdf: pikepdf.Pdf | None = None
    writing_output = False
    try:
        # Stage 1 — unlock. Re-signing also requires removing the old signature.
        flatten = cfg.unlock_and_flatten_signatures or cfg.sign_after_slimming
        pdf, unlock = open_and_unlock_pdf(source, cfg.password, flatten)
        result.page_count = len(pdf.pages)
        result.was_encrypted = unlock.was_encrypted
        result.signatures_flattened = unlock.signatures_flattened
        result.protection_kind = unlock.protection_kind
        result.protection_removed = unlock.protection_removed
        result.required_password = unlock.required_password

        # Stage 2 — audit + effective DPI.
        result.before_breakdown = audit_pdf_bytes(pdf, result.original_bytes)
        dpis = calculate_effective_image_dpis(pdf)

        # Stage 3 — deduplicate before expensive re-encoding.
        dedup = DeduplicationStats()
        if cfg.deduplicate_resources:
            dedup = deduplicate_resources(pdf)

        # Stage 4 — images.
        images: ImageOptimizationStats = optimize_all_images(pdf, dpis, cfg)

        # Stage 5a — fonts.
        fonts = FontOptimizationStats()
        if cfg.subset_fonts:
            fonts = optimize_fonts(pdf, cfg)

        # Stage 5b — structural bloat and deflate recompression.
        streams = StreamOptimizationStats()
        strip_structural_bloat(pdf, cfg, streams)
        recompress_content_streams(pdf, cfg, streams)

        # Stage 6 — drop unreferenced objects, then save atomically.
        try:
            pdf.remove_unreferenced_resources()
        except Exception:
            pass

        writing_output = True
        temp = _temp_path(target.parent)
        _with_lock_retry(
            pdf.save,
            temp,
            object_stream_mode=(
                pikepdf.ObjectStreamMode.generate
                if cfg.use_object_streams
                else pikepdf.ObjectStreamMode.preserve
            ),
            compress_streams=True,
            stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
            recompress_flate=False,  # keep the denser Zopfli streams
            # Outputs are always plain PDFs: owner restrictions and open-password
            # encryption are removed at this point (FR-UNL-4).
            encryption=False,
        )
        pdf.close()
        pdf = None

        result.images_inspected = images.images_inspected
        result.images_optimized = images.images_optimized
        result.opaque_smasks_stripped = images.opaque_smasks_stripped
        result.images_downsampled = images.images_downsampled
        result.images_indexed = images.images_indexed
        result.duplicate_images_merged = dedup.duplicate_images_merged
        result.font_streams_optimized = fonts.font_streams_optimized
        result.cmaps_optimized = fonts.cmaps_optimized

        # Guardrail — never ship a larger file unless the user opted into an
        # unlock/flatten transformation (which necessarily changes the layout).
        new_size = temp.stat().st_size
        was_unlocked = unlock.was_encrypted or unlock.signatures_flattened > 0
        if new_size >= result.original_bytes and not was_unlocked:
            temp.unlink()
            shutil.copyfile(source, temp)
            result.kept_original_guardrail = True

        # Signature — the last write, because any later edit invalidates it.
        if cfg.sign_after_slimming:
            sign_pdf_file(temp, cfg)
            result.signed = True

        result.after_breakdown = (
            result.before_breakdown
            if result.kept_original_guardrail
            else _audit_written_file(temp, result.before_breakdown)
        )
        _with_lock_retry(os.replace, temp, target)
        temp = None
        result.compressed_bytes = target.stat().st_size
        result.already_optimal = (
            result.kept_original_guardrail and result.bytes_saved == 0
        )
        result.success = True
        log.debug(
            "slimmed %s: %s -> %s (%s)",
            source.name,
            format_bytes(result.original_bytes),
            format_bytes(result.compressed_bytes),
            format_percent(result.reduction_percent),
        )
    except PasswordRequiredError:
        result.protection_kind = "password"
        result.required_password = True
        result.error_message = t("errors.password_required")
    except UnsupportedProtectionError:
        result.protection_kind = "unsupported"
        result.required_password = True
        result.error_message = t("errors.protection_unsupported")
    except PermissionError as exc:
        # Once the output is being written, a denial is a destination-folder
        # problem, unless it names the input itself. The OS message names
        # whichever handle failed — pikepdf's temp, ours, or the target — none
        # of which the user can act on, so report the folder instead.
        blocked = getattr(exc, "filename", None) or ""
        locked_input = bool(blocked) and Path(blocked) == Path(source)
        if writing_output and not locked_input:
            result.error_message = t(
                "errors.output_denied",
                folder=str(target.parent),
                reason=exc.strerror or exc.__class__.__name__,
            )
        else:
            result.error_message = t("errors.permission_denied", path=blocked or str(source))
    except SigningError as exc:
        result.error_message = f"{t('errors.signing_failed')}: {exc}"
    except Exception as exc:  # FR-PIPE-3: never leak an exception to callers.
        log.debug("failed %s", source, exc_info=True)
        result.error_message = str(exc) or exc.__class__.__name__
    finally:
        if pdf is not None:
            try:
                pdf.close()
            except Exception:
                pass
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
        result.elapsed_seconds = time.perf_counter() - started
    return result


def _temp_path(directory: Path) -> Path:
    """Create a same-volume temporary path so the final replace is atomic.

    Creation is retried briefly: a cloud folder that is syncing that moment can
    deny the name and accept it a fraction of a second later.
    """

    def create() -> Path:
        handle, name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".tmp.pdf", dir=directory)
        os.close(handle)
        return Path(name)

    return _with_lock_retry(create)


def _audit_written_file(path: Path, fallback: CategoryBreakdown) -> CategoryBreakdown:
    """Audit the saved output; fall back to the input breakdown on failure."""
    try:
        size = path.stat().st_size
        with pikepdf.open(path) as pdf:
            return audit_pdf_bytes(pdf, size)
    except Exception:
        return fallback
