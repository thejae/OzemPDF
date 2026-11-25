"""Batch engine: discovery, output mapping, thumbnails and parallel runs."""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from ..config import CompressionConfig, OutputMode
from ..logging_setup import get_logger
from .analyzer import CategoryBreakdown
from .pipeline import FileCompressionResult, compress_pdf_file

log = get_logger()

DEFAULT_SUFFIX = "_compressed"
TEMP_PREFIX = ".ozempdf_"

# PDFium is not thread-safe (pypdfium2 releases the GIL around every call). The
# inspector requests its original and slimmed halves on two connections at once
# and the thumbnail pool renders in parallel, so two renders can overlap and
# corrupt PDFium's global state — after which *every* later page render fails
# and the inspector stays dark until the app restarts (N-13). One lock around
# every PDFium call is the whole fix.
_PDFIUM_LOCK = threading.Lock()

# Returned by _render_with_pdfium when PDFium refused to load the document, as
# opposed to loading it and finding no such page. Only the former is worth
# retrying from a repaired copy.
_OPEN_FAILED = object()


class BatchCancelledError(Exception):
    """Raised from a progress callback to stop dispatching remaining files."""


@dataclass(slots=True)
class BatchSummary:
    """Aggregate of one batch run.

    Byte totals count successful files only; ``total_files`` counts every file.
    """

    results: list[FileCompressionResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def total_files(self) -> int:
        return len(self.results)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for result in self.results if result.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if not result.success)

    @property
    def already_optimal_count(self) -> int:
        return sum(1 for result in self.results if result.already_optimal)

    @property
    def signed_count(self) -> int:
        return sum(1 for result in self.results if result.signed)

    @property
    def protection_removed_count(self) -> int:
        """Documents whose permission-only protection was removed for free."""
        return sum(1 for result in self.results if result.protection_removed)

    @property
    def _successful(self) -> list[FileCompressionResult]:
        return [result for result in self.results if result.success]

    @property
    def total_original_bytes(self) -> int:
        return sum(result.original_bytes for result in self._successful)

    @property
    def total_compressed_bytes(self) -> int:
        return sum(result.compressed_bytes for result in self._successful)

    @property
    def total_bytes_saved(self) -> int:
        return max(0, self.total_original_bytes - self.total_compressed_bytes)

    @property
    def total_reduction_percent(self) -> float:
        if self.total_original_bytes <= 0:
            return 0.0
        return round(self.total_bytes_saved / self.total_original_bytes * 100, 2)

    @property
    def aggregate_before_breakdown(self) -> CategoryBreakdown:
        total = CategoryBreakdown.empty()
        for result in self._successful:
            total.add_inplace(result.before_breakdown)
        return total

    @property
    def aggregate_after_breakdown(self) -> CategoryBreakdown:
        total = CategoryBreakdown.empty()
        for result in self._successful:
            total.add_inplace(result.after_breakdown)
        return total

    def to_dict(self) -> dict:
        """JSON shape documented in Appendix B.2."""
        return {
            "total_files": self.total_files,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "already_optimal_count": self.already_optimal_count,
            "signed_count": self.signed_count,
            "protection_removed_count": self.protection_removed_count,
            "total_original_bytes": self.total_original_bytes,
            "total_compressed_bytes": self.total_compressed_bytes,
            "total_bytes_saved": self.total_bytes_saved,
            "total_reduction_percent": self.total_reduction_percent,
            "aggregate_before_breakdown": self.aggregate_before_breakdown.to_dict(),
            "aggregate_after_breakdown": self.aggregate_after_breakdown.to_dict(),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "files": [result.to_dict() for result in self.results],
        }


def discover_pdf_files(
    paths: Iterable[Path | str], *, exclude_suffix: str = ""
) -> list[tuple[Path, Path | None]]:
    """Discover PDFs from files and/or folders.

    Directories are walked recursively in a stable sorted order. During folder
    scans, files whose stem already ends with *exclude_suffix* are skipped so a
    re-run does not re-queue its own outputs. Explicit files are never filtered.
    """
    suffix = (exclude_suffix or "").strip()
    found: list[tuple[Path, Path | None]] = []
    seen: set[Path] = set()

    for raw in paths:
        if isinstance(raw, (str, os.PathLike)):
            entry = Path(raw).expanduser()
        else:
            continue
        if entry.is_file():
            _append_unique(found, seen, entry, None)
            continue
        if not entry.is_dir():
            continue
        base = entry.resolve()
        for root, dirs, files in os.walk(base):
            dirs.sort()
            files.sort()
            for name in files:
                candidate = Path(root) / name
                if not _is_pdf_name(name):
                    continue
                if suffix and candidate.stem.endswith(suffix):
                    continue
                _append_unique(found, seen, candidate, base)
    return found


def _is_pdf_name(name: str) -> bool:
    """True for a PDF that is not one of our temporary files."""
    return name.lower().endswith(".pdf") and not name.startswith(TEMP_PREFIX)


