"""Stage 1: decrypt a PDF and flatten digital-signature appearances.

Opening follows FR-UNL-1/2 with the D-15 reduction: no password dictionary is
attempted.  The empty password is always tried first because owner-restricted
documents (printing/copying/signature restrictions without an open password)
open with it; a caller-supplied password is only needed for documents whose
content is encrypted under an open password.

Flattening follows FR-UNL-3 and is opt-in only (D-14): a flattened signature
keeps its visual stamp but is no longer cryptographically valid.
"""

from __future__ import annotations

import math
import os
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pikepdf


class ProtectionKind(str, Enum):
    """How a PDF is protected, and therefore what can be done about it."""

    NONE = "none"
    #: Encrypted, but the empty user password opens it: only permissions are
    #: restricted. The engine removes them without asking for anything.
    PERMISSIONS = "permissions"
    #: Content is encrypted under an open password; the password is required.
    PASSWORD = "password"
    #: Certificate (public-key) or an unsupported encryption scheme: the private
    #: key is required and this engine cannot decrypt it.
    UNSUPPORTED = "unsupported"


@dataclass(slots=True)
class ProtectionInfo:
    """Result of inspecting a file without modifying it."""

    kind: ProtectionKind
    encrypted: bool
    detail: str = ""

    @property
    def needs_credential(self) -> bool:
        """True when the document cannot be opened without a password/key."""
        return self.kind in {ProtectionKind.PASSWORD, ProtectionKind.UNSUPPORTED}


class PasswordRequiredError(Exception):
    """Raised when an open password is required and was not supplied."""


class UnsupportedProtectionError(Exception):
    """Raised when the encryption scheme cannot be opened by this engine."""

    def __init__(self, message: str = "", kind: ProtectionKind = ProtectionKind.UNSUPPORTED) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class UnlockStats:
    """Observable effects of the unlock stage.

    Attributes:
        was_encrypted: True when the input document was encrypted.
        signatures_flattened: Distinct signature objects stamped onto pages.
        perms_removed: True when the document catalog carried a ``/Perms`` entry
            (DocMDP / UR3 restrictions) that was removed.
        required_password: True when the content was encrypted under an open
            password, i.e. the credential was genuinely needed.
        protection_kind: One of :class:`ProtectionKind`'s values.
    """

    was_encrypted: bool = False
    signatures_flattened: int = 0
    perms_removed: bool = False
    required_password: bool = False
    protection_kind: str = ProtectionKind.NONE.value

    @property
    def protection_removed(self) -> bool:
        """True when encryption/restrictions were removed without a credential."""
        return self.was_encrypted and not self.required_password


def _looks_encryption_related(exc: BaseException) -> bool:
    """Heuristic for distinguishing an encryption failure from a broken file."""
    message = str(exc).lower()
    return any(
        token in message
        for token in ("encrypt", "security", "certificate", "password", "algorithm")
    )


def _open_pdf(path: str | os.PathLike[str], password: str) -> pikepdf.Pdf:
    """Open a PDF read-only with one password candidate.

    The pipeline never writes back to the input: every result goes to a temp
    file in the destination directory and is promoted with ``os.replace``.
    Opening read-only therefore avoids needing write permission on the source,
    which is what fails for read-only media, sharing locks and OneDrive/cloud
    placeholders.
    """
    return pikepdf.open(
        path,
        password=password,
        suppress_warnings=True,
    )


def inspect_protection(path: str | os.PathLike[str]) -> ProtectionInfo:
    """Classify a PDF's protection without modifying it.

    Only the empty user password is tried. A document that opens that way is
    permission-restricted (Adobe asks for a password to change permissions, but
    every conforming reader can display it), so OzemPDF can remove the
    restrictions without a credential. A document that does not open cannot be
    decrypted without its open password or private key.
    """
    try:
        with _open_pdf(path, "") as pdf:
            if not pdf.is_encrypted:
                return ProtectionInfo(ProtectionKind.NONE, False)
            return ProtectionInfo(ProtectionKind.PERMISSIONS, True)
    except pikepdf.PasswordError:
        return ProtectionInfo(ProtectionKind.PASSWORD, True)
    except Exception as exc:
        if _looks_encryption_related(exc):
            return ProtectionInfo(ProtectionKind.UNSUPPORTED, True, str(exc))
        return ProtectionInfo(ProtectionKind.NONE, False, str(exc))


