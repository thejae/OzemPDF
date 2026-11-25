"""Strict-lossless invariants: identical pixels, identical operators, never grow."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from conftest import content_bytes, image_streams, nonwhite_pixels, pixel_difference

from ozempdf.config import CompressionConfig
from ozempdf.engine import compress_pdf_file


def test_strict_lossless_pixels_and_operators_identical(assets, tmp_path: Path) -> None:
    output = tmp_path / "lossless.pdf"
    result = compress_pdf_file(assets.lossless, output, CompressionConfig.from_preset("lossless"))

    assert result.success, result.error_message
    assert output.is_file()
    assert result.compressed_bytes < result.original_bytes
    # INV-1: page content bytes are untouched.
    assert content_bytes(output) == content_bytes(assets.lossless)
    # Mode A: decoded pixels are bit-identical.
    assert pixel_difference(output, assets.lossless) == 0.0
    assert nonwhite_pixels(output) > 0
    assert result.images_inspected > 0


def test_never_grow_guardrail_keeps_tiny_document(assets, tmp_path: Path) -> None:
    output = tmp_path / "tiny.pdf"
    result = compress_pdf_file(assets.tiny, output, CompressionConfig.from_preset("balanced"))

    assert result.success
    assert result.kept_original_guardrail is True
    assert result.already_optimal is True
    assert result.compressed_bytes == result.original_bytes
    assert output.read_bytes() == assets.tiny.read_bytes()
    # D-12: the guardrail outcome is visible in the JSON report.
    assert result.to_dict()["kept_original_guardrail"] is True
    assert result.to_dict()["already_optimal"] is True


def test_bloat_dedup_and_opaque_smask_removed(assets, tmp_path: Path) -> None:
    output = tmp_path / "bloat.pdf"
    result = compress_pdf_file(assets.bloat, output, CompressionConfig.from_preset("lossless"))

    assert result.success, result.error_message
    assert result.duplicate_images_merged >= 1
    assert result.opaque_smasks_stripped >= 1
    assert output.stat().st_size < assets.bloat.stat().st_size
    with pikepdf.open(output) as pdf:
        assert "/PieceInfo" not in pdf.Root
        for page in pdf.pages:
            assert "/PieceInfo" not in page
            assert "/Thumb" not in page
        # Lossless keeps document metadata by design (US-048).
        assert len(pdf.docinfo) > 0
        # The stencil-free image must have lost its fully opaque soft mask.
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image":
                assert obj.get("/SMask") is None


def test_max_slim_strips_metadata(assets, tmp_path: Path) -> None:
    output = tmp_path / "max.pdf"
    result = compress_pdf_file(assets.bloat, output, CompressionConfig.from_preset("max"))
    assert result.success, result.error_message
    with pikepdf.open(output) as pdf:
        assert len(pdf.docinfo) == 0
        assert "/Metadata" not in pdf.Root


def test_image_stream_only_replaced_when_smaller(assets, tmp_path: Path) -> None:
    output = tmp_path / "images.pdf"
    config = CompressionConfig.from_preset("lossless")
    result = compress_pdf_file(assets.lossless, output, config)
    assert result.success

    before = sorted(image_streams(assets.lossless), key=lambda info: info["objgen"])
    after = sorted(image_streams(output), key=lambda info: info["objgen"])
    assert before and len(after) == len(before)
    # INV-2: no replacement image stream grew, and dimensions are unchanged.
    for source, slimmed in zip(before, after, strict=True):
        assert slimmed["length"] <= source["length"]
        assert (slimmed["width"], slimmed["height"]) == (source["width"], source["height"])


def test_never_rasterizes_page_content(assets, tmp_path: Path) -> None:
    """A vector-only document must not gain an image or lose its operators."""
    source = tmp_path / "vector.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(300, 300))
    operators = b"q 2 0 0 2 50 50 cm 0 0 100 100 re S BT /F1 12 Tf 10 10 Td (vector) Tj ET Q"
    page.Contents = pdf.make_stream(operators)
    pdf.save(source)

    output = tmp_path / "vector-out.pdf"
    result = compress_pdf_file(source, output, CompressionConfig.from_preset("balanced"))
    assert result.success
    assert content_bytes(output) == operators
    assert image_streams(output) == []


@pytest.mark.parametrize("preset", ["lossless", "balanced", "max"])
def test_every_preset_produces_a_readable_page(assets, tmp_path: Path, preset: str) -> None:
    output = tmp_path / f"{preset}.pdf"
    result = compress_pdf_file(assets.ui, output, CompressionConfig.from_preset(preset))
    assert result.success, result.error_message
    assert nonwhite_pixels(output) > 100
