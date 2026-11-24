"""Stage 5a: font-program subsetting and recompression.

Text in a PDF content stream addresses glyphs, not characters.  For an embedded
TrueType program referenced through a ``/FontDescriptor /FontFile2`` entry the
content stream's strings carry character codes that, via ``/CIDToGIDMap
/Identity``, are used as glyph indices (GIDs) directly.  Subsetting therefore
has to run with ``retain_gids=True`` (FR-FNT-1): dropping or renumbering glyphs
would leave every text run pointing at the wrong glyph.

The pass collects the codes that the document actually shows, subsets
``/FontFile2`` and ``/FontFile3`` programs with fontTools using the option table
from §4.7.1, Zopfli-compresses the result, and writes it only when the encoded
form is strictly smaller.  ``/ToUnicode`` CMaps are recompressed too and counted
separately (D-17).  Every transformation degrades to the original bytes on any
error or when the candidate is not smaller.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pikepdf

try:  # FR-FNT-2: without fontTools the pass degrades to Zopfli-only compression.
    from fontTools import subset as _fonttools_subset
except ImportError:  # pragma: no cover - exercised only on minimal installs
    _fonttools_subset = None

from ..config import CompressionConfig
from .unlock import zopfli_compress

#: Operators whose string operands name glyphs shown on the page (§4.7.1 step 1).
_TEXT_SHOW_OPERATORS: frozenset[str] = frozenset({"Tj", "TJ", "'", '"'})

#: Codes that are always retained: GID 0 is ``.notdef`` and code 32 is the space
#: most producers emit even when no literal space appears in a decoded string.
_SEED_CODES: frozenset[int] = frozenset({0, 32})

#: Below this glyph count subsetting costs more than it saves (§4.7.1).
_MIN_SUBSET_GLYPHS = 64

#: Font-program entries on a ``/FontDescriptor``.
_FONT_PROGRAM_KEYS: tuple[str, ...] = ("/FontFile", "/FontFile2", "/FontFile3")

#: Only the sfnt formats fontTools can subset; ``/FontFile`` is Type1/PFB.
_SUBSETTABLE_KEYS: frozenset[str] = frozenset({"/FontFile2", "/FontFile3"})

#: Tables that never affect PDF text rendering (§4.7.1).  Note ``"SVG "`` keeps
#: its trailing space: PDF/OpenType table tags are exactly four bytes.
_SUBSET_DROP_TABLES: tuple[str, ...] = (
    "DSIG",
    "GPOS",
    "GSUB",
    "GDEF",
    "kern",
    "hdmx",
    "VDMX",
    "LTSH",
    "PCLT",
    "BASE",
    "JSTF",
    "MATH",
    "SVG ",
    "CBDT",
    "CBLC",
    "COLR",
    "CPAL",
    "sbix",
)


@dataclass(slots=True)
class FontOptimizationStats:
    """Observable effects of Stage 5a.

    Attributes:
        font_streams_inspected: Font-program streams read (one per unique
            stream, however many fonts share it).
        font_streams_optimized: Font-program streams written back smaller.
        cmaps_optimized: ``/ToUnicode`` CMaps recompressed smaller.  Separate
            from :attr:`font_streams_optimized` so font counts do not conflate
            CMaps with programs (D-17).
        bytes_saved: Raw bytes removed by every successful rewrite.
    """

    font_streams_inspected: int = 0
    font_streams_optimized: int = 0
    cmaps_optimized: int = 0
    bytes_saved: int = 0

    @property
    def fonts_subsetted(self) -> int:
        """Number of font programs that were rewritten.

        Returns:
            :attr:`font_streams_optimized`, exposed under the name used by the
            report schema and the pipeline.
        """

        return self.font_streams_optimized


def optimize_fonts(pdf: pikepdf.Pdf, config: CompressionConfig) -> FontOptimizationStats:
    """Subset and Zopfli-compress every font program reachable from *pdf*.

    The pass runs in the order of §4.7.1: first collect the glyph codes the
    content streams reference, then rewrite ``/FontFile``, ``/FontFile2`` and
    ``/FontFile3`` streams that get smaller, then recompress ``/ToUnicode``
    CMaps.  The document is mutated in place; nothing is written when the
    candidate encoding is not strictly smaller.

    Args:
        pdf: Open document, mutated in place.
        config: Compression knobs (``zopfli_iterations`` is the only one used;
            ``subset_fonts`` is checked by the pipeline before this stage).

    Returns:
        Counters and byte savings for the file report.
    """

    stats = FontOptimizationStats()
    used_codes, parsed_cleanly = _collect_referenced_codes(pdf)
    optimizer = _FontOptimizer(config, used_codes, parsed_cleanly, stats)

    for resources in _iter_resources(pdf):
        optimizer.visit_resources(resources)
    return stats


class _FontOptimizer:
    """Per-document state for the font rewrite pass."""

    def __init__(
        self,
        config: CompressionConfig,
        used_codes: set[int],
        parsed_cleanly: bool,
        stats: FontOptimizationStats,
    ) -> None:
        """Initialise the rewrite pass.

        Args:
            config: Compression knobs; supplies ``zopfli_iterations``.
            used_codes: Glyph codes collected from the content streams.
            parsed_cleanly: False when some content stream could not be parsed,
                which disables glyph selection so no glyph can be lost.
            stats: Mutable counters updated in place.
        """

        self._config = config
        self._used_codes = used_codes
        self._parsed_cleanly = parsed_cleanly
        self._stats = stats
        self._seen_programs: set[tuple[int, int]] = set()
        self._seen_cmaps: set[tuple[int, int]] = set()
        self._seen_fonts: set[tuple[int, int]] = set()

    def visit_resources(self, resources: pikepdf.Dictionary) -> None:
        """Process every font in a page or Form XObject resource dictionary.

        Args:
            resources: A ``/Resources`` dictionary.
        """

        fonts = _get(resources, "/Font")
        if not isinstance(fonts, pikepdf.Dictionary):
            return
        # pikepdf dictionary iteration order is not guaranteed; sorting makes a
        # run's counters and canonical choices reproducible.
        for key in sorted(fonts.keys()):
            font = _get(fonts, key)
            if isinstance(font, pikepdf.Dictionary):
                self.process_font(font)

    def process_font(self, font: pikepdf.Dictionary) -> None:
        """Rewrite the programs and CMap of one font dictionary.

        CID fonts keep their program on a descendant font's descriptor, so both
        the font's own ``/FontDescriptor`` and any ``/DescendantFonts``
        descriptors are visited.

        Args:
            font: ``/Type /Font`` dictionary.
        """

        font_id = _objgen(font)
        if font_id is not None:
            if font_id in self._seen_fonts:
                return
            self._seen_fonts.add(font_id)

        for descriptor in _descriptors(font):
            for key in _FONT_PROGRAM_KEYS:
                program = _get(descriptor, key)
                if isinstance(program, pikepdf.Stream):
                    self._optimize_program(key, program)

        cmap = _get(font, "/ToUnicode")
        if isinstance(cmap, pikepdf.Stream):
            self._optimize_cmap(cmap)

    def _optimize_program(self, key: str, program: pikepdf.Stream) -> None:
        """Subset and recompress one font-program stream.

        Args:
            key: Descriptor entry that holds the stream (``/FontFile``,
                ``/FontFile2`` or ``/FontFile3``).
            program: Font-program stream.
        """

        program_id = _objgen(program)
        if program_id is not None:
            if program_id in self._seen_programs:
                return
            self._seen_programs.add(program_id)

        try:
            raw_length = len(program.read_raw_bytes())
            decoded = program.read_bytes()
        except Exception:
            return
        if raw_length <= 0 or not decoded:
            return
        self._stats.font_streams_inspected += 1

        payload = decoded
        if key in _SUBSETTABLE_KEYS:
            payload = _subset_and_compact_sfnt(decoded, self._used_codes, self._parsed_cleanly)
        try:
            compressed = zopfli_compress(payload, self._config.zopfli_iterations)
        except Exception:
            return
        if len(compressed) >= raw_length:
            return

        try:
            program.write(compressed, filter=pikepdf.Name("/FlateDecode"))
            if "/DecodeParms" in program:
                del program["/DecodeParms"]
            if key == "/FontFile2":
                # /Length1 is the *decoded* length of the sfnt program; it must
                # describe the subset bytes, not the original file.
                program["/Length1"] = len(payload)
        except Exception:
            return

        self._stats.font_streams_optimized += 1
        self._stats.bytes_saved += raw_length - len(compressed)

    def _optimize_cmap(self, cmap: pikepdf.Stream) -> None:
        """Zopfli-recompress one ``/ToUnicode`` CMap stream.

        Args:
            cmap: CMap stream referenced by a font's ``/ToUnicode`` entry.
        """

        cmap_id = _objgen(cmap)
        if cmap_id is not None:
            if cmap_id in self._seen_cmaps:
                return
            self._seen_cmaps.add(cmap_id)

        try:
            raw_length = len(cmap.read_raw_bytes())
            decoded = cmap.read_bytes()
        except Exception:
            return
        if raw_length <= 0 or not decoded:
            return

        try:
            compressed = zopfli_compress(decoded, self._config.zopfli_iterations)
        except Exception:
            return
        if len(compressed) >= raw_length:
            return

        try:
            cmap.write(compressed, filter=pikepdf.Name("/FlateDecode"))
            if "/DecodeParms" in cmap:
                del cmap["/DecodeParms"]
        except Exception:
            return

        # D-17: CMaps have their own counter; they are not font programs.
        self._stats.cmaps_optimized += 1
        self._stats.bytes_saved += raw_length - len(compressed)


def _subset_and_compact_sfnt(
    data: bytes,
    used_codes: set[int],
    parsed_cleanly: bool,
) -> bytes:
    """Subset an sfnt font program with fontTools (§4.7.1).

    ``retain_gids=True`` is load-bearing (FR-FNT-1): content streams address
    glyphs by index, so the subset must keep every retained glyph at its
    original GID.  When the content streams parsed cleanly, have codes and the
    program has more than 64 glyphs, only the used GIDs (and their Unicode
    values) are kept; otherwise every GID is kept and only tables are stripped.

    Args:
        data: Decoded font-program bytes.
        used_codes: Codes collected from ``Tj``, ``TJ``, ``'`` and ``"``.
        parsed_cleanly: False when a content stream failed to parse.

    Returns:
        The compacted sfnt, or *data* unchanged when fontTools is missing, an
        exception occurs, or the result is not strictly smaller.
    """

    if _fonttools_subset is None:
        return data

    try:
        options = _fonttools_subset.Options()
        options.retain_gids = True
        options.notdef_outline = True
        options.glyph_names = False
        options.legacy_kern = False
        options.layout_features = []
        options.name_IDs = [1, 2, 6]
        options.name_legacy = True
        options.name_languages = [0x0409]
        options.drop_tables = list(_SUBSET_DROP_TABLES)

        font = _fonttools_subset.load_font(io.BytesIO(data), options, dontLoadGlyphNames=True)
        num_glyphs = len(font.getGlyphOrder())
        subsetter = _fonttools_subset.Subsetter(options=options)
        if parsed_cleanly and used_codes and num_glyphs > _MIN_SUBSET_GLYPHS:
            # Codes are GID-addressed (CIDToGIDMap /Identity), so both the GID
            # list and the Unicode set are populated from the same codes.
            gids = sorted(code for code in used_codes if 0 <= code < num_glyphs)
            subsetter.populate(gids=gids, unicodes=used_codes)
        else:
            subsetter.populate(gids=range(num_glyphs))
        subsetter.subset(font)

        buffer = io.BytesIO()
        _fonttools_subset.save_font(font, buffer, options)
        compacted = buffer.getvalue()
    except Exception:
        return data
    return compacted if 0 < len(compacted) < len(data) else data


def _collect_referenced_codes(pdf: pikepdf.Pdf) -> tuple[set[int], bool]:
    """Collect the glyph codes shown by page and Form XObject content streams.

    For every ``Tj``, ``'`` and ``"`` string each byte is added; when the string
    length is even each big-endian 16-bit value is added as well (§4.7.1).  For
    ``TJ`` the same rule applies to every string inside the array.

    Args:
        pdf: Open document to scan; not modified.

    Returns:
        Tuple of the collected codes and a flag that is False when any content
        stream failed to parse.
    """

    codes: set[int] = set(_SEED_CODES)
    parsed_cleanly = True

    for page in _safe_pages(pdf):
        try:
            for instruction in pikepdf.parse_content_stream(page):
                _add_instruction_codes(instruction, codes)
        except Exception:
            # A malformed stream means glyph selection is unsafe; the caller
            # then keeps every GID instead of guessing.
            parsed_cleanly = False

    # Glyphs can be shown from a Form XObject too, and a form may use fonts the
    # page itself never references.
    seen_forms: set[tuple[int, int]] = set()
    for resources in _iter_resources(pdf):
        xobjects = _get(resources, "/XObject")
        if not isinstance(xobjects, pikepdf.Dictionary):
            continue
        for key in sorted(xobjects.keys()):
            entry = _get(xobjects, key)
            if not isinstance(entry, pikepdf.Stream):
                continue
            if _name(_get(entry, "/Subtype")) != "/Form":
                continue
            form_id = _objgen(entry)
            if form_id is not None:
                if form_id in seen_forms:
                    continue
                seen_forms.add(form_id)
            try:
                for instruction in pikepdf.parse_content_stream(entry):
                    _add_instruction_codes(instruction, codes)
            except Exception:
                parsed_cleanly = False
    return codes, parsed_cleanly


def _add_instruction_codes(instruction: Any, codes: set[int]) -> None:
    """Add the glyph codes of one content-stream instruction to *codes*.

    Args:
        instruction: ``pikepdf.ContentStreamInstruction`` from the parser.
        codes: Accumulator for the collected codes.
    """

    try:
        if str(instruction.operator) not in _TEXT_SHOW_OPERATORS:
            return
    except Exception:
        return

    for operand in instruction.operands:
        if isinstance(operand, pikepdf.String):
            _add_string_codes(bytes(operand), codes)
        elif isinstance(operand, pikepdf.Array):
            for element in operand:
                if isinstance(element, pikepdf.String):
                    _add_string_codes(bytes(element), codes)


def _add_string_codes(data: bytes, codes: set[int]) -> None:
    """Add the single-byte and, for even-length strings, 16-bit codes of *data*.

    Args:
        data: Decoded string operand from a text-showing operator.
        codes: Accumulator for the collected codes.
    """

    for byte in data:
        codes.add(byte)
    if len(data) % 2 == 0:
        for index in range(0, len(data), 2):
            codes.add((data[index] << 8) | data[index + 1])


def _descriptors(font: pikepdf.Dictionary) -> Iterator[pikepdf.Dictionary]:
    """Yield the font descriptors that can hold an embedded program.

    Args:
        font: ``/Type /Font`` dictionary, possibly a composite font.

    Yields:
        The font's own descriptor, then each ``/DescendantFonts`` descriptor.
    """

    descriptor = _get(font, "/FontDescriptor")
    if isinstance(descriptor, pikepdf.Dictionary):
        yield descriptor

    descendants = _get(font, "/DescendantFonts")
    if isinstance(descendants, pikepdf.Array):
        for descendant in descendants:
            if not isinstance(descendant, pikepdf.Dictionary):
                continue
            descendant_descriptor = _get(descendant, "/FontDescriptor")
            if isinstance(descendant_descriptor, pikepdf.Dictionary):
                yield descendant_descriptor


def _iter_resources(pdf: pikepdf.Pdf) -> Iterator[pikepdf.Dictionary]:
    """Yield every page and Form XObject ``/Resources`` dictionary once.

    Forms can reference themselves or be shared between pages, so both the
    resources object numbers and the form object numbers are tracked.

    Args:
        pdf: Open document to walk.

    Yields:
        Resource dictionaries in page order, then their nested form resources.
    """

    visited_resources: set[tuple[int, int]] = set()
    visited_forms: set[tuple[int, int]] = set()
    for page in _safe_pages(pdf):
        resources = _page_resources(page)
        if resources is not None:
            yield from _walk_resources(resources, visited_resources, visited_forms)


def _walk_resources(
    resources: pikepdf.Dictionary,
    visited_resources: set[tuple[int, int]],
    visited_forms: set[tuple[int, int]],
) -> Iterator[pikepdf.Dictionary]:
    """Yield *resources* and the resources of every nested Form XObject.

    Args:
        resources: Resource dictionary to visit.
        visited_resources: Object numbers of resource dictionaries already seen.
        visited_forms: Object numbers of forms already entered (cycle guard).

    Yields:
        *resources*, then each reachable form's resources.
    """

    resources_id = _objgen(resources)
    if resources_id is not None:
        if resources_id in visited_resources:
            return
        visited_resources.add(resources_id)
    yield resources

    xobjects = _get(resources, "/XObject")
    if not isinstance(xobjects, pikepdf.Dictionary):
        return
    for key in sorted(xobjects.keys()):
        entry = _get(xobjects, key)
        if not isinstance(entry, pikepdf.Stream):
            continue
        if _name(_get(entry, "/Subtype")) != "/Form":
            continue
        form_id = _objgen(entry)
        if form_id is not None:
            if form_id in visited_forms:
                continue
            visited_forms.add(form_id)
        form_resources = _get(entry, "/Resources")
        if isinstance(form_resources, pikepdf.Dictionary):
            yield from _walk_resources(form_resources, visited_resources, visited_forms)


def _safe_pages(pdf: pikepdf.Pdf) -> list[pikepdf.Page]:
    """Return the page list, or an empty list when the page tree is unreadable.

    Args:
        pdf: Open document.

    Returns:
        List of pages; empty when qpdf cannot resolve the page tree.
    """

    try:
        return list(pdf.pages)
    except Exception:
        return []


def _page_resources(page: pikepdf.Page) -> pikepdf.Dictionary | None:
    """Return a page's resource dictionary, resolving inherited ``/Resources``.

    ``/Resources`` is an inheritable page attribute: a producer may store it on
    an intermediate ``/Pages`` node instead of on every page.  The parent chain
    is walked until a dictionary is found, with a guard against malformed trees
    that loop.

    Args:
        page: Page to inspect.

    Returns:
        The effective resource dictionary, or None when the page has none.
    """

    seen: set[tuple[int, int]] = set()
    try:
        node = page.obj
    except Exception:
        return None
    while isinstance(node, pikepdf.Dictionary):
        node_id = _objgen(node)
        if node_id is not None:
            if node_id in seen:
                return None
            seen.add(node_id)
        resources = _get(node, "/Resources")
        if isinstance(resources, pikepdf.Dictionary):
            return resources
        node = _get(node, "/Parent")
    return None


def _get(container: pikepdf.Object, key: str | int) -> pikepdf.Object | None:
    """Return an entry from a dictionary or array, or None when unavailable.

    Args:
        container: Dictionary or array to read.
        key: Name key for dictionaries, zero-based index for arrays.

    Returns:
        The stored object, or None when the lookup fails.
    """

    try:
        if isinstance(key, int):
            return container[key] if key < len(container) else None
        return container.get(key)
    except Exception:
        return None


def _name(value: pikepdf.Object | None) -> str | None:
    """Return a PDF name object's text (e.g. ``"/Form"``), else None.

    Args:
        value: Candidate name object.

    Returns:
        The name text including the leading slash, or None.
    """

    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


def _objgen(obj: pikepdf.Object) -> tuple[int, int] | None:
    """Return the PDF object number and generation of *obj*.

    Args:
        obj: Candidate object.

    Returns:
        ``(num, gen)`` for an indirect object, else None for direct objects.
    """

    try:
        return obj.objgen if obj.is_indirect else None
    except Exception:
        return None
