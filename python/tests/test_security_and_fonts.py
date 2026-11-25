"""Encryption, signature flattening, re-signing, font subsetting and D-15."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from conftest import content_bytes

from ozempdf.config import CompressionConfig, OutputMode
from ozempdf.engine import (
    PasswordRequiredError,
    ProtectionKind,
    compress_pdf_file,
    inspect_protection,
    open_and_unlock_pdf,
)
from ozempdf.engine.batch import run_batch_compression


def test_owner_encrypted_signed_document_is_flattened(assets, tmp_path: Path) -> None:
    output = tmp_path / "signed.pdf"
    config = CompressionConfig.from_preset("balanced").copy_with(
        unlock_and_flatten_signatures=True
    )
    result = compress_pdf_file(assets.signed, output, config)

    assert result.success, result.error_message
    assert result.was_encrypted is True
    assert result.signatures_flattened >= 1
    with pikepdf.open(output) as pdf:
        assert pdf.is_encrypted is False
        assert "/Perms" not in pdf.Root
        page = pdf.pages[0]
        assert page.get("/Annots") is None or len(page.Annots) == 0
        # The visible stamp is stamped into page content.
        assert b"OzSigFlat" in content_bytes(output)


def test_permission_restrictions_are_removed_without_a_password(assets, tmp_path: Path) -> None:
    """A document that opens without a password loses its restrictions for free."""
    output = tmp_path / "permissions.pdf"
    result = compress_pdf_file(assets.signed, output, CompressionConfig.from_preset("balanced"))

    assert result.success, result.error_message
    assert result.protection_kind == ProtectionKind.PERMISSIONS.value
    assert result.protection_removed is True
    assert result.required_password is False
    assert result.was_encrypted is True
    with pikepdf.open(output) as pdf:
        assert pdf.is_encrypted is False
        assert "/Encrypt" not in pdf.trailer
        assert pdf.pages[0].Contents.read_bytes()
    report = result.to_dict()
    assert report["protection_removed"] is True
    assert report["protection_kind"] == "permissions"


def test_supplied_password_does_not_break_permission_only_files(assets, tmp_path: Path) -> None:
    """The empty user password is tried first, so mixed batches work."""
    output = tmp_path / "mixed.pdf"
    config = CompressionConfig.from_preset("balanced").copy_with(password="not-needed")
    result = compress_pdf_file(assets.signed, output, config)
    assert result.success, result.error_message
    assert result.protection_removed is True
    assert result.required_password is False


def test_mixed_batch_with_one_open_password(assets, tmp_path: Path) -> None:
    source_a = tmp_path / "restricted.pdf"
    source_a.write_bytes(assets.signed.read_bytes())
    source_b = tmp_path / "locked.pdf"
    source_b.write_bytes(assets.protected.read_bytes())
    config = CompressionConfig.from_preset("balanced").copy_with(
        password="secret",
        max_workers=1,
        output_mode=OutputMode.CUSTOM_FOLDER,
        output_dir=str(tmp_path / "out"),
    )
    summary = run_batch_compression([source_a, source_b], config)
    assert summary.succeeded_count == 2, [result.error_message for result in summary.results]
    assert summary.protection_removed_count == 1


def test_open_password_without_credential_is_reported_not_guessed(assets, tmp_path: Path) -> None:
    output = tmp_path / "locked.pdf"
    result = compress_pdf_file(assets.protected, output, CompressionConfig.from_preset("balanced"))
    assert result.success is False
    assert result.protection_kind == ProtectionKind.PASSWORD.value
    assert result.required_password is True
    assert "password" in result.error_message.lower()
    assert not output.exists()


def test_input_is_opened_read_only(assets, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never request write access to the source: cloud/read-only files break."""
    from ozempdf.engine import unlock

    real_open = pikepdf.open
    seen: dict = {}

    def recording_open(*args, **kwargs):
        seen.update(kwargs)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(unlock.pikepdf, "open", recording_open)
    pdf, _stats = open_and_unlock_pdf(assets.ui)
    pdf.close()
    assert not seen.get("allow_overwriting_input", False)