def open_and_unlock_pdf(
    path: str | os.PathLike[str],
    password: str = "",
    flatten_signatures: bool = False,
) -> tuple[pikepdf.Pdf, UnlockStats]:
    """Open *path*, removing owner restrictions and decrypting when possible.

    The empty user password is always attempted first, so permission-restricted
    documents are unlocked even when a password was supplied for a different
    file in the same batch. A supplied password is then tried for documents
    whose content is actually encrypted under it (D-15: no guessing beyond the
    caller's own password).

    Args:
        path: PDF file to open.
        password: User-supplied open password; empty for none.
        flatten_signatures: When True, stamp signature appearances and strip
            signature structures (FR-UNL-3). Defaults to False (D-14).

    Returns:
        Tuple of the opened :class:`pikepdf.Pdf` and its :class:`UnlockStats`.
        The returned document must be closed by the caller.

    Raises:
        PasswordRequiredError: If an open password is required and the supplied
            one (or none) does not open the document.
        UnsupportedProtectionError: If the document uses certificate encryption
            or an encryption scheme libqpdf cannot open.
    """
    pdf: pikepdf.Pdf | None = None
    required_password = False
    last_error: Exception | None = None

    try:
        pdf = _open_pdf(path, "")
    except pikepdf.PasswordError as exc:
        last_error = exc
    except Exception as exc:
        if _looks_encryption_related(exc):
            raise UnsupportedProtectionError(str(exc)) from exc
        raise

    if pdf is None and password:
        try:
            pdf = _open_pdf(path, password)
            required_password = True
        except pikepdf.PasswordError as exc:
            last_error = exc
        except Exception as exc:
            if _looks_encryption_related(exc):
                raise UnsupportedProtectionError(str(exc)) from exc
            raise

    if pdf is None:
        raise PasswordRequiredError from last_error

    # FR-UNL-2: record encryption state before any modification, because
    # pikepdf keeps reporting the source file as encrypted after decryption.
    was_encrypted = bool(pdf.is_encrypted)
    if was_encrypted and required_password:
        kind = ProtectionKind.PASSWORD
    elif was_encrypted:
        kind = ProtectionKind.PERMISSIONS
    else:
        kind = ProtectionKind.NONE
    stats = UnlockStats(
        was_encrypted=was_encrypted,
        required_password=required_password,
        protection_kind=kind.value,
    )

    if flatten_signatures:
        signatures_flattened, perms_removed = flatten_and_strip_signatures(pdf)
        stats.signatures_flattened = signatures_flattened
        stats.perms_removed = perms_removed

    return pdf, stats


def flatten_and_strip_signatures(pdf: pikepdf.Pdf) -> tuple[int, bool]:
    """Flatten signature appearances and strip signature structures (FR-UNL-3).

    The visual appearance of every signature widget is stamped onto its page as
    a ``/OzSigFlat<n>`` form XObject, then the signature annotations, signature
    form fields, ``/SigFlags`` and ``/Perms`` are removed.  Removing those
    structures deliberately invalidates the PKCS#7 byte-range digest: the
    document keeps the stamped look of a signature but not its validity.

    Args:
        pdf: Open document, modified in place.

    Returns:
        Tuple of ``(number of distinct signature objects handled, whether
        /Perms was removed)``.
    """
    signature_count = 0
    seen_objgens: set[tuple[int, int]] = set()

    for page in pdf.pages:
        try:
            annots = page.obj.get("/Annots")
        except Exception:
            continue
        if not isinstance(annots, pikepdf.Array):
            continue

        removed_indexes: list[int] = []
        for index in range(len(annots)):
            annot = annots[index]
            if not _is_signature_object(annot):
                continue

            objgen = _objgen(annot)
            if objgen == (0, 0):
                # Direct objects have no identity across references, so every
                # occurrence counts (FR-UNL-3 step 6).
                signature_count += 1
            elif objgen not in seen_objgens:
                seen_objgens.add(objgen)
                signature_count += 1

            # A malformed /AP or /BBox must never abort unlocking, so the whole
            # flattening attempt is contained here.
            try:
                _flatten_widget_appearance_to_page(pdf, page, annot)
            except Exception:
                pass
            removed_indexes.append(index)

        # Delete from the back so earlier indexes stay valid.
        for index in reversed(removed_indexes):
            try:
                del annots[index]
            except Exception:
                pass
        if removed_indexes and len(annots) == 0:
            try:
                del page.obj["/Annots"]
            except Exception:
                pass

    _strip_signature_fields(pdf)
    perms_removed = _strip_perms(pdf)
    return signature_count, perms_removed


