"""Post-compression PDF signing.

A digital signature is an identity statement, not a compression feature, so
this stage is opt-in and requires a PKCS#12 certificate owned by the user. The
old signature is always removed first (it cannot survive re-encoding); the new
one is applied as the final write, because every later modification would
invalidate it.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pikepdf

from ..config import CompressionConfig


class SigningError(Exception):
    """Raised when a document cannot be signed; surfaced as a file error."""


def sign_pdf_file(pdf_path: Path, config: CompressionConfig) -> None:
    """Sign *pdf_path* in place with the configured PKCS#12 certificate.

    Raises:
        SigningError: the certificate is missing, unreadable, the password is
            wrong, or the signing backend is unavailable. The input file is left
            untouched on every failure path.
    """
    path = Path(pdf_path)
    keystore = Path(config.sign_pkcs12_path).expanduser()
    if not config.sign_after_slimming:
        return
    if not keystore.is_file():
        raise SigningError(f"signing certificate not found: {keystore}")

    try:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers
        from pyhanko.sign.fields import SigFieldSpec
        from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
        from pyhanko.sign.timestamps import HTTPTimeStamper
    except ImportError as exc:  # pragma: no cover - dependency is normally present
        raise SigningError("signing support is not installed") from exc

    passphrase = config.sign_pkcs12_password.encode() if config.sign_pkcs12_password else None
    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx_file=str(keystore), passphrase=passphrase)
    except Exception as exc:
        raise SigningError("could not open the signing certificate (wrong password?)") from exc
    if signer is None:
        # pyhanko reports a failed PKCS#12 load by returning None.
        raise SigningError("could not open the signing certificate (wrong password?)")

    field_name = (config.sign_field_name or "OzemPDF Signature").strip()
    metadata = PdfSignatureMetadata(
        field_name=field_name,
        reason=(config.sign_reason or None),
        location=(config.sign_location or None),
        md_algorithm="sha256",
    )
    timestamper = HTTPTimeStamper(config.sign_tsa_url) if config.sign_tsa_url else None
    temp = path.with_name(f".ozempdf_signed_{uuid.uuid4().hex}.tmp.pdf")
    try:
        with path.open("rb") as source:
            writer = IncrementalPdfFileWriter(source, strict=False)
            signed = signers.sign_pdf(
                writer,
                metadata,
                signer=signer,
                timestamper=timestamper,
                new_field_spec=SigFieldSpec(sig_field_name=field_name),
            )
            temp.write_bytes(signed.getvalue())
        # Verify the signed bytes open and carry a signature before promoting.
        with pikepdf.open(temp) as check:
            if len(check.Root.get("/AcroForm", {}).get("/Fields", [])) == 0:
                raise SigningError("the signature field was not written")
        os.replace(temp, path)
    except SigningError:
        raise
    except Exception as exc:
        raise SigningError(str(exc)) from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
