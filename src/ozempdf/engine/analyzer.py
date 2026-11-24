"""Byte accounting and effective-image-DPI analysis for OzemPDF.

This module is Stage 2 of the compression pipeline (specification §4.4).
:func:`audit_pdf_bytes` attributes a PDF's bytes to the four user-facing
categories that the inspector and the batch report display, and
:func:`calculate_effective_image_dpis` recovers the resolution at which each
image XObject is actually painted on a page.

Both analyses are read-only: they never mutate the document, so they can run
on the input before any optimisation stage touches it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from math import hypot
from typing import Final

import pikepdf

_IMAGES: Final = "images_bytes"
_FONTS: Final = "fonts_bytes"
_VECTORS: Final = "vectors_bytes"
_METADATA: Final = "metadata_bytes"

_BUCKETS: Final[tuple[str, ...]] = (_IMAGES, _FONTS, _VECTORS, _METADATA)

_FONT_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"/Font1", "/Type1C", "/CIDFontType0C", "/OpenType"}
)
_FONT_FILE_KEYS: Final[tuple[str, ...]] = ("/FontFile", "/FontFile2", "/FontFile3")
_FONT_LENGTH_KEYS: Final[tuple[str, ...]] = ("/Length1", "/Length2", "/Length3")

# (a, b, c, d, e, f) as stored by PDF matrices and by the cm / Matrix operators.
Matrix = tuple[float, float, float, float, float, float]
# (object number, generation) pairs, as returned by pikepdf.Object.objgen.
ObjectKey = tuple[int, int]

_IDENTITY: Final[Matrix] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_MIN_RENDERED_SIDE_PT: Final = 0.5
_MAX_RESOURCE_INHERITANCE: Final = 64


@dataclass(slots=True)
class CategoryBreakdown:
    """Bytes attributed to each user-facing category plus the file total.

    Attributes:
        images_bytes: Image XObjects, including their ``/SMask`` and ``/Mask``
            streams.
        fonts_bytes: Embedded font programs, ``/ToUnicode`` CMaps and font
            program streams.
        vectors_bytes: Page content streams and ``/Subtype /Form`` XObjects.
        metadata_bytes: Metadata streams, every unclassified stream and the
            container overhead (xref table, object dictionaries, trailer)
            that stream payloads alone cannot account for.
        total_bytes: Whole-file size; always equal to the sum of the four
            category counters produced by :func:`audit_pdf_bytes`.
    """

    images_bytes: int = 0
    fonts_bytes: int = 0
    vectors_bytes: int = 0
    metadata_bytes: int = 0
    total_bytes: int = 0

    def __add__(self, other: CategoryBreakdown) -> CategoryBreakdown:
        """Return the aggregate of ``self`` and ``other``.

        Args:
            other: The breakdown to add. Neither operand is modified.

        Returns:
            A new breakdown whose counters are the sums of both operands and
            whose ``total_bytes`` is recomputed from the category counters.
        """
        combined = CategoryBreakdown.empty()
        combined.add_inplace(self)
        combined.add_inplace(other)
        return combined

    def add_inplace(self, other: CategoryBreakdown) -> None:
        """Accumulate ``other`` into this breakdown.

        Used by ``BatchSummary`` to fold per-file audits into one total.

        Args:
            other: The breakdown to accumulate. It is not modified.
        """
        self.images_bytes += other.images_bytes
        self.fonts_bytes += other.fonts_bytes
        self.vectors_bytes += other.vectors_bytes
        self.metadata_bytes += other.metadata_bytes
        self.total_bytes = (
            self.images_bytes
            + self.fonts_bytes
            + self.vectors_bytes
            + self.metadata_bytes
        )

    def to_dict(self) -> dict[str, int]:
        """Return the breakdown as a plain JSON-serialisable dictionary."""
        return {
            "images_bytes": self.images_bytes,
            "fonts_bytes": self.fonts_bytes,
            "vectors_bytes": self.vectors_bytes,
            "metadata_bytes": self.metadata_bytes,
            "total_bytes": self.total_bytes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CategoryBreakdown:
        """Rebuild a breakdown from :meth:`to_dict` output.

        Unknown keys are ignored and unusable values fall back to ``0`` so a
        stale or hand-edited report can never crash the inspector.

        Args:
            data: Mapping with the five counter names.

        Returns:
            The rebuilt breakdown.
        """
        return cls(
            images_bytes=_as_int(data.get("images_bytes")),
            fonts_bytes=_as_int(data.get("fonts_bytes")),
            vectors_bytes=_as_int(data.get("vectors_bytes")),
            metadata_bytes=_as_int(data.get("metadata_bytes")),
            total_bytes=_as_int(data.get("total_bytes")),
        )

    @classmethod
    def empty(cls) -> CategoryBreakdown:
        """Return an all-zero breakdown, the identity for :meth:`__add__`."""
        return cls()


def audit_pdf_bytes(pdf: pikepdf.Pdf, file_size: int | None = None) -> CategoryBreakdown:
    """Attribute a PDF's bytes to the four user-facing categories.

    Implements the three passes of specification §4.4.1: role discovery from
    the page tree, object classification, then summation over ``pdf.objects``.

    Pass 1 records a stream's *role* before anything else looks at its stream
    dictionary: a page ``/Contents`` stream is vectors and a page ``/Thumb`` is
    metadata even when it also carries ``/Subtype /Image`` (defect D-16).

    Args:
        pdf: The opened source document. It is not modified.
        file_size: Size of the serialised file in bytes. When supplied, the
            container overhead that no stream owns is added to
            ``metadata_bytes`` so the categories sum exactly to this value.

    Returns:
        The populated breakdown. ``total_bytes`` equals the sum of the four
        category counters; it equals ``file_size`` whenever that argument is
        at least the measured stream payload.
    """
    roles: dict[ObjectKey, str] = {}
    direct: dict[str, int] = {bucket: 0 for bucket in _BUCKETS}

    _discover_page_roles(pdf, roles, direct)
    _classify_objects(pdf, roles, direct)

    totals = dict(direct)
    seen: set[ObjectKey] = set()
    for obj in _iter_objects(pdf):
        if not isinstance(obj, pikepdf.Stream):
            continue
        key = obj.objgen
        if key == (0, 0) or key in seen:
            continue
        seen.add(key)
        totals[roles.get(key, _METADATA)] += _stream_size(obj)

    stream_total = sum(totals.values())
    total = stream_total
    if file_size is not None and file_size > stream_total:
        # Stream payloads never cover the xref table, object dictionaries or
        # the trailer, so the rest of the file is metadata overhead.
        totals[_METADATA] += file_size - stream_total
        total = file_size

    return CategoryBreakdown(
        images_bytes=totals[_IMAGES],
        fonts_bytes=totals[_FONTS],
        vectors_bytes=totals[_VECTORS],
        metadata_bytes=totals[_METADATA],
        total_bytes=total,
    )


def calculate_effective_image_dpis(pdf: pikepdf.Pdf) -> dict[tuple[int, int], float]:
    """Return the effective DPI at which every image XObject is painted.

    Implements specification §4.4.2. Each page's content stream is replayed
    with a CTM stack, so the result reflects the transform in force at each
    ``Do`` operator rather than the image's pixel dimensions alone. Form
    XObjects are entered with their composed ``/Matrix`` and own resources.

    Pages whose content stream cannot be parsed are skipped; inline images
    (``BI … ID … EI``) are outside the analysis and ignored.

    Args:
        pdf: The opened source document. It is not modified.

    Returns:
        Mapping ``(object number, generation) -> maximum effective DPI``. The
        maximum is kept when one image is painted at several sizes or on
        several pages. Direct image streams share the ``(0, 0)`` key.
    """
    dpis: dict[ObjectKey, float] = {}
    try:
        pages = list(pdf.pages)
    except Exception:
        return dpis
    for page in pages:
        try:
            instructions = pikepdf.parse_content_stream(page)
            resources = _inherited_resources(page.obj)
            _walk_content(instructions, resources, _IDENTITY, dpis, set())
        except Exception:
            continue
    return dpis


def _discover_page_roles(
    pdf: pikepdf.Pdf,
    roles: dict[ObjectKey, str],
    direct: dict[str, int],
) -> None:
    """Pass 1: record the category dictated by each stream's role on a page.

    Page ``/Contents`` streams are vectors and page ``/Thumb`` streams are
    metadata, regardless of the stream's own ``/Subtype``.

    Args:
        pdf: The opened source document.
        roles: Per-object category map, updated in place. First role wins.
        direct: Direct-stream byte totals per category, updated in place.
    """
    try:
        pages = list(pdf.pages)
    except Exception:
        return
    for page in pages:
        try:
            page_obj = page.obj
            for stream in _iter_streams(page_obj.get("/Contents")):
                _mark(stream, _VECTORS, roles, direct)
            _mark(page_obj.get("/Thumb"), _METADATA, roles, direct)
        except Exception:
            continue


def _classify_objects(
    pdf: pikepdf.Pdf,
    roles: dict[ObjectKey, str],
    direct: dict[str, int],
) -> None:
    """Pass 2: classify every object of the object table and its references.

    Font descriptors, font dictionaries, image and mask streams, form
    XObjects and metadata streams are given a category. Role assignments from
    pass 1 are never overwritten.

    Args:
        pdf: The opened source document.
        roles: Per-object category map, updated in place.
        direct: Direct-stream byte totals per category, updated in place.
    """
    for obj in _iter_objects(pdf):
        try:
            if isinstance(obj, pikepdf.Stream):
                _classify_stream_object(obj, roles, direct)
            elif isinstance(obj, pikepdf.Dictionary):
                _classify_font_references(obj, roles, direct)
        except Exception:
            continue


def _classify_stream_object(
    stream: pikepdf.Stream,
    roles: dict[ObjectKey, str],
    direct: dict[str, int],
) -> None:
    """Classify one stream and, for images, its ``/SMask`` and ``/Mask`` streams.

    Args:
        stream: The stream object to classify.
        roles: Per-object category map, updated in place.
        direct: Direct-stream byte totals per category, updated in place.
    """
    bucket = _bucket_for_stream(stream)
    if bucket is not None:
        _mark(stream, bucket, roles, direct)
    if _text(stream.get("/Subtype")) == "/Image":
        _mark(stream.get("/SMask"), _IMAGES, roles, direct)
        _mark(stream.get("/Mask"), _IMAGES, roles, direct)


def _bucket_for_stream(stream: pikepdf.Stream) -> str | None:
    """Return the fallback category of a stream from its dictionary alone.

    Args:
        stream: The stream object to inspect.

    Returns:
        The category name, or ``None`` when the stream is unclassified (the
        summation pass then files it under metadata).
    """
    subtype = _text(stream.get("/Subtype"))
    if subtype == "/Image":
        return _IMAGES
    if subtype in _FONT_SUBTYPES or any(key in stream for key in _FONT_LENGTH_KEYS):
        return _FONTS
    if subtype == "/Form":
        return _VECTORS
    if _text(stream.get("/Type")) == "/Metadata" or subtype == "/XML":
        return _METADATA
    return None


def _classify_font_references(
    obj: pikepdf.Dictionary,
    roles: dict[ObjectKey, str],
    direct: dict[str, int],
) -> None:
    """Mark the font program streams reachable from a font or descriptor dict.

    Handles ``/FontDescriptor /FontFile``, ``/FontFile2`` and ``/FontFile3``
    both when descending from a ``/Font`` and when the descriptor is visited
    as its own object table entry, plus ``/ToUnicode`` CMaps.

    Args:
        obj: A non-stream dictionary object.
        roles: Per-object category map, updated in place.
        direct: Direct-stream byte totals per category, updated in place.
    """
    descriptor = obj.get("/FontDescriptor")
    if isinstance(descriptor, pikepdf.Dictionary):
        for key in _FONT_FILE_KEYS:
            _mark(descriptor.get(key), _FONTS, roles, direct)
    for key in _FONT_FILE_KEYS:
        _mark(obj.get(key), _FONTS, roles, direct)
    _mark(obj.get("/ToUnicode"), _FONTS, roles, direct)


def _iter_objects(pdf: pikepdf.Pdf) -> Iterator[pikepdf.Object]:
    """Yield the object table of ``pdf``, tolerating an unreadable document."""
    try:
        objects = pdf.objects
    except Exception:
        return
    try:
        yield from objects
    except Exception:
        return


def _iter_streams(value: object) -> Iterator[pikepdf.Stream]:
    """Yield the streams held by a direct stream or by an array of streams."""
    if isinstance(value, pikepdf.Stream):
        yield value
        return
    if isinstance(value, pikepdf.Array):
        for item in value:
            if isinstance(item, pikepdf.Stream):
                yield item


def _mark(
    value: object,
    bucket: str,
    roles: dict[ObjectKey, str],
    direct: dict[str, int],
) -> None:
    """Attribute a stream to a category; non-stream references are ignored.

    Indirect streams are recorded in ``roles`` by object identity, with the
    first role winning. Direct streams are not object table entries, so their
    bytes are measured immediately into ``direct``.

    Args:
        value: Candidate stream reference.
        bucket: Category name to assign.
        roles: Per-object category map, updated in place.
        direct: Direct-stream byte totals per category, updated in place.
    """
    if not isinstance(value, pikepdf.Stream):
        return
    key = value.objgen
    if key == (0, 0):
        direct[bucket] += _stream_size(value)
        return
    roles.setdefault(key, bucket)


def _stream_size(stream: pikepdf.Stream) -> int:
    """Return a stream's stored payload size, falling back to decoded size.

    Args:
        stream: The stream to measure.

    Returns:
        Byte length of the stored (usually compressed) payload; the decoded
        length if the raw bytes are unavailable; otherwise ``0``.
    """
    try:
        return len(stream.read_raw_bytes())
    except Exception:
        pass
    try:
        return len(stream.read_bytes())
    except Exception:
        return 0


def _as_int(value: object) -> int:
    """Convert a report counter to ``int``, defaulting to ``0`` when unusable."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float | None:
    """Convert a PDF number to ``float``, or return ``None`` when unusable."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    """Return a PDF name/number as text, or an empty string when absent."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _inherited_resources(page_obj: pikepdf.Object) -> pikepdf.Object | None:
    """Return the effective ``/Resources`` of a page, following its parents.

    ``/Resources`` is an inheritable page-tree attribute, so a page without
    its own entry uses the nearest ancestor that defines one.

    Args:
        page_obj: The page dictionary.

    Returns:
        The resource dictionary, or ``None`` when the page tree defines none.
    """
    node = page_obj
    for _ in range(_MAX_RESOURCE_INHERITANCE):
        try:
            resources = node.get("/Resources")
            parent = node.get("/Parent")
        except Exception:
            return None
        if resources is not None:
            return resources
        if parent is None:
            return None
        node = parent
    return None


