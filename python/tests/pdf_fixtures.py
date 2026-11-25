"""Synthetic PDF fixtures for the OzemPDF test suite.

Every fixture is generated at test time so the repository carries no binary
documents. Images are deterministic gradients or flat UI mock-ups, which is
enough to exercise downsampling, palette quantisation, opaque masks,
duplication, bloat removal, fonts and encrypted/signed documents.
"""

from __future__ import annotations

import io
import zlib
from pathlib import Path

import numpy as np
import pikepdf
from PIL import Image, ImageDraw

# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


def gradient_rgb(width: int = 1800, height: int = 1200) -> Image.Image:
    """Continuous-tone test image (photograph stand-in)."""
    x = np.linspace(0, 255, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 255, height, dtype=np.float32)[:, None]
    red = (x + y * 0.4) % 256
    green = (y + x * 0.7) % 256
    blue = (x * 0.3 + y * 1.1) % 256
    return Image.fromarray(np.stack([red, green, blue], axis=-1).astype(np.uint8), "RGB")


def flat_ui_rgb(width: int = 600, height: int = 400) -> Image.Image:
    """Flat UI mock-up: few colours, long horizontal runs."""
    image = Image.new("RGB", (width, height), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, width - 20, 60], fill=(30, 40, 90))
    for index in range(8):
        y = 90 + index * 36
        draw.rectangle([20, y, 200, y + 12], fill=(40, 40, 40))
        right = max(230, width - 80)
        draw.rectangle([220, y, right, y + 12], fill=(190, 190, 190))
    return image


def _jpeg_stream(pdf: pikepdf.Pdf, image: Image.Image, quality: int = 92) -> pikepdf.Object:
    """Embed *image* as a DCTDecode image XObject."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    stream = pikepdf.Stream(pdf, buffer.getvalue())
    stream.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": image.width,
            "/Height": image.height,
            "/ColorSpace": pikepdf.Name("/DeviceRGB"),
            "/BitsPerComponent": 8,
            "/Filter": pikepdf.Name("/DCTDecode"),
        }
    )
    return pdf.make_indirect(stream)


def _flate_stream(pdf: pikepdf.Pdf, image: Image.Image) -> pikepdf.Object:
    """Embed an RGB image as a FlateDecode image XObject (lossless source)."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    stream = pikepdf.Stream(pdf, zlib.compress(image.tobytes(), 9))
    stream.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": image.width,
            "/Height": image.height,
            "/ColorSpace": pikepdf.Name("/DeviceRGB"),
            "/BitsPerComponent": 8,
            "/Filter": pikepdf.Name("/FlateDecode"),
        }
    )
    return pdf.make_indirect(stream)


def _bilevel_stream(pdf: pikepdf.Pdf, image: Image.Image) -> pikepdf.Object:
    """Embed a 1-bit image as FlateDecode *stored* data, the way Word does it.

    Level 0 keeps the source stream large so the compressor's zopfli candidate
    always wins, which is what exercises the 1-bit recompression path.
    """
    if image.mode != "1":
        image = image.convert("1")
    stream = pikepdf.Stream(pdf, zlib.compress(image.tobytes(), 0))
    stream.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": image.width,
            "/Height": image.height,
            "/ColorSpace": pikepdf.Name("/DeviceGray"),
            "/BitsPerComponent": 1,
            "/Filter": pikepdf.Name("/FlateDecode"),
        }
    )
    return pdf.make_indirect(stream)


def bilevel_mask(width: int = 1188, height: int = 197) -> Image.Image:
    """Dense 1-bit text-run mask: glyph-like blocks on a transparent ground."""
    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for index in range(6):
        left = 24 + index * 190
        draw.rectangle([left, 40, left + 150, 160], fill=1)
    return image


def _page_with_images(
    pdf: pikepdf.Pdf,
    placements: list[tuple[pikepdf.Object, tuple[float, float, float, float]]],
    *,
    text: bytes = b"",
) -> None:
    """Add a page drawing each ``(xobject, (w, h, x, y))`` placement."""
    page = pdf.add_blank_page(page_size=(612, 792))
    resources = {}
    commands = []
    for index, (xobject, (width, height, x, y)) in enumerate(placements):
        name = f"/Im{index + 1}"
        resources[name] = xobject
        commands.append(f"q {width} 0 0 {height} {x} {y} cm {name} Do Q".encode())
    page.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary(resources)})
    page.Contents = pdf.make_stream(b"\n".join(commands) + text)


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def build_lossless_pdf(path: Path) -> Path:
    """Flate image + vector content: the strict-lossless workhorse."""
    pdf = pikepdf.new()
    image = _flate_stream(pdf, gradient_rgb(600, 400))
    text = b"\nq 1 0 0 1 0 0 cm BT /F1 18 Tf 72 700 Td (Line art preserved) Tj ET Q"
    _page_with_images(pdf, [(image, (400, 266, 100, 100))], text=text)
    pdf.save(path)
    pdf.close()
    return path