def _append_unique(
    found: list[tuple[Path, Path | None]],
    seen: set[Path],
    path: Path,
    base: Path | None,
) -> None:
    """Add a resolved path once, preserving first-seen order."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    if resolved in seen or not resolved.is_file():
        return
    seen.add(resolved)
    found.append((resolved, base))


def resolve_output_path(
    input_path: Path, config: CompressionConfig, base_folder: Path | None = None
) -> Path:
    """Map one input path to its output path (spec §3.8)."""
    source = Path(input_path).expanduser().resolve()
    if config.output_mode is OutputMode.OVERWRITE:
        return source
    if config.output_mode is OutputMode.CUSTOM_FOLDER:
        output_dir = Path(config.output_dir).expanduser() if config.output_dir else None
        if output_dir is None:
            return _suffix_path(source, config.output_suffix)
        if base_folder is not None:
            try:
                relative = source.relative_to(Path(base_folder).expanduser().resolve())
                return output_dir / relative
            except ValueError:
                pass
        return output_dir / source.name
    return _suffix_path(source, config.output_suffix)


def _suffix_path(source: Path, suffix: str) -> Path:
    """``<stem><suffix><ext>`` beside the original; suffix never empty."""
    clean = (suffix or "").strip() or DEFAULT_SUFFIX
    return source.with_name(f"{source.stem}{clean}{source.suffix}")


def _unique_outputs(pairs: Sequence[tuple[Path, Path]]) -> list[Path]:
    """Disambiguate colliding output paths (defect D-18).

    Two inputs from different roots can map to one name when no base folder is
    known. The later file gets ``-2``, ``-3`` … before the extension instead of
    silently overwriting the earlier result.
    """
    used: set[str] = set()
    resolved: list[Path] = []
    for _source, output in pairs:
        candidate = output
        counter = 1
        key = _path_key(candidate)
        while key in used:
            counter += 1
            candidate = output.with_name(f"{output.stem}-{counter}{output.suffix}")
            key = _path_key(candidate)
        used.add(key)
        resolved.append(candidate)
    return resolved


def _path_key(path: Path) -> str:
    """Case-insensitive identity on Windows, case-sensitive elsewhere."""
    text = str(path)
    return text.casefold() if os.name == "nt" else text


def inspect_pdf_quick_metadata(path: Path, password: str = "") -> tuple[int, int]:
    """Return ``(page_count, size)`` quickly; page count is 0 when unknown."""
    try:
        size = Path(path).stat().st_size
    except OSError:
        return (0, 0)
    try:
        with pikepdf.open(path, password=password or "") as pdf:
            return (len(pdf.pages), size)
    except Exception:
        return (0, size)


def render_page_thumbnail_png(
    path: Path, page_index: int = 0, max_dimension: int = 112, password: str = ""
) -> bytes | None:
    """Render page 1 (or *page_index*) to PNG bytes, or None on failure."""
    return render_page_png(path, page_index=page_index, max_dimension=max_dimension, password=password)


def render_page_png(
    path: Path,
    page_index: int = 0,
    *,
    max_dimension: int | None = None,
    scale: float | None = None,
    zoom_percent: float | None = None,
    password: str = "",
) -> bytes | None:
    """Rasterise one page to PNG bytes with pypdfium2.

    Either *scale* is used directly, *zoom_percent* maps 200 % to 1.15× and is
    capped so a huge page cannot stall the inspector, or *max_dimension* picks a
    scale clamped to ``[0.15, 2.0]`` for a thumbnail.

    Renders are serialized because PDFium is not thread-safe. When PDFium
    refuses a document that qpdf can still reconstruct — PDFium demands an
    intact cross-reference table, qpdf scans and rebuilds one — the page is
    rendered from a repaired in-memory copy. Nothing is written to disk.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    target = Path(path)
    with _PDFIUM_LOCK:
        data = _render_with_pdfium(
            pdfium, str(target), page_index, max_dimension, scale, zoom_percent, password
        )
        if data is not _OPEN_FAILED:
            return data
        repaired = _repaired_pdf_bytes(target, password)
        if repaired is None:
            log.debug("page render failed for %s (page %s)", target, page_index + 1)
            return None
        return _render_with_pdfium(pdfium, repaired, page_index, max_dimension, scale, zoom_percent, "")


