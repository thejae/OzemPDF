"""Visually-lossless behaviour: DPI math, downsampling, palettes, predictor."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from conftest import image_streams, nonwhite_pixels, pixel_difference

from ozempdf.config import CompressionConfig
from ozempdf.engine import calculate_effective_image_dpis, compress_pdf_file
from ozempdf.engine.image_optimizer import _filter_row, _paeth_predictor, apply_png_predictor_15


def test_effective_dpi_from_ctm(assets) -> None:
    with pikepdf.open(assets.photo) as pdf:
        dpis = calculate_effective_image_dpis(pdf)
    assert dpis, "expected at least one image DPI"
    assert any(abs(value - 600) < 5 for value in dpis.values()), dpis


def test_balanced_downsamples_a_600_dpi_photo(assets, tmp_path: Path) -> None:
    output = tmp_path / "balanced.pdf"
    result = compress_pdf_file(assets.photo, output, CompressionConfig.from_preset("balanced"))

    assert result.success, result.error_message
    assert result.images_downsampled >= 1
    assert result.reduction_percent > 50
    assert nonwhite_pixels(output) > 100
    images = image_streams(output)
    assert images
    assert max(images[0]["width"], images[0]["height"]) < 1800


def test_max_slim_is_not_larger_than_balanced(assets, tmp_path: Path) -> None:
    balanced = compress_pdf_file(assets.photo, tmp_path / "b.pdf", CompressionConfig.from_preset("balanced"))
    maximum = compress_pdf_file(assets.photo, tmp_path / "m.pdf", CompressionConfig.from_preset("max"))
    assert balanced.success and maximum.success
    assert maximum.compressed_bytes <= balanced.compressed_bytes


def test_screenshot_becomes_palette_not_jpeg(assets, tmp_path: Path) -> None:
    output = tmp_path / "ui.pdf"
    result = compress_pdf_file(assets.ui, output, CompressionConfig.from_preset("balanced"))

    assert result.success
    assert result.images_indexed >= 1
    images = image_streams(output)
    assert images
    assert "FlateDecode" in images[0]["filter"]
    assert "Indexed" in images[0]["colorspace"]


def test_low_density_image_is_not_downsampled(tmp_path: Path) -> None:
    from pdf_fixtures import _jpeg_stream, _page_with_images, gradient_rgb

    source = tmp_path / "low-dpi.pdf"
    pdf = pikepdf.new()
    image = _jpeg_stream(pdf, gradient_rgb(600, 400))
    _page_with_images(pdf, [(image, (432, 288, 72, 300))])  # 100 DPI
    pdf.save(source)
    pdf.close()

    output = tmp_path / "low-dpi-out.pdf"
    result = compress_pdf_file(source, output, CompressionConfig.from_preset("balanced"))
    assert result.success
    assert result.images_downsampled == 0
    assert (image_streams(output)[0]["width"], image_streams(output)[0]["height"]) == (600, 400)


# --------------------------------------------------------------------------- #
# 1-bit masks (Word renders some text runs as a tiny colour image + 1-bit SMask)
# --------------------------------------------------------------------------- #


def _bilevel_rasters(path: Path) -> list[tuple[int, int, str, bytes]]:
    """Decoded raster of every 1-bit image, so compression can be checked."""
    found = []
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image":
                continue
            if int(obj.get("/BitsPerComponent", 8) or 8) != 1:
                continue
            try:
                raster = obj.read_bytes()
            except Exception:
                raster = b""
            found.append(
                (
                    int(obj.get("/Width", 0) or 0),
                    int(obj.get("/Height", 0) or 0),
                    str(obj.get("/ColorSpace", "")),
                    raster,
                )
            )
    return sorted(found, key=lambda item: (item[0], item[1], len(item[3])))


def test_bilevel_mask_keeps_every_pixel(assets, tmp_path: Path) -> None:
    """Recompressing a 1-bit mask must not shrink it to a deflated blob."""
    before = _bilevel_rasters(assets.bilevel)
    output = tmp_path / "bilevel.pdf"
    result = compress_pdf_file(assets.bilevel, output, CompressionConfig.from_preset("balanced"))

    assert result.success, result.error_message
    assert result.images_optimized >= 1, "the 1-bit stream should be recompressed"
    assert _bilevel_rasters(output) == before
    assert pixel_difference(assets.bilevel, output) < 0.01


def test_bilevel_mask_with_decode_is_not_reencoded(assets, tmp_path: Path) -> None:
    """A ``/Decode`` inversion changes what the bytes mean, so leave it alone."""
    output = tmp_path / "bilevel-inverted.pdf"
    result = compress_pdf_file(
        assets.bilevel_inverted, output, CompressionConfig.from_preset("balanced")
    )
    assert result.success, result.error_message

    with pikepdf.open(assets.bilevel_inverted) as source, pikepdf.open(output) as compressed:
        def only_mask(pdf) -> pikepdf.Object:
            matches = [
                obj
                for obj in pdf.objects
                if isinstance(obj, pikepdf.Stream)
                and obj.get("/Subtype") == "/Image"
                and int(obj.get("/BitsPerComponent", 8) or 8) == 1
            ]
            assert len(matches) == 1
            return matches[0]

        original = only_mask(source)
        stored = only_mask(compressed)
        assert stored.get("/Decode") is not None
        assert stored.read_raw_bytes() == original.read_raw_bytes()


def test_text_mask_overlay_still_renders(assets, tmp_path: Path) -> None:
    """The Word text-as-mask construct must keep its text after compression."""
    output = tmp_path / "text-mask.pdf"
    result = compress_pdf_file(assets.text_mask, output, CompressionConfig.from_preset("balanced"))

    assert result.success, result.error_message
    assert nonwhite_pixels(output) > 100
    assert pixel_difference(assets.text_mask, output) < 0.01
    assert any("Indexed" in image["colorspace"] for image in image_streams(output))


# --------------------------------------------------------------------------- #
# Predictor (D-02: all five PNG filters must really be implemented)
# --------------------------------------------------------------------------- #


def _unfilter(data: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    out = bytearray()
    prior = bytes(stride)
    position = 0
    for _ in range(height):
        filter_type = data[position]
        row = bytearray(data[position + 1 : position + 1 + stride])
        position += stride + 1
        for index in range(stride):
            left = row[index - bpp] if index >= bpp else 0
            up = prior[index]
            upper_left = prior[index - bpp] if index >= bpp else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 0xFF
            elif filter_type == 2:
                row[index] = (row[index] + up) & 0xFF
            elif filter_type == 3:
                row[index] = (row[index] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                row[index] = (row[index] + _paeth_predictor(left, up, upper_left)) & 0xFF
        out += row
        prior = bytes(row)
    return bytes(out)


@pytest.mark.parametrize("bpp", [1, 3])
def test_predictor_roundtrip_all_filters(bpp: int) -> None:
    width, height = 37, 11
    stride = width * bpp
    raw = bytes((index * 37 + (index // stride) * 11) % 256 for index in range(stride * height))
    encoded = apply_png_predictor_15(raw, width, height, bpp)
    assert len(encoded) == (stride + 1) * height
    filters = {encoded[row * (stride + 1)] for row in range(height)}
    assert filters <= {0, 1, 2, 3, 4}
    assert _unfilter(encoded, width, height, bpp) == raw


def test_paeth_filter_matches_reference() -> None:
    prior = bytes([10, 20, 30, 40, 50, 60])
    row = bytes([12, 25, 33, 41, 52, 61])
    filtered = _filter_row(4, row, prior, bpp=3)
    expected = bytes(
        (row[i] - _paeth_predictor(row[i - 3] if i >= 3 else 0, prior[i], prior[i - 3] if i >= 3 else 0))
        & 0xFF
        for i in range(len(row))
    )
    assert filtered == expected


def test_paeth_predictor_values() -> None:
    # The three branches of the PNG Paeth predictor.
    assert _paeth_predictor(10, 20, 30) == 10
    assert _paeth_predictor(50, 10, 20) == 50
    assert _paeth_predictor(10, 10, 10) == 10
