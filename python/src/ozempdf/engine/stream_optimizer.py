"""Structural bloat stripping and lossless stream recompression (Stage 3).

Two independent passes over an open PDF:

* :func:`strip_structural_bloat` removes editing history that no reader needs
  (``/PieceInfo``, page thumbnails, optionally XMP metadata and document info).
* :func:`recompress_content_streams` re-encodes already-Flate or uncompressed
  streams with Zopfli.  Decoded bytes never change, so text, vectors, ICC
  profiles and embedded files stay byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

import pikepdf

from ozempdf.config import CompressionConfig
from ozempdf.engine.unlock import zopfli_compress

# Content streams shorter than this are not worth a recompression attempt.
_MIN_DECODED_SIZE = 32


@dataclass(slots=True)
class StreamOptimizationStats:
    """Observable effects of the stream optimisation passes.

    Attributes:
        pieceinfo_removed: ``/PieceInfo`` entries deleted.
        thumbnails_removed: Page ``/Thumb`` images deleted.
        metadata_removed: XMP ``/Metadata`` streams deleted from the catalog.
        streams_recompressed: Streams replaced with a strictly smaller encoding.
        bytes_saved: Raw encoded bytes saved across thumbnails, metadata and
            recompressed streams.
    """

    pieceinfo_removed: int = 0
    thumbnails_removed: int = 0
    metadata_removed: int = 0
    streams_recompressed: int = 0
    bytes_saved: int = 0

    @property
    def piece_info_removed(self) -> int:
        """Alias for :attr:`pieceinfo_removed` used by report surfaces."""
        return self.pieceinfo_removed


def strip_structural_bloat(
    pdf: pikepdf.Pdf,
    config: CompressionConfig,
    stats: StreamOptimizationStats,
) -> None:
    """Delete structural leftovers that only inflate the file.

    With ``config.strip_bloat`` every ``/PieceInfo`` (catalog, pages, or any
    dictionary/stream) and every page ``/Thumb`` is removed.  With
    ``config.strip_metadata`` the catalog ``/Metadata`` XMP stream and every
    ``pdf.docinfo`` key are removed.  Outlines, link annotations and AcroForm
    fields are never touched.

    Args:
        pdf: Open document, modified in place.
        config: Run configuration; ``strip_bloat`` and ``strip_metadata`` gate
            the two halves of the pass.
        stats: Mutable counters updated in place.
    """
    if config.strip_bloat:
        _strip_piece_info(pdf, stats)
        _strip_thumbnails(pdf, stats)
    if config.strip_metadata:
        _strip_root_metadata(pdf, stats)
        _clear_docinfo(pdf)


def recompress_content_streams(
    pdf: pikepdf.Pdf,
    config: CompressionConfig,
    stats: StreamOptimizationStats,
) -> None:
    """Re-encode eligible streams with Zopfli when that makes them smaller.

    A stream is eligible when it is not an image, has no ``/DecodeParms``
    (predictors and parameters would be invalidated), and its ``/Filter`` is
    either absent or exactly ``/FlateDecode``.  Decoded payloads under 32 bytes
    are skipped.  The stream is rewritten only when the new encoding is
    strictly smaller than the current raw encoding; ``/Filter`` is then set to
    ``/FlateDecode``.  ``bytes_saved`` counts the raw-length delta.

    Args:
        pdf: Open document, modified in place.
        config: Run configuration; ``zopfli_iterations`` tunes the encoder.
        stats: Mutable counters updated in place.
    """
    iterations = config.zopfli_iterations
    for obj in list(pdf.objects):
        if not isinstance(obj, pikepdf.Stream):
            continue
        try:
            subtype = obj.get("/Subtype")
            if subtype == pikepdf.Name("/Image"):
                # Raster images are Stage 4's business (JPEG/DPI handling).
                continue
            if "/DecodeParms" in obj:
                # Predictors and similar parameters would no longer match the
                # rewritten payload.
                continue
            filter_name = obj.get("/Filter")
            if filter_name is not None and filter_name != pikepdf.Name("/FlateDecode"):
                # Unsupported or chained filters: leave the bytes untouched.
                continue

            decoded = obj.read_bytes()
            if len(decoded) < _MIN_DECODED_SIZE:
                continue
            raw_length = len(obj.read_raw_bytes())
            compressed = zopfli_compress(decoded, iterations)
            if len(compressed) >= raw_length:
                continue

            obj.write(compressed)
            # write() stores raw bytes and clears the filter, so label the new
            # zlib-wrapped payload explicitly.
            obj["/Filter"] = pikepdf.Name("/FlateDecode")
            if "/DecodeParms" in obj:
                del obj["/DecodeParms"]
            stats.streams_recompressed += 1
            stats.bytes_saved += raw_length - len(compressed)
        except Exception:
            # A malformed stream must not abort the run; leave it as-is.
            continue


def _strip_piece_info(pdf: pikepdf.Pdf, stats: StreamOptimizationStats) -> None:
    """Delete ``/PieceInfo`` from every dictionary and stream.

    ``/PieceInfo`` records the editing application's private data (often a
    preview illustration); removing it is lossless for rendering.

    Args:
        pdf: Open document, modified in place.
        stats: Mutable counters updated in place.
    """
    for obj in list(pdf.objects):
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            continue
        try:
            if "/PieceInfo" in obj:
                del obj["/PieceInfo"]
                stats.pieceinfo_removed += 1
        except Exception:
            continue
    # The catalog may be reachable only through the trailer; cover it and the
    # page dictionaries explicitly in case their entries are direct.
    try:
        if "/PieceInfo" in pdf.Root:
            del pdf.Root["/PieceInfo"]
            stats.pieceinfo_removed += 1
    except Exception:
        pass
    for page in pdf.pages:
        try:
            if "/PieceInfo" in page.obj:
                del page.obj["/PieceInfo"]
                stats.pieceinfo_removed += 1
        except Exception:
            continue


def _strip_thumbnails(pdf: pikepdf.Pdf, stats: StreamOptimizationStats) -> None:
    """Delete page ``/Thumb`` preview streams and credit their raw bytes.

    Args:
        pdf: Open document, modified in place.
        stats: Mutable counters updated in place.
    """
    for page in pdf.pages:
        try:
            thumbnail = page.obj.get("/Thumb")
            if thumbnail is None:
                continue
            stats.bytes_saved += _raw_length(thumbnail)
            del page.obj["/Thumb"]
            stats.thumbnails_removed += 1
        except Exception:
            continue


def _strip_root_metadata(pdf: pikepdf.Pdf, stats: StreamOptimizationStats) -> None:
    """Delete the catalog ``/Metadata`` XMP stream and credit its raw bytes.

    Args:
        pdf: Open document, modified in place.
        stats: Mutable counters updated in place.
    """
    try:
        metadata = pdf.Root.get("/Metadata")
        if metadata is None:
            return
        stats.bytes_saved += _raw_length(metadata)
        del pdf.Root["/Metadata"]
        stats.metadata_removed += 1
    except Exception:
        pass


def _clear_docinfo(pdf: pikepdf.Pdf) -> None:
    """Delete every key from the document information dictionary.

    Args:
        pdf: Open document, modified in place.
    """
    try:
        docinfo = pdf.docinfo
        for key in list(docinfo.keys()):
            try:
                del docinfo[key]
            except Exception:
                continue
    except Exception:
        pass


def _raw_length(obj: pikepdf.Object) -> int:
    """Return the encoded byte length of a stream, or 0 when unreadable.

    Args:
        obj: Candidate stream (may be a non-stream object).

    Returns:
        Length of the raw encoded bytes, falling back to the decoded length.
    """
    try:
        return len(obj.read_raw_bytes())
    except Exception:
        try:
            return len(obj.read_bytes())
        except Exception:
            return 0