def _render_with_pdfium(
    pdfium: Any,
    source: str | bytes,
    page_index: int,
    max_dimension: int | None,
    scale: float | None,
    zoom_percent: float | None,
    password: str,
) -> bytes | None | object:
    """Render one page from a path or PDF byte buffer.

    Returns PNG bytes, ``None`` when the document loaded but the page or its
    raster failed, or :data:`_OPEN_FAILED` when the document itself would not
    load (which a repaired copy may still fix).
    """
    document = None
    try:
        if isinstance(source, bytes):
            # pypdfium2 keeps the buffer alive for the document's lifetime.
            document = pdfium.PdfDocument(source)
        else:
            document = pdfium.PdfDocument(source, password=password or None)
    except Exception as exc:
        log.debug("pdfium refused %s: %s", source if isinstance(source, str) else "<repaired>", exc)
        return _OPEN_FAILED
    try:
        if page_index < 0 or page_index >= len(document):
            return None
        page = document[page_index]
        width_pt, height_pt = page.get_size()
        longest = max(width_pt, height_pt, 1.0)
        if zoom_percent is not None:
            scale = max(0.5, min(3400.0 / longest, 1.15 * (float(zoom_percent) / 200.0)))
        elif scale is None:
            target = max_dimension or 112
            scale = min(2.0, max(0.15, target / longest))
        bitmap = page.render(scale=float(scale))
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        # The default compression level is ~5x faster than optimize=True at
        # inspector sizes and produced the same bytes for page rasters (the
        # inspector fetches these over loopback, so a few hundred KB do not
        # matter), and zoom steps are latency, not size, limited.
        image.save(buffer, format="PNG", compress_level=6)
        return buffer.getvalue()
    except Exception as exc:
        log.debug("page raster failed: %s", exc)
        return None
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _repaired_pdf_bytes(path: Path, password: str) -> bytes | None:
    """Re-save *path* through qpdf so a strict renderer can open it.

    PDFium rejects documents that have lost their cross-reference table even
    when every object is intact (a truncated or badly finalised file); qpdf
    scans the objects and rebuilds the table. The repaired bytes exist only for
    the lifetime of the render.
    """
    buffer = io.BytesIO()
    try:
        try:
            pdf = pikepdf.open(path)
        except pikepdf.PasswordError:
            if not password:
                return None
            pdf = pikepdf.open(path, password=password)
        with pdf:
            pdf.save(buffer, object_stream_mode=pikepdf.ObjectStreamMode.generate)
        return buffer.getvalue()
    except Exception as exc:
        log.debug("could not repair %s for preview: %s", path, exc)
        return None


def _worker_compress_task(task: tuple[str, str, dict]) -> FileCompressionResult:
    """Module-level picklable worker for the process pool (FR-BAT-8)."""
    input_path, output_path, config_data = task
    return compress_pdf_file(input_path, output_path, CompressionConfig.from_dict(config_data))


def run_batch_compression(
    items: Sequence[tuple[Path, Path | None] | Path],
    config: CompressionConfig,
    progress_callback: Callable[[int, int, FileCompressionResult], None] | None = None,
) -> BatchSummary:
    """Compress a batch, emitting one callback per completed file.

    Args:
        items: ``Path`` or ``(path, base_folder)`` pairs.
        config: Frozen knobs for the run.
        progress_callback: Called ``(completed, total, result)`` in completion
            order. Raising :class:`BatchCancelledError` stops dispatch.

    Returns:
        A summary whose ``results`` are ordered by input index.
    """
    import time

    started = time.perf_counter()
    cfg = config.copy_with()
    pairs: list[tuple[Path, Path]] = []
    for item in items:
        if isinstance(item, tuple):
            path, base = item
        else:
            path, base = item, None
        pairs.append((Path(path), resolve_output_path(Path(path), cfg, base)))
    outputs = _unique_outputs(pairs)
    tasks = [
        (str(source), str(output), cfg.to_dict(public=False))
        for (source, _), output in zip(pairs, outputs, strict=True)
    ]
    total = len(tasks)
    summary = BatchSummary()
    if total == 0:
        summary.elapsed_seconds = 0.0
        return summary

    workers = cfg.max_workers or min(total, os.cpu_count() or 1)
    ordered: list[FileCompressionResult | None] = [None] * total

    def emit(index: int, result: FileCompressionResult) -> None:
        ordered[index] = result
        summary.results.append(result)
        if progress_callback is not None:
            progress_callback(len(summary.results), total, result)

    if total == 1 or workers <= 1:
        try:
            for index, task in enumerate(tasks):
                emit(index, _worker_compress_task(task))
        except BatchCancelledError:
            # Completed files keep their results; unstarted files never run.
            pass
        summary.results = [r for r in ordered if r is not None]
        summary.elapsed_seconds = time.perf_counter() - started
        return summary

    # Dispatch lazily: only `workers` files are ever in flight, so stopping the
    # batch prevents unstarted files from being processed (US-019).
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending: dict = {}
        next_index = 0
        try:
            while next_index < total or pending:
                while next_index < total and len(pending) < workers:
                    future = pool.submit(_worker_compress_task, tasks[next_index])
                    pending[future] = next_index
                    next_index += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:  # FR-BAT-9
                        result = FileCompressionResult(
                            input_path=str(tasks[index][0]),
                            output_path=str(outputs[index]),
                            success=False,
                            error_message=str(exc) or exc.__class__.__name__,
                        )
                    emit(index, result)
        except BatchCancelledError:
            for future in pending:
                future.cancel()

    summary.results = [r for r in ordered if r is not None]
    summary.elapsed_seconds = time.perf_counter() - started
    log.debug("batch of %d finished in %.2fs", total, summary.elapsed_seconds)
    return summary
