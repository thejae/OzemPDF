"""Stage 3: merge byte-identical resource streams.

PDF resources are shared *by reference*: a page ``/Resources /XObject`` entry, an
``[/ICCBased ...]`` colour-space array, and a font descriptor's ``/FontFile*``
entry each hold a reference to a stream object.  Authoring tools routinely ship
the same image, ICC profile or font program several times under different object
numbers, so the same bytes are written once per copy.

This stage computes a SHA-256 signature per stream and re-points every later
duplicate's *dictionary entry* at the first (canonical) object.  Nothing is
deleted here (FR-DED-5): the displaced objects become unreachable and are
reclaimed by ``remove_unreferenced_resources()`` or the save-time garbage
collector.  The reported saving is therefore an estimate taken from the
displaced copies' raw bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import pikepdf

#: Stream dictionary entries that identify the decoded image payload (FR-DED-1).
#: Two images whose bytes match but whose geometry, colour space or decode array
#: differ are *not* interchangeable, so the metadata is part of the signature.
_IMAGE_METADATA_KEYS: tuple[str, ...] = (
    "/Width",
    "/Height",
    "/BitsPerComponent",
    "/ColorSpace",
    "/ImageMask",
    "/Decode",
)

#: Font program entries found on a ``/FontDescriptor`` (FR-DED-4).
_FONT_FILE_KEYS: tuple[str, ...] = ("/FontFile", "/FontFile2", "/FontFile3")

_IMAGE_SUBTYPE = "/Image"
_FORM_SUBTYPE = "/Form"
_ICC_BASED = "/ICCBased"

# A re-pointing callback: stores *canonical* in the duplicate's dictionary slot.
_RePointer = Callable[[pikepdf.Object], None]


@dataclass(slots=True)
class DeduplicationStats:
    """Observable effects of Stage 3.

    Attributes:
        duplicate_images_merged: Image entries re-pointed at an earlier,
            byte-identical image stream.
        duplicate_icc_merged: ``[/ICCBased ...]`` arrays re-pointed at an
            earlier, byte-identical ICC profile.
        duplicate_fonts_merged: ``/FontFile``, ``/FontFile2`` and
            ``/FontFile3`` entries re-pointed at an earlier font program.
        bytes_saved_estimate: Sum of ``read_raw_bytes()`` of the displaced
            duplicates; an estimate because save-time GC does the reclamation.
    """

    duplicate_images_merged: int = 0
    duplicate_icc_merged: int = 0
    duplicate_fonts_merged: int = 0
    bytes_saved_estimate: int = 0


def deduplicate_resources(pdf: pikepdf.Pdf) -> DeduplicationStats:
    """Merge duplicate resource streams in *pdf* in place.

    Every page ``/Resources`` graph is walked, recurring into ``/Subtype /Form``
    XObjects through their own ``/Resources`` (FR-DED-2).  Images (FR-DED-1),
    ICC profiles (FR-DED-3) and font programs (FR-DED-4) are signature-matched
    and later duplicates are re-pointed at the canonical stream.  Nothing is
    deleted; the caller's save-time GC reclaims the orphaned copies.

    Args:
        pdf: Open document, mutated in place.

    Returns:
        Counters and the estimated byte saving for the file report.
    """

    merger = _ResourceMerger(pdf)
    try:
        pages = pdf.pages
    except Exception:
        # A document whose page tree cannot be walked gains nothing here; the
        # later stages report their own failures.
        return merger.stats

    for page in pages:
        resources = _page_resources(page)
        if resources is not None:
            merger.visit_resources(resources)
    return merger.stats


class _ResourceMerger:
    """Signature registry and re-pointer for one document."""

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        """Initialise the per-document signature registries.

        Args:
            pdf: Document owning every stream seen by this merger.
        """

        self._pdf = pdf
        self.stats = DeduplicationStats()
        self._canonical_images: dict[bytes, pikepdf.Stream] = {}
        self._canonical_icc: dict[bytes, pikepdf.Stream] = {}
        self._canonical_fonts: dict[tuple[str, bytes], pikepdf.Stream] = {}
        self._displaced_images: set[tuple[int, int]] = set()
        self._displaced_icc: set[tuple[int, int]] = set()
        self._displaced_fonts: set[tuple[int, int]] = set()
        self._visited_resources: set[tuple[int, int]] = set()
        self._visited_forms: set[tuple[int, int]] = set()
        self._visited_fonts: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------ walk

    def visit_resources(self, resources: pikepdf.Dictionary) -> None:
        """Deduplicate one resource dictionary and any forms it contains.

        Colour spaces are processed first so that image signatures built from
        ``/ColorSpace`` metadata see already-canonicalised ICC references.

        Args:
            resources: A page or Form XObject ``/Resources`` dictionary.
        """

        resources_id = _objgen(resources)
        if resources_id is not None:
            if resources_id in self._visited_resources:
                return
            self._visited_resources.add(resources_id)

        self._merge_colorspaces(_get(resources, "/ColorSpace"))
        self._visit_xobjects(_get(resources, "/XObject"))
        self._merge_fonts(_get(resources, "/Font"))

    def _visit_xobjects(self, xobjects: pikepdf.Object | None) -> None:
        """Merge image entries and recurse into every Form XObject.

        Args:
            xobjects: The ``/XObject`` dictionary of a resource dictionary.
        """

        if not isinstance(xobjects, pikepdf.Dictionary):
            return
        # pikepdf dictionary iteration order is not guaranteed; sorting keeps
        # which copy becomes canonical (and thus the counters) deterministic.
        for key in sorted(xobjects.keys()):
            entry = _get(xobjects, key)
            if not isinstance(entry, pikepdf.Stream):
                continue
            subtype = _name(_get(entry, "/Subtype"))
            if subtype == _IMAGE_SUBTYPE:
                self._merge_image(xobjects, key, entry)
            elif subtype == _FORM_SUBTYPE:
                self._visit_form(entry)

    def _visit_form(self, form: pikepdf.Stream) -> None:
        """Recurse into a Form XObject's resources with cycle protection.

        A form may reference itself (directly or through another form), so the
        form's object number is recorded before its resources are visited.

        Args:
            form: ``/Subtype /Form`` XObject stream.
        """

        form_id = _objgen(form)
        if form_id is not None:
            if form_id in self._visited_forms:
                return
            self._visited_forms.add(form_id)
        resources = _get(form, "/Resources")
        if isinstance(resources, pikepdf.Dictionary):
            self.visit_resources(resources)

    # ---------------------------------------------------------------- images

    def _merge_image(
        self,
        holder: pikepdf.Dictionary,
        key: str,
        image: pikepdf.Stream,
    ) -> None:
        """Re-point an image entry when a byte-identical image already exists.

        Args:
            holder: Dictionary that owns the entry (a ``/XObject`` dictionary).
            key: Name of the entry inside *holder*.
            image: Candidate ``/Subtype /Image`` stream.
        """

        self._merge_colorspace(_get(image, "/ColorSpace"))
        signature = _image_signature(image)
        if signature is None:
            return
        merged, saved = self._merge(
            self._canonical_images,
            self._displaced_images,
            signature,
            image,
            lambda canonical: holder.__setitem__(key, canonical),
        )
        if merged:
            self.stats.duplicate_images_merged += 1
            self.stats.bytes_saved_estimate += saved

    # ------------------------------------------------------------------- ICC

    def _merge_colorspaces(self, colorspaces: pikepdf.Object | None) -> None:
        """Walk every value of a ``/ColorSpace`` resource dictionary.

        Args:
            colorspaces: ``/ColorSpace`` dictionary, or None/other when absent.
        """

        if not isinstance(colorspaces, pikepdf.Dictionary):
            return
        for key in sorted(colorspaces.keys()):
            self._merge_colorspace(_get(colorspaces, key))

    def _merge_colorspace(self, colorspace: pikepdf.Object | None) -> None:
        """Merge an ICC profile inside one colour-space value.

        Handles ``[/ICCBased <stream>]`` and nested arrays such as
        ``[/Indexed [/ICCBased <stream>] hival lookup]`` (FR-DED-3).

        Args:
            colorspace: A colour-space value; most are names and are ignored.
        """

        if isinstance(colorspace, pikepdf.Array):
            self._walk_colorspace_array(colorspace)

    def _walk_colorspace_array(self, array: pikepdf.Array) -> None:
        """Find ICC profiles in *array*, recursing into nested colour spaces.

        Args:
            array: Colour-space array from a resource or an image stream.
        """

        try:
            length = len(array)
        except Exception:
            return
        if length == 2 and _name(_get(array, 0)) == _ICC_BASED:
            profile = _get(array, 1)
            if isinstance(profile, pikepdf.Stream):
                self._merge_icc(array, profile)
            return
        for index in range(length):
            element = _get(array, index)
            if isinstance(element, pikepdf.Array):
                self._walk_colorspace_array(element)

    def _merge_icc(self, array: pikepdf.Array, profile: pikepdf.Stream) -> None:
        """Re-point an ICC array at the canonical profile for its bytes.

        Args:
            array: The ``[/ICCBased ...]`` array to rewrite in place.
            profile: Candidate ICC profile stream.
        """

        signature = _content_signature(profile)
        if signature is None:
            return
        merged, saved = self._merge(
            self._canonical_icc,
            self._displaced_icc,
            signature,
            profile,
            lambda canonical: array.__setitem__(1, canonical),
        )
        if merged:
            self.stats.duplicate_icc_merged += 1
            self.stats.bytes_saved_estimate += saved

    # ----------------------------------------------------------------- fonts

    def _merge_fonts(self, fonts: pikepdf.Object | None) -> None:
        """Merge font programs for every font in a ``/Font`` dictionary.

        Args:
            fonts: ``/Font`` dictionary, or None/other when absent.
        """

        if not isinstance(fonts, pikepdf.Dictionary):
            return
        for key in sorted(fonts.keys()):
            font = _get(fonts, key)
            if isinstance(font, pikepdf.Dictionary):
                self._merge_font(font)

    def _merge_font(self, font: pikepdf.Dictionary) -> None:
        """Re-point duplicate ``/FontFile*`` entries of one font.

        Both the font's own ``/FontDescriptor`` and the descriptors of its
        ``/DescendantFonts`` (CID fonts) are inspected.

        Args:
            font: ``/Type /Font`` dictionary.
        """

        font_id = _objgen(font)
        if font_id is not None:
            if font_id in self._visited_fonts:
                return
            self._visited_fonts.add(font_id)

        descriptors: list[pikepdf.Dictionary] = []
        self._collect_descriptor(font, descriptors)
        descendants = _get(font, "/DescendantFonts")
        if isinstance(descendants, pikepdf.Array):
            for descendant in descendants:
                if isinstance(descendant, pikepdf.Dictionary):
                    self._collect_descriptor(descendant, descriptors)

        for descriptor in descriptors:
            for key in _FONT_FILE_KEYS:
                font_file = _get(descriptor, key)
                if isinstance(font_file, pikepdf.Stream):
                    self._merge_font_file(descriptor, key, font_file)

    def _collect_descriptor(
        self,
        source: pikepdf.Dictionary,
        descriptors: list[pikepdf.Dictionary],
    ) -> None:
        """Append *source*'s ``/FontDescriptor`` when it is a dictionary.

        Args:
            source: Font or descendant-font dictionary.
            descriptors: Accumulator for the descriptors to inspect.
        """

        descriptor = _get(source, "/FontDescriptor")
        if isinstance(descriptor, pikepdf.Dictionary):
            descriptors.append(descriptor)

    def _merge_font_file(
        self,
        descriptor: pikepdf.Dictionary,
        key: str,
        font_file: pikepdf.Stream,
    ) -> None:
        """Re-point one duplicate font program entry.

        The descriptor key is part of the signature because ``/FontFile``,
        ``/FontFile2`` and ``/FontFile3`` carry different formats and different
        auxiliary lengths (``/Length1``-``/Length3``).

        Args:
            descriptor: ``/FontDescriptor`` that owns the entry.
            key: One of ``/FontFile``, ``/FontFile2``, ``/FontFile3``.
            font_file: Candidate font program stream.
        """

        signature = _content_signature(font_file)
        if signature is None:
            return
        merged, saved = self._merge(
            self._canonical_fonts,
            self._displaced_fonts,
            (key, signature),
            font_file,
            lambda canonical: descriptor.__setitem__(key, canonical),
        )
        if merged:
            self.stats.duplicate_fonts_merged += 1
            self.stats.bytes_saved_estimate += saved

    # -------------------------------------------------------------- plumbing

    def _merge(
        self,
        canonical: dict[object, pikepdf.Stream],
        displaced: set[tuple[int, int]],
        signature: object,
        stream: pikepdf.Stream,
        re_point: _RePointer,
    ) -> tuple[bool, int]:
        """Re-point *stream* at its canonical twin when one is registered.

        Args:
            canonical: Signature -> first stream seen with that signature.
            displaced: Object numbers already counted as displaced duplicates.
            signature: Hash key identifying the payload.
            stream: Candidate duplicate stream.
            re_point: Callback that stores the canonical stream in the
                duplicate's dictionary slot.

        Returns:
            ``(merged, saved)`` where *saved* is the raw byte count of the
            displaced duplicate, or ``(False, 0)`` when nothing was merged.
        """

        first = canonical.get(signature)
        if first is None:
            # First copy seen: remember it as the canonical target for later
            # duplicates; its dictionary entry stays untouched.
            canonical[signature] = stream
            return False, 0
        if _same_object(first, stream):
            # Same object referenced from two places: no entry needs rewriting.
            return False, 0

        re_point(self._as_reference(first))
        stream_id = _objgen(stream)
        if stream_id is not None:
            if stream_id in displaced:
                # Two entries already pointed at this duplicate; count it once.
                return True, 0
            displaced.add(stream_id)
        return True, _raw_length(stream)

    def _as_reference(self, stream: pikepdf.Stream) -> pikepdf.Object:
        """Return *stream* usable as a dictionary value referencing its object.

        Args:
            stream: Canonical stream registered earlier.

        Returns:
            The stream itself when already indirect, else a new indirect object.
        """

        try:
            if stream.is_indirect:
                return stream
        except Exception:
            pass
        try:
            return self._pdf.make_indirect(stream)
        except Exception:
            return stream


# --------------------------------------------------------------- helpers


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
    """Return a PDF name object's text (e.g. ``"/Image"``), else None.

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