def _walk_content(
    instructions: Sequence[pikepdf.ContentStreamInstruction],
    resources: object,
    ctm: Matrix,
    dpis: dict[ObjectKey, float],
    visited_forms: set[ObjectKey],
) -> None:
    """Replay content-stream operators while tracking the CTM stack.

    ``q`` saves the current matrix, ``Q`` restores it (never below the initial
    matrix), ``cm`` post-multiplies the new matrix onto the current one and
    ``Do`` paints an image or enters a form XObject.

    Args:
        instructions: Parsed content-stream instructions.
        resources: Resource dictionary in force for these instructions.
        ctm: Transform in force when the stream starts.
        dpis: Accumulated effective DPI per image, updated in place.
        visited_forms: Form XObjects already entered on this page.
    """
    stack: list[Matrix] = [ctm]
    for instruction in instructions:
        operator = _text(instruction.operator)
        if operator == "q":
            stack.append(stack[-1])
        elif operator == "Q":
            if len(stack) > 1:
                stack.pop()
        elif operator == "cm":
            matrix = _matrix_from(instruction.operands)
            if matrix is not None:
                stack[-1] = _concat_matrix(matrix, stack[-1])
        elif operator == "Do":
            _paint_xobject(instruction.operands, resources, stack[-1], dpis, visited_forms)