def test_permission_denied_is_actionable(
    assets, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked/cloud-only input produces a clear message, not a raw errno."""
    from ozempdf.engine import unlock

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(assets.ui))

    monkeypatch.setattr(unlock.pikepdf, "open", denied)
    output = tmp_path / "denied.pdf"
    result = compress_pdf_file(assets.ui, output, CompressionConfig.from_preset("balanced"))
    assert result.success is False
    assert "Permission denied" in result.error_message
    assert str(assets.ui) in result.error_message
    assert not output.exists()


def test_denied_output_names_the_folder_not_the_temp_file(
    assets, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied write must point at the folder the user chose, not our temp name.

    Reported from a OneDrive folder: the message named a random
    ``.ozempdf_*.tmp.pdf`` the user could never open, and suggested closing an
    application holding it. The temp file is an implementation detail.
    """
    from ozempdf.engine import pipeline

    def always_denied(source, destination):
        raise PermissionError(13, "Permission denied", str(source))

    monkeypatch.setattr(pipeline.os, "replace", always_denied)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = compress_pdf_file(
        assets.ui, out_dir / "result.pdf", CompressionConfig.from_preset("balanced")
    )
    assert result.success is False
    assert "Could not write the result into" in result.error_message
    assert str(out_dir) in result.error_message
    assert ".ozempdf_" not in result.error_message
    # Every retry ran, then the temp file was cleaned up.
    assert list(out_dir.iterdir()) == []


def test_transient_lock_on_the_output_is_retried(
    assets, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cloud/AV locks are momentary: one denial must not fail the file."""
    from ozempdf.engine import pipeline

    real_replace = pipeline.os.replace
    attempts = {"count": 0}

    def flaky(source, destination):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError(13, "Permission denied", str(source))
        return real_replace(source, destination)

    monkeypatch.setattr(pipeline.os, "replace", flaky)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = compress_pdf_file(
        assets.ui, out_dir / "retried.pdf", CompressionConfig.from_preset("balanced")
    )
    assert result.success is True, result.error_message
    # The first denial was retried rather than reported. pikepdf promotes its
    # own save through a temp file, so one save attempt is more than one call.
    assert attempts["count"] > 1
    assert (out_dir / "retried.pdf").is_file()


def test_protection_classification(assets) -> None:
    assert inspect_protection(assets.ui).kind is ProtectionKind.NONE
    assert inspect_protection(assets.signed).kind is ProtectionKind.PERMISSIONS
    assert inspect_protection(assets.protected).kind is ProtectionKind.PASSWORD
    assert inspect_protection(assets.protected).needs_credential is True


def test_unsupported_encryption_is_reported_not_unlocked(assets, tmp_path: Path) -> None:
    """Certificate/unsupported schemes cannot be decrypted by this engine."""
    data = assets.protected.read_bytes().replace(b"/V 4", b"/V 9", 1)
    unsupported = tmp_path / "unsupported.pdf"
    unsupported.write_bytes(data)
    assert inspect_protection(unsupported).kind is ProtectionKind.UNSUPPORTED
    assert inspect_protection(unsupported).needs_credential is True

    result = compress_pdf_file(
        unsupported, tmp_path / "out.pdf", CompressionConfig.from_preset("balanced")
    )
    assert result.success is False
    assert result.protection_kind == ProtectionKind.UNSUPPORTED.value
    assert "certificate" in result.error_message.lower() or "unsupported" in result.error_message.lower()
    assert not (tmp_path / "out.pdf").exists()


def test_password_protected_file_needs_the_password(assets, tmp_path: Path) -> None:
    output = tmp_path / "protected.pdf"
    failed = compress_pdf_file(assets.protected, output, CompressionConfig.from_preset("balanced"))
    assert failed.success is False
    assert "password" in failed.error_message.lower()
    assert not output.exists()

    config = CompressionConfig.from_preset("balanced").copy_with(password="secret")
    succeeded = compress_pdf_file(assets.protected, output, config)
    assert succeeded.success, succeeded.error_message
    with pikepdf.open(output) as pdf:
        assert pdf.is_encrypted is False


def test_no_password_guessing(assets) -> None:
    """D-15: the old dictionary of common passwords must be gone."""
    with pytest.raises(PasswordRequiredError):
        open_and_unlock_pdf(assets.protected, password="1234")


def test_flatten_disabled_leaves_signature_in_place(assets, tmp_path: Path) -> None:
    output = tmp_path / "no-flatten.pdf"
    config = CompressionConfig.from_preset("balanced").copy_with(
        unlock_and_flatten_signatures=False
    )
    result = compress_pdf_file(assets.signed, output, config)
    assert result.success, result.error_message
    assert result.signatures_flattened == 0
    with pikepdf.open(output, password="") as pdf:
        page = pdf.pages[0]
        assert page.get("/Annots") is not None
        assert len(page.Annots) >= 1


def test_font_is_subset_and_still_valid(assets, tmp_path: Path) -> None:
    from fontTools.ttLib import TTFont

    output = tmp_path / "font.pdf"
    result = compress_pdf_file(assets.font, output, CompressionConfig.from_preset("balanced"))

    assert result.success, result.error_message
    assert result.font_streams_optimized >= 1
    assert "cmaps_optimized" in result.to_dict()
    assert content_bytes(output) == content_bytes(assets.font)

    with pikepdf.open(output) as pdf:
        font_program = None
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Length1") is not None:
                font_program = obj.read_bytes()
                break
    assert font_program, "expected an embedded TrueType program"
    font = TTFont(__import__("io").BytesIO(font_program))
    assert "glyf" in font
    assert font["maxp"].numGlyphs >= 1


# --------------------------------------------------------------------------- #
# Re-signing
# --------------------------------------------------------------------------- #


def _make_pkcs12(path: Path, password: str = "certpass") -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "OzemPDF Test Signer"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OzemPDF"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(__import__("datetime").datetime(2024, 1, 1))
        .not_valid_after(__import__("datetime").datetime(2034, 1, 1))
        .sign(key, hashes.SHA256())
    )
    blob = pkcs12.serialize_key_and_certificates(
        b"OzemPDF Test Signer",
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password.encode()),
    )
    path.write_bytes(blob)
    return path


def test_sign_after_slimming(assets, tmp_path: Path) -> None:
    certificate = _make_pkcs12(tmp_path / "signer.p12")
    output = tmp_path / "signed-output.pdf"
    config = CompressionConfig.from_preset("balanced").copy_with(
        sign_after_slimming=True,
        sign_pkcs12_path=str(certificate),
        sign_pkcs12_password="certpass",
        sign_reason="Test signature",
    )
    result = compress_pdf_file(assets.signed, output, config)

    assert result.success, result.error_message
    assert result.signed is True
    assert result.signatures_flattened >= 1
    with pikepdf.open(output) as pdf:
        fields = pdf.Root.AcroForm.Fields
        assert len(fields) == 1
        signature = fields[0].get("/V")
        assert signature is not None
        assert signature.get("/ByteRange") is not None
        # /Contents is a PDF string holding the CMS container, not a stream.
        assert len(signature.get("/Contents").as_bytes()) > 100
        assert pdf.is_encrypted is False


def test_signing_with_wrong_password_fails_cleanly(assets, tmp_path: Path) -> None:
    certificate = _make_pkcs12(tmp_path / "signer.p12")
    output = tmp_path / "signed-bad.pdf"
    config = CompressionConfig.from_preset("balanced").copy_with(
        sign_after_slimming=True,
        sign_pkcs12_path=str(certificate),
        sign_pkcs12_password="wrong",
    )
    result = compress_pdf_file(assets.signed, output, config)
    assert result.success is False
    assert "certificate" in result.error_message.lower()
    assert not output.exists()
