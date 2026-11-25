"""Image stream optimisation.

Two modes:

* **Mode A — Strict Lossless** (:attr:`CompressionConfig.strict_lossless`): only
  transforms that leave the decoded pixels bit-identical.
* **Mode B — Visually Lossless / Max Slim**: downsamples above an effective-DPI
  trigger, then offers palette, progressive JPEG and deflate candidates.

Every mutation is gated by a strict decrease in encoded size (``INV-2``).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pikepdf
from PIL import Image

from ..config import CompressionConfig
from .unlock import zopfli_compress

# Predictor work is pure Python; these caps bound the cost per image (NFR-2).
_PREDICTOR_MAX_PIXELS = 4_000_000
_CANDIDATE_DEFLATE_MAX_PIXELS = 1_500_000
_MIN_IMAGE_BYTES = 64
_THUMBNAIL_MAX = 160
_UI_FLAT_RUN_RATIO = 0.38

UI_GRAPHIC = "ui"
PHOTOGRAPH = "photo"
MONOCHROME = "mono"

# A candidate is (cost in bytes, payload, /Filter, /ColorSpace override, /DecodeParms).
_Candidate = tuple[int, bytes, str, Any, pikepdf.Dictionary | None]


@dataclass(slots=True)
class ImageOptimizationStats:
    """Per-file counters for Stage 4."""

    images_inspected: int = 0
    images_optimized: int = 0
    opaque_smasks_stripped: int = 0
    images_downsampled: int = 0
    images_indexed: int = 0
    bytes_saved: int = 0


def optimize_all_images(
    pdf: pikepdf.Pdf,
    effective_dpis: dict[tuple[int, int], float] | None,
    config: CompressionConfig,
) -> ImageOptimizationStats:
    """Optimise every unique ``/Subtype /Image`` stream in *pdf*.

    Args:
        pdf: Open document, mutated in place.
        effective_dpis: ``{objgen: max DPI}`` from the CTM analysis.
        config: Compression knobs.

    Returns:
        Counters and bytes saved for the file report.
    """
    stats = ImageOptimizationStats()
    seen: set[tuple[int, int]] = set()
    dpis = effective_dpis or {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream):
            continue
        try:
            if obj.get("/Subtype") != "/Image":
                continue
        except Exception:
            continue
        key = obj.objgen
        if key in seen:
            continue
        seen.add(key)
        try:
            optimize_single_image(obj, dpis.get(key), config, stats)
        except Exception:
            # One broken raster must never sink the file.
            continue
    return stats


def optimize_single_image(
    image: pikepdf.Object,
    effective_dpi: float | None,
    config: CompressionConfig,
    stats: ImageOptimizationStats,
) -> None:
    """Optimise one image stream in place, updating *stats*."""
    stats.images_inspected += 1
    if image.get("/ImageMask"):
        return

    has_active_smask = False
    smask = image.get("/SMask")
    if isinstance(smask, pikepdf.Stream):
        opaque = _smask_is_fully_opaque(smask)
        if opaque:
            try:
                removed = len(smask.read_raw_bytes())
            except Exception:
                removed = 0
            try:
                del image.stream_dict["/SMask"]
            except Exception:
                pass
            stats.opaque_smasks_stripped += 1
            stats.bytes_saved += removed
        else:
            _optimize_smask_stream(smask, config)
            has_active_smask = True
    if image.get("/Mask") is not None:
        has_active_smask = True

    try:
        original_raw = image.read_raw_bytes()
    except Exception:
        return
    if len(original_raw) < _MIN_IMAGE_BYTES:
        return

    is_dct = _is_dct(image)
    if config.strict_lossless:
        _optimize_lossless(image, original_raw, is_dct, stats)
    else:
        _optimize_perceptual(
            image, original_raw, effective_dpi, config, stats, has_active_smask
        )


# --------------------------------------------------------------------------- #
# Mode A — strict lossless
# --------------------------------------------------------------------------- #


def _optimize_lossless(
    image: pikepdf.Object,
    original_raw: bytes,
    is_dct: bool,
    stats: ImageOptimizationStats,
) -> None:
    """Apply only bit-identical transforms."""
    if is_dct:
        stripped = strip_jpeg_bloat_lossless(original_raw)
        if len(stripped) < len(original_raw):
            _apply_stream_update(image, stripped, filter_="/DCTDecode")
            stats.bytes_saved += len(original_raw) - len(stripped)
            stats.images_optimized += 1
        return

    width = int(image.get("/Width", 0) or 0)
    height = int(image.get("/Height", 0) or 0)
    bpc = int(image.get("/BitsPerComponent", 8) or 8)
    if width <= 0 or height <= 0:
        return

    if bpc == 1:
        payload = zopfli_compress(original_raw, 15)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=width, height=height, bpc=1,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return

    try:
        pil = pikepdf.PdfImage(image).as_pil_image()
    except Exception:
        return
    mode = pil.mode
    if mode not in {"RGB", "L", "P", "1"}:
        # CMYK / ICCBased stay verbatim: converting them would round colours.
        return
    if mode == "1":
        payload = zopfli_compress(pil.tobytes(), 15)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=width, height=height, bpc=1,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return
    if mode == "P":
        pil = pil.convert("RGB")
        mode = "RGB"

    if mode == "L":
        raw = pil.tobytes()
        payload, parms = _smallest_flate_candidate(raw, width, height, bpp=1)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=width, height=height, bpc=8, decode_parms=parms,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return

    raw = pil.tobytes()
    if width * height <= _PREDICTOR_MAX_PIXELS and raw[0::3] == raw[1::3] == raw[2::3]:
        gray = raw[0::3]
        payload, parms = _smallest_flate_candidate(gray, width, height, bpp=1)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=width, height=height, bpc=8, decode_parms=parms,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return

    indexed = _exact_palette_candidate(pil, max_colors=256)
    if indexed is not None:
        indices, palette = indexed
        payload, parms = _smallest_flate_candidate(indices, width, height, bpp=1)
        extra = len(palette)
        if len(payload) + extra < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode",
                colorspace=_indexed_colorspace(palette),
                width=width, height=height, bpc=8, decode_parms=parms,
            )
            stats.bytes_saved += len(original_raw) - len(payload) - extra
            stats.images_optimized += 1
            stats.images_indexed += 1
            return

    payload, parms = _smallest_flate_candidate(raw, width, height, bpp=3)
    if len(payload) < len(original_raw):
        _apply_stream_update(
            image, payload, filter_="/FlateDecode", colorspace="/DeviceRGB",
            width=width, height=height, bpc=8, decode_parms=parms,
        )
        stats.bytes_saved += len(original_raw) - len(payload)
        stats.images_optimized += 1


# --------------------------------------------------------------------------- #
# Mode B — visually lossless / max slim
# --------------------------------------------------------------------------- #


def _optimize_perceptual(
    image: pikepdf.Object,
    original_raw: bytes,
    effective_dpi: float | None,
    config: CompressionConfig,
    stats: ImageOptimizationStats,
    has_active_smask: bool,
) -> None:
    """Downsample and re-encode within the preset's perceptual budget."""
    width = int(image.get("/Width", 0) or 0)
    height = int(image.get("/Height", 0) or 0)
    bpc = int(image.get("/BitsPerComponent", 8) or 8)
    if width <= 0 or height <= 0:
        return

    if bpc == 1:
        payload = zopfli_compress(original_raw, config.zopfli_iterations)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=width, height=height, bpc=1,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return

    try:
        pil = pikepdf.PdfImage(image).as_pil_image()
    except Exception:
        return
    if pil.mode != "1":
        pil = _normalise_pil(pil)

    if config.enable_downsampling and config.target_dpi < 600:
        dpi = effective_dpi
        if dpi is None and max(pil.width, pil.height) > 1600:
            dpi = max(pil.width, pil.height) / 6.0
        if dpi is not None and dpi > config.downsample_threshold_dpi:
            scale = config.target_dpi / dpi
            new_w = max(16, round(pil.width * scale))
            new_h = max(16, round(pil.height * scale))
            if new_w < pil.width and new_h < pil.height:
                resized = pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
                # Keep the alpha channel aligned with the resized parent.
                smask = image.get("/SMask")
                aligned = True
                if has_active_smask and isinstance(smask, pikepdf.Stream):
                    aligned = _resize_smask(smask, new_w, new_h, config)
                if aligned:
                    pil = resized
                    width, height = new_w, new_h
                    stats.images_downsampled += 1

    if pil.mode == "1":
        payload = zopfli_compress(pil.tobytes(), config.zopfli_iterations)
        if len(payload) < len(original_raw):
            _apply_stream_update(
                image, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
                width=pil.width, height=pil.height, bpc=1,
            )
            stats.bytes_saved += len(original_raw) - len(payload)
            stats.images_optimized += 1
        return

    kind = classify_pil_image(pil)
    candidates: list[_Candidate] = []

    if kind == UI_GRAPHIC and pil.mode == "RGB" and config.quantize_ui_screenshots:
        colors = max(16, min(256, config.ui_palette_colors))
        indexed = _indexed_candidate(pil, max_colors=colors)
        if indexed is not None:
            indices, palette = indexed
            payload, parms = _smallest_flate_candidate(
                indices, pil.width, pil.height, bpp=1,
                iterations=config.zopfli_iterations,
            )
            candidates.append(
                (len(payload) + len(palette), payload, "/FlateDecode",
                 _indexed_colorspace(palette), parms)
            )

    if kind == PHOTOGRAPH or not candidates:
        jpeg = _encode_jpeg(pil, config.jpeg_quality)
        if jpeg is not None:
            candidates.append((len(jpeg), jpeg, "/DCTDecode", _device_cs(pil.mode), None))

    if pil.width * pil.height <= _CANDIDATE_DEFLATE_MAX_PIXELS:
        raw = pil.tobytes()
        bpp = 3 if pil.mode == "RGB" else 1
        payload, parms = _smallest_flate_candidate(
            raw, pil.width, pil.height, bpp=bpp, iterations=config.zopfli_iterations
        )
        candidates.append((len(payload), payload, "/FlateDecode", _device_cs(pil.mode), parms))

    if not candidates:
        return
    cost, payload, filter_, colorspace, parms = min(candidates, key=lambda c: c[0])
    if cost >= len(original_raw):
        return
    _apply_stream_update(
        image, payload, filter_=filter_, colorspace=colorspace,
        width=pil.width, height=pil.height, bpc=8,
        decode_parms=parms if filter_ == "/FlateDecode" else None,
    )
    stats.bytes_saved += len(original_raw) - cost
    stats.images_optimized += 1
    if isinstance(colorspace, pikepdf.Array):
        stats.images_indexed += 1