def _paint_xobject(
    operands: Sequence[object],
    resources: object,
    ctm: Matrix,
    dpis: dict[ObjectKey, float],
    visited_forms: set[ObjectKey],
) -> None:
    """Handle a ``Do`` operator for an image or form XObject.

    Args:
        operands: The operator's operands, the first being the XObject name.
        resources: Resource dictionary used to resolve the name.
        ctm: Transform in force at the ``Do``.
        dpis: Accumulated effective DPI per image, updated in place.
        visited_forms: Form XObjects already entered on this page.
    """
    if not operands:
        return
    xobject = _lookup_xobject(resources, _text(operands[0]))
    if xobject is None:
        return
    subtype = _text(xobject.get("/Subtype"))
    if subtype == "/Image":
        _record_image_dpi(xobject, ctm, dpis)
    elif subtype == "/Form":
        _descend_form(xobject, resources, ctm, dpis, visited_forms)


def _lookup_xobject(resources: object, name: str) -> pikepdf.Stream | None:
    """Resolve a ``Do`` operand name in ``/Resources /XObject``.

    Args:
        resources: Resource dictionary in force.
        name: The XObject name, including its leading slash.

    Returns:
        The referenced stream, or ``None`` when it is absent or not a stream.
    """
    if not isinstance(resources, pikepdf.Dictionary):
        return None
    xobjects = resources.get("/XObject")
    if not isinstance(xobjects, pikepdf.Dictionary):
        return None
    try:
        candidate = xobjects.get(name)
    except Exception:
        return None
    return candidate if isinstance(candidate, pikepdf.Stream) else None