def zopfli_compress(data: bytes, iterations: int = 15) -> bytes:
    """Compress *data* as a zlib stream, keeping whichever result is smaller.

    Zopfli is optional: when it is missing or fails, the zlib (level 9) result
    is returned.  Both encoders emit an RFC 1950 zlib wrapper, so the result is
    always readable with ``FlateDecode``.  This lives here so every stage uses
    one implementation.

    Args:
        data: Decoded bytes to compress.
        iterations: Zopfli iteration count.

    Returns:
        The smaller of the zlib and Zopfli encodings, zlib-wrapped.
    """
    baseline = zlib.compress(data, 9)
    if not data:
        # Both encoders emit the same trivial stream; skip Zopfli entirely.
        return baseline
    try:
        from zopfli import zlib as zopfli_zlib
    except Exception:
        return baseline
    try:
        candidate = zopfli_zlib.compress(data, int(iterations))
    except Exception:
        return baseline
    return candidate if len(candidate) < len(baseline) else baseline


def _is_signature_object(obj: Any) -> bool:
    """Return True if *obj* is a signature annotation or signature field.

    Recognised shapes (FR-UNL-3 step 1): ``/FT /Sig``, ``/Type /Sig``, a ``/V``
    that is a signature value dictionary, or a widget whose ``/Parent`` field is
    a signature field.

    Args:
        obj: Candidate annotation or field object.

    Returns:
        True when the object carries signature semantics.
    """
    try:
        if not isinstance(obj, pikepdf.Dictionary):
            return False
        if obj.get("/FT") == pikepdf.Name("/Sig"):
            return True
        if obj.get("/Type") == pikepdf.Name("/Sig"):
            return True
        signature_value = obj.get("/V")
        if isinstance(signature_value, pikepdf.Dictionary) and signature_value.get(
            "/Type"
        ) == pikepdf.Name("/Sig"):
            return True
        parent = obj.get("/Parent")
        if isinstance(parent, pikepdf.Dictionary) and parent.get("/FT") == pikepdf.Name(
            "/Sig"
        ):
            return True
    except Exception:
        return False
    return False


def _objgen(obj: Any) -> tuple[int, int]:
    """Return an object's ``(object number, generation)``, or ``(0, 0)``.

    ``(0, 0)`` is also what pikepdf reports for direct (non-indirect) objects.

    Args:
        obj: Any PDF object.

    Returns:
        Two-tuple of object number and generation.
    """
    try:
        objgen = obj.objgen
        return (int(objgen[0]), int(objgen[1]))
    except Exception:
        return (0, 0)


def _rect_values(obj: Any) -> tuple[float, float, float, float] | None:
    """Convert a four-element ``/Rect`` or ``/BBox`` array to floats.

    Args:
        obj: Candidate array object.

    Returns:
        ``(x0, y0, x1, y1)`` or None when the array is missing or malformed.
    """
    if not isinstance(obj, pikepdf.Array) or len(obj) != 4:
        return None
    try:
        values = tuple(float(value) for value in obj)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return values  # type: ignore[return-value]


