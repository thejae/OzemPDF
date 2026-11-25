"""Public engine facade.

Import from here rather than from the individual stage modules where possible;
the facade is the surface the CLI, the HTTP API and the tests share.
"""

from ..config import CompressionConfig, OutputMode, PresetMode
from ..formatting import format_bytes, format_percent
from .analyzer import CategoryBreakdown, audit_pdf_bytes, calculate_effective_image_dpis
from .batch import (
    BatchCancelledError,
    BatchSummary,
    discover_pdf_files,
    inspect_pdf_quick_metadata,
    render_page_png,
    render_page_thumbnail_png,
    resolve_output_path,
    run_batch_compression,
)
from .deduplicator import DeduplicationStats, deduplicate_resources
from .font_optimizer import FontOptimizationStats, optimize_fonts
from .image_optimizer import ImageOptimizationStats, optimize_all_images
from .pipeline import FileCompressionResult, compress_pdf_file
from .signing import SigningError, sign_pdf_file
from .stream_optimizer import StreamOptimizationStats, recompress_content_streams, strip_structural_bloat
from .unlock import (
    PasswordRequiredError,
    ProtectionInfo,
    ProtectionKind,
    UnlockStats,
    UnsupportedProtectionError,
    flatten_and_strip_signatures,
    inspect_protection,
    open_and_unlock_pdf,
    zopfli_compress,
)

__all__ = [
    "BatchCancelledError",
    "BatchSummary",
    "CategoryBreakdown",
    "CompressionConfig",
    "DeduplicationStats",
    "FileCompressionResult",
    "FontOptimizationStats",
    "ImageOptimizationStats",
    "OutputMode",
    "PasswordRequiredError",
    "PresetMode",
    "ProtectionInfo",
    "ProtectionKind",
    "SigningError",
    "StreamOptimizationStats",
    "UnlockStats",
    "UnsupportedProtectionError",
    "audit_pdf_bytes",
    "calculate_effective_image_dpis",
    "compress_pdf_file",
    "deduplicate_resources",
    "discover_pdf_files",
    "flatten_and_strip_signatures",
    "format_bytes",
    "format_percent",
    "inspect_pdf_quick_metadata",
    "inspect_protection",
    "open_and_unlock_pdf",
    "optimize_all_images",
    "optimize_fonts",
    "recompress_content_streams",
    "render_page_png",
    "render_page_thumbnail_png",
    "resolve_output_path",
    "run_batch_compression",
    "sign_pdf_file",
    "strip_structural_bloat",
    "zopfli_compress",
]