def _record_image_dpi(
    image: pikepdf.Stream,
    ctm: Matrix,
    dpis: dict[ObjectKey, float],
) -> None:
    """Compute one image's effective DPI under ``ctm`` and keep the maximum.

    The image's unit square is mapped through the CTM, so the lengths of the
    matrix's first two column vectors ``(a, b)`` and ``(c, d)`` are the
    painted width and height in points. Pixel dimensions divided by those
    lengths in inches give the two axis resolutions; the effective DPI is the
    larger one, since that is what the viewer actually renders.

    Args:
        image: The image XObject being painted.
        ctm: Transform in force at the ``Do``.
        dpis: Accumulated effective DPI per image, updated in place.
    """
    width_px = _as_float(image.get("/Width"))
    height_px = _as_float(image.get("/Height"))
    if width_px is None or height_px is None or width_px <= 0 or height_px <= 0:
        return
    a, b, c, d = ctm[0], ctm[1], ctm[2], ctm[3]
    width_pt = hypot(a, b)
    height_pt = hypot(c, d)
    if width_pt <= _MIN_RENDERED_SIDE_PT or height_pt <= _MIN_RENDERED_SIDE_PT:
        return
    dpi_x = width_px / (width_pt / 72.0)
    dpi_y = height_px / (height_pt / 72.0)
    effective = max(dpi_x, dpi_y)
    key = image.objgen
    if effective > dpis.get(key, 0.0):
        dpis[key] = effective