def _flatten_widget_appearance_to_page(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    annot: pikepdf.Object,
) -> bool:
    """Stamp a signature widget's appearance onto its page (FR-UNL-3 step 2).

    The appearance stream becomes a page ``/XObject`` named ``/OzSigFlat<n>``
    and is painted with a ``cm`` matrix that maps the appearance ``/BBox`` onto
    the widget ``/Rect``.  An annotation without a visible appearance stream or
    with a zero-area rectangle is skipped.

    Args:
        pdf: Document owning the objects, used to create indirect objects.
        page: Page whose content stream receives the stamp.
        annot: Signature annotation (widget) being flattened.

    Returns:
        True when the stamp was added, False when the widget has no usable
        appearance.
    """
    appearance = annot.get("/AP")
    if not isinstance(appearance, pikepdf.Dictionary):
        return False
    # /N may be a dictionary of appearance states; only a stream can be painted
    # without consulting /AS, so treat other shapes as invisible.
    normal = appearance.get("/N")
    if not isinstance(normal, pikepdf.Stream):
        return False

    rect = _rect_values(annot.get("/Rect"))
    if rect is None:
        return False
    x0, y0, x1, y1 = rect
    width = x1 - x0
    height = y1 - y0
    if width == 0 or height == 0:
        return False

    bbox = _rect_values(normal.get("/BBox"))
    if bbox is None:
        return False
    bx0, by0, bx1, by1 = bbox
    bbox_width = bx1 - bx0
    bbox_height = by1 - by0
    if bbox_width == 0 or bbox_height == 0:
        return False

    sx = width / bbox_width
    sy = height / bbox_height
    # Translate the scaled BBox origin onto the widget rectangle's origin.
    e = x0 - bx0 * sx
    f = y0 - by0 * sy

    if "/Subtype" not in normal:
        normal["/Subtype"] = pikepdf.Name("/Form")

    xobjects = _page_xobjects(pdf, page)
    if xobjects is None:
        return False

    index = 0
    while pikepdf.Name(f"/OzSigFlat{index}") in xobjects:
        index += 1
    name = f"/OzSigFlat{index}"
    # Page resources may only reference indirect objects for XObjects.
    xobjects[name] = pdf.make_indirect(normal)

    content = (
        f"q {sx:.6f} 0 0 {sy:.6f} {e:.6f} {f:.6f} cm {name} Do Q".encode("ascii")
    )
    page.contents_add(pikepdf.Stream(pdf, content), prepend=False)
    return True


def _page_xobjects(pdf: pikepdf.Pdf, page: pikepdf.Page) -> pikepdf.Dictionary | None:
    """Return the page's ``/Resources /XObject`` dictionary, creating it if needed.

    Args:
        pdf: Document used to create indirect dictionaries.
        page: Page to inspect.

    Returns:
        The XObject dictionary to register the stamp in, or None on failure.
    """
    try:
        resources = page.obj.get("/Resources")
        if not isinstance(resources, pikepdf.Dictionary):
            resources = pikepdf.Dictionary()
            page.obj["/Resources"] = pdf.make_indirect(resources)

        xobjects = resources.get("/XObject")
        if not isinstance(xobjects, pikepdf.Dictionary):
            xobjects = pikepdf.Dictionary()
            resources["/XObject"] = pdf.make_indirect(xobjects)
        return xobjects
    except Exception:
        return None


def _strip_signature_fields(pdf: pikepdf.Pdf) -> None:
    """Remove signature fields from ``/AcroForm /Fields`` and drop ``/SigFlags``.

    Field trees are pruned recursively so a signature widget nested under a
    non-signature parent field is removed without deleting the parent.

    Args:
        pdf: Open document, modified in place.
    """
    try:
        acroform = pdf.Root.get("/AcroForm")
        if not isinstance(acroform, pikepdf.Dictionary):
            return
        fields = acroform.get("/Fields")
        if isinstance(fields, pikepdf.Array):
            _prune_signature_fields(fields)
        if "/SigFlags" in acroform:
            del acroform["/SigFlags"]
    except Exception:
        pass


def _prune_signature_fields(fields: pikepdf.Array) -> None:
    """Remove signature fields from an AcroForm field array in place.

    Args:
        fields: ``/Fields`` or a field's ``/Kids`` array.
    """
    for index in range(len(fields) - 1, -1, -1):
        field = fields[index]
        try:
            if _is_signature_object(field):
                del fields[index]
                continue
            kids = field.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                _prune_signature_fields(kids)
                if len(kids) == 0:
                    del field["/Kids"]
        except Exception:
            continue


def _strip_perms(pdf: pikepdf.Pdf) -> bool:
    """Delete ``/Root /Perms`` (DocMDP / UR3 locks).

    Args:
        pdf: Open document, modified in place.

    Returns:
        True when a ``/Perms`` entry was present and removed.
    """
    try:
        if "/Perms" in pdf.Root:
            del pdf.Root["/Perms"]
            return True
    except Exception:
        return False
    return False