def _normalise_pil(pil: Image.Image) -> Image.Image:
    """Convert unsupported modes to RGB, and a strictly grey RGB to L."""
    if pil.mode in {"P", "CMYK", "LA", "RGBA", "I", "F", "YCbCr"}:
        pil = pil.convert("RGB")
    if pil.mode == "RGB":
        raw = pil.tobytes()
        if raw[0::3] == raw[1::3] == raw[2::3]:
            return pil.convert("L")
    return pil


def classify_pil_image(pil: Image.Image) -> str:
    """Classify an image as UI graphic, photograph or monochrome.

    The classifier is deliberately cheap: a 160 px thumbnail is enough to tell a
    flat screenshot (few colours, long horizontal runs) from continuous tone.
    """
    if pil.mode == "1":
        return MONOCHROME
    thumb = pil.copy()
    thumb.thumbnail((_THUMBNAIL_MAX, _THUMBNAIL_MAX), Image.Resampling.NEAREST)
    if thumb.mode != "RGB":
        thumb = thumb.convert("RGB")
    colors = thumb.getcolors(maxcolors=257)
    if colors is not None and len(colors) <= 256:
        return UI_GRAPHIC
    return UI_GRAPHIC if _flat_run_ratio(thumb.tobytes()) >= _UI_FLAT_RUN_RATIO else PHOTOGRAPH