def _descend_form(
    form: pikepdf.Stream,
    parent_resources: object,
    ctm: Matrix,
    dpis: dict[ObjectKey, float],
    visited_forms: set[ObjectKey],
) -> None:
    """Recurse into a Form XObject's content stream.

    The form's ``/Matrix`` is composed with the current CTM; its own
    ``/Resources`` take precedence, falling back to the parent resources. A
    per-page visited set stops self-referencing forms from recursing forever.

    Args:
        form: The form XObject being entered.
        parent_resources: Resources in force before entering the form.
        ctm: Transform in force at the ``Do``.
        dpis: Accumulated effective DPI per image, updated in place.
        visited_forms: Form XObjects already entered on this page.
    """
    key = form.objgen
    if key != (0, 0):
        if key in visited_forms:
            return
        visited_forms.add(key)
    matrix = _matrix_from(form.get("/Matrix"))
    form_ctm = _concat_matrix(matrix, ctm) if matrix is not None else ctm
    try:
        form_resources = form.get("/Resources")
    except Exception:
        form_resources = None
    if form_resources is None:
        form_resources = parent_resources
    try:
        instructions = pikepdf.parse_content_stream(form)
    except Exception:
        return
    _walk_content(instructions, form_resources, form_ctm, dpis, visited_forms)


def _matrix_from(value: object) -> Matrix | None:
    """Read a six-number PDF matrix, or return ``None`` when malformed.

    Args:
        value: Matrix operand (``cm`` operands or a ``/Matrix`` array).

    Returns:
        The matrix as a tuple, or ``None`` if it does not hold six numbers.
    """
    try:
        items = list(value)  # type: ignore[call-overload]
    except TypeError:
        return None
    if len(items) != 6:
        return None
    numbers: list[float] = []
    for item in items:
        number = _as_float(item)
        if number is None:
            return None
        numbers.append(number)
    return (numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5])


def _concat_matrix(left: Matrix, right: Matrix) -> Matrix:
    """Return ``left × right`` for PDF matrices in row-vector convention.

    PDF stores the matrix as rows ``[a b 0] [c d 0] [e f 1]`` and points are
    row vectors, so the product of the new ``cm`` matrix (left) with the
    current CTM (right) expands to::

        [a1 b1 0]   [a2 b2 0]   [a1a2+b1c2  a1b2+b1d2  0]
        [c1 d1 0] × [c2 d2 0] = [c1a2+d1c2  c1b2+d1d2  0]
        [e1 f1 1]   [e2 f2 1]   [e1a2+f1c2+e2  e1b2+f1d2+f2  1]

    Args:
        left: The matrix applied second (a ``cm`` operand or form ``/Matrix``).
        right: The current CTM.

    Returns:
        The composed matrix.
    """
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )
