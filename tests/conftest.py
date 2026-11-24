"""Shared fixtures and pixel helpers for the OzemPDF test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pdf_fixtures import (  # noqa: E402
    build_bloat_pdf,
    build_font_pdf,
    build_lossless_pdf,
    build_photo_pdf,
    build_protected_pdf,
    build_signed_encrypted_pdf,
    build_tiny_pdf,
    build_ui_pdf,
)


@pytest.fixture(scope="session")
def assets(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """Every input document, built once per session."""
    base = tmp_path_factory.mktemp("ozempdf-assets")
    return SimpleNamespace(
        lossless=build_lossless_pdf(base / "lossless.pdf"),
        photo=build_photo_pdf(base / "photo.pdf"),
        ui=build_ui_pdf(base / "ui.pdf"),
        tiny=build_tiny_pdf(base / "tiny.pdf"),
        bloat=build_bloat_pdf(base / "bloat.pdf"),
        font=build_font_pdf(base / "font.pdf"),
        signed=build_signed_encrypted_pdf(base / "signed.pdf"),
        protected=build_protected_pdf(base / "protected.pdf", password="secret"),
    )


def render_page(path: Path | str, page_index: int = 0, scale: float = 1.0) -> Image.Image:
    """Rasterise one page with pypdfium2 (the renderer users actually see)."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        return document[page_index].render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()


def pixel_difference(left: Path | str, right: Path | str, page_index: int = 0) -> float:
    """Mean absolute pixel difference between two rendered pages."""
    import numpy as np

    first = np.asarray(render_page(left, page_index), dtype=np.int16)
    second = np.asarray(render_page(right, page_index), dtype=np.int16)
    if first.shape != second.shape:
        return 255.0
    return float(np.abs(first - second).mean())


def nonwhite_pixels(path: Path | str, page_index: int = 0) -> int:
    """Count pixels that are not near-white; a blank page returns 0."""
    image = render_page(path, page_index, scale=0.5).convert("L")
    histogram = image.histogram()
    return sum(histogram[:250])


def content_bytes(path: Path | str, page_index: int = 0) -> bytes:
    """Decoded page content stream bytes (arrays are concatenated)."""
    import pikepdf

    with pikepdf.open(path) as pdf:
        contents = pdf.pages[page_index].Contents
        if isinstance(contents, pikepdf.Array):
            return b"\n".join(stream.read_bytes() for stream in contents)
        return contents.read_bytes()


def image_streams(path: Path | str) -> list[dict]:
    """Every image XObject, summarised while the document is open.

    Objects cannot outlive their ``Pdf`` handle, so the payload and dictionary
    values are copied here.
    """
    import pikepdf

    found: dict[tuple[int, int], dict] = {}
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image":
                continue
            try:
                raw = obj.read_raw_bytes()
            except Exception:
                raw = b""
            found[obj.objgen] = {
                "objgen": obj.objgen,
                "length": len(raw),
                "filter": str(obj.get("/Filter")),
                "colorspace": str(obj.get("/ColorSpace")),
                "width": int(obj.get("/Width", 0) or 0),
                "height": int(obj.get("/Height", 0) or 0),
                "raw": raw,
                "has_smask": obj.get("/SMask") is not None,
            }
    return list(found.values())