def _same_object(first: pikepdf.Object, second: pikepdf.Object) -> bool:
    """Return True when two handles reference the same indirect object.

    Args:
        first: One object handle.
        second: The other object handle.

    Returns:
        True for the same indirect object, False otherwise.
    """

    try:
        return bool(first.is_same_object_as(second))
    except Exception:
        return False


def _decoded_or_raw(stream: pikepdf.Stream) -> bytes:
    """Return decoded stream bytes, falling back to the raw (still encoded) ones.

    Some filters (notably ``DCTDecode``) cannot be decoded by qpdf; the raw
    bytes still identify the payload for deduplication purposes.

    Args:
        stream: Stream to read.

    Returns:
        Decoded bytes when possible, else raw bytes, else ``b""``.
    """

    try:
        return stream.read_bytes()
    except Exception:
        try:
            return stream.read_raw_bytes()
        except Exception:
            return b""


def _raw_length(stream: pikepdf.Stream) -> int:
    """Return the number of raw (encoded) bytes *stream* occupies.

    Args:
        stream: Stream to measure.

    Returns:
        Raw byte count, or 0 when the stream cannot be read.
    """

    try:
        return len(stream.read_raw_bytes())
    except Exception:
        return 0


def _image_signature(image: pikepdf.Stream) -> bytes | None:
    """Hash the metadata and decoded payload of an image (FR-DED-1).

    The signature covers ``W|H|BPC|CS|IM|D``, the decoded payload (raw fallback)
    and, when present, the decoded ``/SMask`` payload, so two images are only
    merged when they are interchangeable.

    Args:
        image: ``/Subtype /Image`` stream.

    Returns:
        32-byte SHA-256 digest, or None when the image cannot be inspected.
    """

    try:
        metadata_parts: list[str] = []
        for key in _IMAGE_METADATA_KEYS:
            value = _get(image, key)
            metadata_parts.append("" if value is None else str(value))
        metadata = "|".join(metadata_parts)
    except Exception:
        return None

    try:
        digest = hashlib.sha256(metadata.encode("utf-8", "backslashreplace"))
        digest.update(b"\x00")
        digest.update(_decoded_or_raw(image))
        smask = _get(image, "/SMask")
        if isinstance(smask, pikepdf.Stream):
            digest.update(b"\x00")
            digest.update(_decoded_or_raw(smask))
        return digest.digest()
    except Exception:
        return None


def _content_signature(stream: pikepdf.Stream) -> bytes | None:
    """Hash the decoded payload of a resource stream, raw bytes as fallback.

    Args:
        stream: ICC profile or font program stream.

    Returns:
        32-byte SHA-256 digest, or None when the stream cannot be read.
    """

    try:
        return hashlib.sha256(_decoded_or_raw(stream)).digest()
    except Exception:
        return None