def build_photo_pdf(path: Path, *, width: int = 1800, height: int = 1200) -> Path:
    """1800x1200 photo drawn in a 3x2 inch box, i.e. 600 effective DPI."""
    pdf = pikepdf.new()
    image = _jpeg_stream(pdf, gradient_rgb(width, height))
    _page_with_images(pdf, [(image, (216, 144, 72, 400))])
    pdf.save(path)
    pdf.close()
    return path


def build_ui_pdf(path: Path) -> Path:
    """Flat UI screenshot at roughly 108 DPI — should become a palette image."""
    pdf = pikepdf.new()
    image = _jpeg_stream(pdf, flat_ui_rgb(600, 400))
    _page_with_images(pdf, [(image, (400, 266, 100, 300))])
    pdf.save(path)
    pdf.close()
    return path


def build_tiny_pdf(path: Path) -> Path:
    """A file with nothing to gain: the never-grow guardrail must keep it."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Contents = pdf.make_stream(b"q 0 0 1 rg 0 0 10 10 re f Q")
    pdf.save(path)
    pdf.close()
    return path


def build_truncated_pdf(path: Path, *, source: Path | None = None) -> Path:
    """A document that lost its cross-reference table and trailer.

    Every object survives; only the lookup table is gone, which is what a
    half-written or badly finalised file looks like. PDFium requires the table
    and refuses the document, while qpdf scans the objects and rebuilds one —
    the class the inspector's repaired-copy fallback exists for (N-13).
    """
    build_ui_pdf(source or path)
    raw = (source or path).read_bytes()
    cut = raw.rfind(b"\nxref")
    if cut <= 0:
        raise RuntimeError("fixture has no cross-reference table to remove")
    path.write_bytes(raw[:cut] + b"\n")
    return path


def build_bilevel_pdf(path: Path) -> Path:
    """A page drawn entirely through a 1-bit DeviceGray mask."""
    pdf = pikepdf.new()
    image = _bilevel_stream(pdf, bilevel_mask())
    _page_with_images(pdf, [(image, (594, 98.5, 72, 400))])
    pdf.save(path)
    pdf.close()
    return path


def build_bilevel_inverted_pdf(path: Path) -> Path:
    """A 1-bit mask whose ``/Decode`` array inverts it; bytes must stay put."""
    pdf = pikepdf.new()
    image = _bilevel_stream(pdf, bilevel_mask())
    image.stream_dict["/Decode"] = pikepdf.Array([1, 0])
    _page_with_images(pdf, [(image, (594, 98.5, 72, 400))])
    pdf.save(path)
    pdf.close()
    return path


def build_text_mask_pdf(path: Path) -> Path:
    """The construct Word emits for a text run: a 2x2 indexed image + 1-bit /SMask."""
    pdf = pikepdf.new()
    palette = bytes((26, 26, 26, 26, 26, 26))
    base = pdf.make_indirect(
        pikepdf.Stream(pdf, zlib.compress(b"\x00\x00", 0))
    )
    base.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": 2,
            "/Height": 2,
            "/ColorSpace": pikepdf.Array(
                [pikepdf.Name("/Indexed"), pikepdf.Name("/DeviceRGB"), 1, pikepdf.String(palette)]
            ),
            "/BitsPerComponent": 1,
            "/Filter": pikepdf.Name("/FlateDecode"),
        }
    )
    base.stream_dict["/SMask"] = _bilevel_stream(pdf, bilevel_mask())
    _page_with_images(pdf, [(base, (142.59, 23.64, 43.2, 600))])
    pdf.save(path)
    pdf.close()
    return path


def build_bloat_pdf(path: Path) -> Path:
    """Duplicate images, opaque /SMask, /PieceInfo, /Thumb and XMP metadata."""
    pdf = pikepdf.new()
    base = flat_ui_rgb(400, 300)
    image = _flate_stream(pdf, base)
    duplicate = _flate_stream(pdf, base)
    mask = Image.new("L", (400, 300), 255)
    smask = pikepdf.Stream(pdf, zlib.compress(mask.tobytes(), 9))
    smask.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Image"),
            "/Width": 400,
            "/Height": 300,
            "/ColorSpace": pikepdf.Name("/DeviceGray"),
            "/BitsPerComponent": 8,
            "/Filter": pikepdf.Name("/FlateDecode"),
        }
    )
    image.stream_dict["/SMask"] = pdf.make_indirect(smask)
    duplicate.stream_dict["/SMask"] = image.stream_dict["/SMask"]
    _page_with_images(pdf, [(image, (300, 225, 80, 300))])
    _page_with_images(pdf, [(duplicate, (300, 225, 80, 300))])
    for page in pdf.pages:
        page.PieceInfo = pikepdf.Dictionary({"/Private": pikepdf.String("x" * 4000)})
    pdf.Root.PieceInfo = pikepdf.Dictionary({"/Private": pikepdf.String("y" * 4000)})
    pdf.pages[0].Thumb = _jpeg_stream(pdf, flat_ui_rgb(80, 60))
    with pdf.open_metadata() as meta:
        meta["dc:title"] = "Bloat fixture"
    pdf.save(path)
    pdf.close()
    return path


def build_font_pdf(path: Path) -> Path:
    """A document with an embedded TrueType font and a /ToUnicode CMap."""
    try:
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
    except ImportError:  # pragma: no cover - fontTools is a runtime dependency
        raise RuntimeError("fontTools is required to build the font fixture") from None

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A", "B"])
    builder.setupCharacterMap({65: "A", 66: "B"})
    glyphs = {}
    for name in (".notdef", "A", "B"):
        pen = TTGlyphPen(None)
        if name != ".notdef":
            pen.moveTo((100, 0))
            pen.lineTo((100, 700))
            pen.lineTo((600, 700))
            pen.lineTo((600, 0))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (700, 100) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Ozem Test Sans", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    buffer = io.BytesIO()
    builder.save(buffer)
    font_bytes = buffer.getvalue()

    pdf = pikepdf.new()
    font_stream = pikepdf.Stream(pdf, font_bytes)
    font_stream.stream_dict["/Length1"] = len(font_bytes)
    font_stream = pdf.make_indirect(font_stream)
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/OzemTestSans"),
                "/Flags": 32,
                "/FontBBox": pikepdf.Array([0, -200, 700, 800]),
                "/ItalicAngle": 0,
                "/Ascent": 800,
                "/Descent": -200,
                "/CapHeight": 700,
                "/StemV": 80,
                "/FontFile2": font_stream,
            }
        )
    )
    widths = pikepdf.Array([0] * 32 + [700, 700] + [0] * 224)
    cid_to_gid = pdf.make_stream(b"")
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/OzemTestSans"),
                "/FirstChar": 0,
                "/LastChar": 255,
                "/Widths": widths,
                "/FontDescriptor": descriptor,
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                "/ToUnicode": _to_unicode_stream(pdf),
            }
        )
    )
    del cid_to_gid
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
    page.Contents = pdf.make_stream(b"BT /F1 24 Tf 72 700 Td (ABAB) Tj ET")
    pdf.save(path)
    pdf.close()
    return path


def _to_unicode_stream(pdf: pikepdf.Pdf) -> pikepdf.Object:
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"1 begincodespacerange <00> <FF> endcodespacerange\n"
        b"2 beginbfchar <41> <0041> <42> <0042> endbfchar\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    stream = pikepdf.Stream(pdf, zlib.compress(cmap, 9))
    stream.stream_dict["/Filter"] = pikepdf.Name("/FlateDecode")
    return pdf.make_indirect(stream)


def build_signed_encrypted_pdf(path: Path, *, user_password: str = "", owner_password: str = "owner") -> Path:
    """Owner-encrypted document carrying a visible signature widget and /Perms."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Contents = pdf.make_stream(b"q 0.9 g 0 0 612 792 re f Q")

    signature = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Sig"),
                "/Filter": pikepdf.Name("/Adobe.PPKLite"),
                "/SubFilter": pikepdf.Name("/adbe.pkcs7.detached"),
                "/ByteRange": pikepdf.Array([0, 1000, 2000, 3000]),
                "/Contents": pikepdf.String(b"\x00" * 64),
            }
        )
    )
    appearance = pdf.make_stream(b"q 1 0 0 1 0 0 cm 0 0 1 rg 0 0 100 40 re f Q")
    appearance.stream_dict.update(
        {
            "/Type": pikepdf.Name("/XObject"),
            "/Subtype": pikepdf.Name("/Form"),
            "/BBox": pikepdf.Array([0, 0, 100, 40]),
        }
    )
    widget = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/FT": pikepdf.Name("/Sig"),
                "/Rect": pikepdf.Array([72, 72, 172, 112]),
                "/V": signature,
                "/AP": pikepdf.Dictionary({"/N": appearance}),
            }
        )
    )
    page.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(
        {"/Fields": pikepdf.Array([widget]), "/SigFlags": 3}
    )
    pdf.Root.Perms = pikepdf.Dictionary({"/DocMDP": signature})
    pdf.save(path, encryption=pikepdf.Encryption(owner=owner_password, user=user_password, R=4))
    pdf.close()
    return path


def build_protected_pdf(path: Path, *, password: str = "secret") -> Path:
    """A document that needs an open password (no owner-only restriction)."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Contents = pdf.make_stream(b"q 0 0 1 rg 0 0 10 10 re f Q")
    pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user=password, R=4))
    pdf.close()
    return path


def build_nested_folder_tree(root: Path) -> list[Path]:
    """``root/2024/q3/a.pdf``, ``root/2024/q3/b.pdf`` plus a non-PDF and a temp file."""
    folder = root / "2024" / "q3"
    folder.mkdir(parents=True, exist_ok=True)
    first = build_tiny_pdf(folder / "a.pdf")
    second = build_tiny_pdf(folder / "b.pdf")
    (folder / "notes.txt").write_text("not a pdf", encoding="utf-8")
    (folder / ".ozempdf_stale.tmp.pdf").write_bytes(b"tmp")
    return [first, second]