def _flat_run_ratio(rgb: bytes) -> float:
    """Fraction of adjacent horizontal pixels that are byte-identical."""
    if not rgb or len(rgb) % 3:
        return 0.0
    pixels = len(rgb) // 3
    if pixels < 2:
        return 0.0
    runs = 0
    for i in range(3, len(rgb), 3):
        if rgb[i : i + 3] == rgb[i - 3 : i]:
            runs += 1
    return runs / (pixels - 1)


def _encode_jpeg(pil: Image.Image, quality: int) -> bytes | None:
    """Encode a progressive JPEG; return None when the mode is unsupported."""
    if pil.mode not in {"RGB", "L"}:
        pil = pil.convert("RGB")
    buffer = io.BytesIO()
    try:
        pil.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=2 if pil.mode == "RGB" else 0,
        )
    except Exception:
        return None
    return strip_jpeg_bloat_lossless(buffer.getvalue())


def _device_cs(mode: str) -> str:
    """Colour space name for a Pillow mode."""
    return "/DeviceGray" if mode == "L" else "/DeviceRGB"


def _indexed_colorspace(palette: bytes) -> pikepdf.Array:
    """Build the ``[/Indexed /DeviceRGB hival <lookup>]`` colour space.

    The first three entries must be PDF *names*: a plain string is serialised as
    a string object and every renderer rejects the colour space.
    """
    hival = max(0, len(palette) // 3 - 1)
    return pikepdf.Array(
        [pikepdf.Name("/Indexed"), pikepdf.Name("/DeviceRGB"), hival, pikepdf.String(palette)]
    )


def _indexed_candidate(pil: Image.Image, max_colors: int) -> tuple[bytes, bytes] | None:
    """Quantise to an indexed palette; return ``(indices, rgb_palette)``.

    This is the **lossy** path used by Mode B, where a new palette is a design
    choice rather than a transformation of an existing one.
    """
    rgb = pil if pil.mode == "RGB" else pil.convert("RGB")
    try:
        quantized = rgb.quantize(
            colors=max_colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE
        )
    except Exception:
        return None
    indices = quantized.tobytes()
    used = (max(indices) + 1) if indices else 1
    palette_obj = quantized.getpalette() or []
    palette = bytes(palette_obj[: used * 3])
    if not palette:
        return None
    return indices, palette


def _exact_palette_candidate(pil: Image.Image, max_colors: int = 256) -> tuple[bytes, bytes] | None:
    """Build a palette only when the image really has ``<= max_colors`` colours.

    Mode A must not change any pixel value, so quantisation is forbidden here.
    The palette is ordered by frequency (the spec's rule) and the index buffer
    maps every pixel onto its exact colour.
    """
    if pil.mode != "RGB":
        return None
    if pil.width * pil.height > _PREDICTOR_MAX_PIXELS:
        return None
    colors = pil.getcolors(maxcolors=max_colors + 1)
    if colors is None or not colors:
        return None
    colors.sort(key=lambda item: item[0], reverse=True)
    lookup = {rgb: index for index, (count, rgb) in enumerate(colors)}
    raw = pil.tobytes()
    indices = bytearray(len(raw) // 3)
    for position in range(0, len(raw), 3):
        indices[position // 3] = lookup[(raw[position], raw[position + 1], raw[position + 2])]
    palette = bytes(component for _count, rgb in colors for component in rgb)
    return bytes(indices), palette


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _smask_is_fully_opaque(smask: pikepdf.Object) -> bool:
    """True when an 8-bit /SMask has no transparency at all."""
    try:
        width = int(smask.get("/Width", 0) or 0)
        height = int(smask.get("/Height", 0) or 0)
        bpc = int(smask.get("/BitsPerComponent", 8) or 8)
        if bpc != 8 or width <= 0 or height <= 0:
            return False
        decoded = smask.read_bytes()
    except Exception:
        return False
    return len(decoded) == width * height and decoded.count(0xFF) == len(decoded)


def _optimize_smask_stream(smask: pikepdf.Object, config: CompressionConfig) -> bool:
    """Recompress an /SMask; return True when it was rewritten smaller."""
    try:
        width = int(smask.get("/Width", 0) or 0)
        height = int(smask.get("/Height", 0) or 0)
        bpc = int(smask.get("/BitsPerComponent", 8) or 8)
        decoded = smask.read_bytes()
    except Exception:
        return False
    if bpc != 8 or width <= 0 or height <= 0 or len(decoded) != width * height:
        return False
    try:
        original = smask.read_raw_bytes()
    except Exception:
        return False
    payload, parms = _smallest_flate_candidate(
        decoded, width, height, bpp=1, iterations=config.zopfli_iterations
    )
    if len(payload) >= len(original):
        return False
    _apply_stream_update(
        smask, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
        width=width, height=height, bpc=8, decode_parms=parms,
    )
    return True


def _resize_smask(smask: pikepdf.Object, width: int, height: int, config: CompressionConfig) -> bool:
    """Re-render an /SMask at the parent's new size (FR-IMG-3)."""
    try:
        pil = pikepdf.PdfImage(smask).as_pil_image().convert("L")
        pil = pil.resize((width, height), Image.Resampling.LANCZOS)
        payload, parms = _smallest_flate_candidate(
            pil.tobytes(), width, height, bpp=1, iterations=config.zopfli_iterations
        )
        _apply_stream_update(
            smask, payload, filter_="/FlateDecode", colorspace="/DeviceGray",
            width=width, height=height, bpc=8, decode_parms=parms,
        )
        return True
    except Exception:
        return False


def _is_dct(image: pikepdf.Object) -> bool:
    """True when the stream filter is DCTDecode (single name or 1-element array)."""
    try:
        filter_ = image.get("/Filter")
    except Exception:
        return False
    if filter_ is None:
        return False
    if isinstance(filter_, pikepdf.Array):
        return len(filter_) == 1 and filter_[0] == "/DCTDecode"
    return filter_ == "/DCTDecode"


def _apply_stream_update(
    image: pikepdf.Object,
    payload: bytes,
    *,
    filter_: str,
    width: int | None = None,
    height: int | None = None,
    colorspace: Any = None,
    bpc: int | None = None,
    decode_parms: pikepdf.Object | None = None,
) -> None:
    """Write *payload* into *image* and normalise its dictionary."""
    if decode_parms is not None:
        image.write(payload, filter=pikepdf.Name(filter_), decode_parms=decode_parms)
    else:
        image.write(payload, filter=pikepdf.Name(filter_))
        try:
            del image.stream_dict["/DecodeParms"]
        except (KeyError, AttributeError):
            pass
    stream_dict = image.stream_dict
    if width is not None:
        stream_dict["/Width"] = width
    if height is not None:
        stream_dict["/Height"] = height
    if colorspace is not None:
        # A plain string would be stored as a PDF string, not a name, and every
        # renderer would reject the image (blank page).
        stream_dict["/ColorSpace"] = (
            pikepdf.Name(colorspace) if isinstance(colorspace, str) else colorspace
        )
    if bpc is not None:
        stream_dict["/BitsPerComponent"] = bpc
    try:
        del stream_dict["/Decode"]
    except (KeyError, AttributeError):
        pass


def _smallest_flate_candidate(
    raw: bytes,
    width: int,
    height: int,
    *,
    bpp: int,
    iterations: int = 15,
) -> tuple[bytes, pikepdf.Dictionary | None]:
    """Return the smallest of raw-deflate and Predictor-15 deflate for *raw*."""
    plain = zopfli_compress(raw, iterations)
    if width * height > _PREDICTOR_MAX_PIXELS:
        return plain, None
    predicted = apply_png_predictor_15(raw, width, height, bpp)
    compressed = zopfli_compress(predicted, iterations)
    if len(compressed) < len(plain):
        parms = pikepdf.Dictionary(
            {
                "/Predictor": 15,
                "/Colors": bpp,
                "/BitsPerComponent": 8,
                "/Columns": width,
            }
        )
        return compressed, parms
    return plain, None


def apply_png_predictor_15(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    """Encode *raw* rows with PNG filter bytes for PDF Predictor 15.

    All five PNG filters are considered per row: None, Sub, Up, Average and
    Paeth. The row with the smallest sum of absolute signed residuals wins,
    which is the standard libpng heuristic; deflate density depends on it.
    """
    stride = width * bpp
    if stride <= 0 or height <= 0 or len(raw) < stride * height:
        return b""
    out = bytearray((stride + 1) * height)
    prior = bytes(stride)
    pos = 0
    for y in range(height):
        row = raw[y * stride : (y + 1) * stride]
        best_filter = 0
        best_cost = None
        best_row = row
        for filter_type in range(5):
            filtered = _filter_row(filter_type, row, prior, bpp)
            cost = 0
            for value in filtered:
                cost += value if value < 128 else 256 - value
            if best_cost is None or cost < best_cost:
                best_cost, best_filter, best_row = cost, filter_type, filtered
                if cost == 0:
                    break
        out[pos] = best_filter
        out[pos + 1 : pos + 1 + stride] = best_row
        pos += stride + 1
        prior = row
    return bytes(out)


def _filter_row(filter_type: int, row: bytes, prior: bytes, bpp: int) -> bytes:
    """Apply one PNG filter to a single scanline."""
    stride = len(row)
    if filter_type == 0:
        return row
    out = bytearray(stride)
    if filter_type == 1:  # Sub
        for i in range(stride):
            out[i] = (row[i] - (row[i - bpp] if i >= bpp else 0)) & 0xFF
        return bytes(out)
    if filter_type == 2:  # Up
        for i in range(stride):
            out[i] = (row[i] - prior[i]) & 0xFF
        return bytes(out)
    if filter_type == 3:  # Average
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            out[i] = (row[i] - ((left + prior[i]) >> 1)) & 0xFF
        return bytes(out)
    for i in range(stride):  # Paeth
        left = row[i - bpp] if i >= bpp else 0
        up = prior[i]
        upper_left = prior[i - bpp] if i >= bpp else 0
        out[i] = (row[i] - _paeth_predictor(left, up, upper_left)) & 0xFF
    return bytes(out)


def _paeth_predictor(left: int, up: int, upper_left: int) -> int:
    """PNG Paeth predictor (ISO/IEC 15948 §9.4)."""
    estimate = left + up - upper_left
    dist_left = abs(estimate - left)
    dist_up = abs(estimate - up)
    dist_ul = abs(estimate - upper_left)
    if dist_left <= dist_up and dist_left <= dist_ul:
        return left
    return up if dist_up <= dist_ul else upper_left


# --------------------------------------------------------------------------- #
# JPEG marker surgery
# --------------------------------------------------------------------------- #

_KEEP_MARKERS = {0xE0, 0xE2, 0xEE}  # APP0 JFIF, APP2 ICC_PROFILE, APP14 Adobe
_TABLE_MARKERS = set(range(0xC0, 0xD0)) | {0xDB, 0xDD}  # SOF*, DQT, DHT, DRI


def strip_jpeg_bloat_lossless(data: bytes) -> bytes:
    """Drop metadata APPn and COM segments from a JPEG without touching pixels.

    The entropy-coded scan is copied verbatim from SOS to EOI, so DCT
    coefficients are never re-encoded. Any structural anomaly returns the
    original bytes: a malformed JPEG must never be made worse.
    """
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return data
    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos + 1 < len(data):
        if data[pos] != 0xFF:
            return data
        marker = data[pos + 1]
        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            return bytes(out)
        if marker == 0xDA:  # SOS: copy the rest of the scan untouched
            out += data[pos:]
            return bytes(out)
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            out += data[pos : pos + 2]
            pos += 2
            continue
        if pos + 4 > len(data):
            return data
        segment_length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if segment_length < 2 or pos + 2 + segment_length > len(data):
            return data
        keep = marker in _KEEP_MARKERS or marker in _TABLE_MARKERS
        if keep:
            out += data[pos : pos + 2 + segment_length]
        pos += 2 + segment_length
    return data
