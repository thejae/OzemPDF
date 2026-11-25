"""Batch discovery, output mapping, collisions, cancellation and the CLI."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pdf_fixtures import (
    build_nested_folder_tree,
    build_protected_pdf,
    build_tiny_pdf,
    build_truncated_pdf,
)

from ozempdf.config import CompressionConfig, OutputMode
from ozempdf.engine import (
    BatchCancelledError,
    compress_pdf_file,
    discover_pdf_files,
    render_page_png,
    render_page_thumbnail_png,
    resolve_output_path,
    run_batch_compression,
)
from ozempdf.engine.batch import _unique_outputs


def test_recursive_discovery_sorted_and_filtered(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    build_nested_folder_tree(root)
    (root / "top.pdf").write_bytes(b"%PDF-1.4\n")
    discovered = discover_pdf_files([root])
    names = [path.name for path, _base in discovered]
    assert "notes.txt" not in names
    assert not any(name.startswith(".ozempdf_") for name in names)
    # Deterministic enumeration: the walked root's own files come first, then
    # each subdirectory in sorted order (FR-BAT-3).
    assert names == ["top.pdf", "a.pdf", "b.pdf"]
    assert {path.parent for path, _base in discovered} == {root, root / "2024" / "q3"}
    assert all(base == root.resolve() for _path, base in discovered)


def test_discovery_skips_previous_outputs(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    build_nested_folder_tree(root)
    (root / "2024" / "q3" / "a_compressed.pdf").write_bytes(b"%PDF-1.4\n")
    discovered = discover_pdf_files([root], exclude_suffix="_compressed")
    assert all(not path.stem.endswith("_compressed") for path, _base in discovered)
    # An explicit file is never filtered.
    explicit = discover_pdf_files([root / "2024" / "q3" / "a_compressed.pdf"], exclude_suffix="_compressed")
    assert len(explicit) == 1


def test_output_mapping_modes(tmp_path: Path) -> None:
    source = tmp_path / "in" / "doc.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.4\n")

    suffix = CompressionConfig.from_preset("balanced")
    assert resolve_output_path(source, suffix) == source.with_name("doc_compressed.pdf")

    overwrite = CompressionConfig.from_preset("balanced").copy_with(output_mode=OutputMode.OVERWRITE)
    assert resolve_output_path(source, overwrite) == source.resolve()

    folder = CompressionConfig.from_preset("balanced").copy_with(
        output_mode=OutputMode.CUSTOM_FOLDER, output_dir=str(tmp_path / "out")
    )
    mirrored = resolve_output_path(source, folder, base_folder=tmp_path / "in")
    assert mirrored == tmp_path / "out" / "doc.pdf"
    loose = resolve_output_path(source, folder)
    assert loose == tmp_path / "out" / "doc.pdf"


def test_output_collision_gets_a_suffix(tmp_path: Path) -> None:
    first = tmp_path / "a" / "report.pdf"
    second = tmp_path / "b" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"%PDF-1.4\n")
    second.write_bytes(b"%PDF-1.4\n")
    output = tmp_path / "out" / "report.pdf"
    resolved = _unique_outputs([(first, output), (second, output)])
    assert resolved[0] == output
    assert resolved[1] == tmp_path / "out" / "report-2.pdf"


def test_empty_suffix_falls_back(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    config = CompressionConfig.from_preset("balanced").copy_with(output_suffix="  ")
    assert resolve_output_path(source, config).name == "doc_compressed.pdf"


def test_parallel_batch_is_ordered_and_reports_progress(tmp_path: Path) -> None:
    sources = []
    for index in range(3):
        source = tmp_path / f"file{index}.pdf"
        build_tiny_pdf(source)
        sources.append(source)
    config = CompressionConfig.from_preset("balanced").copy_with(max_workers=2, output_mode=OutputMode.CUSTOM_FOLDER, output_dir=str(tmp_path / "out"))
    seen = []

    def progress(done: int, total: int, result) -> None:
        seen.append((done, total))

    summary = run_batch_compression([(source, None) for source in sources], config, progress)
    assert summary.total_files == 3
    assert summary.succeeded_count == 3
    assert [Path(result.input_path).name for result in summary.results] == ["file0.pdf", "file1.pdf", "file2.pdf"]
    assert [done for done, _total in seen] == [1, 2, 3]


def test_cancel_stops_dispatch_but_keeps_finished_work(tmp_path: Path) -> None:
    sources = []
    for index in range(4):
        source = tmp_path / f"cancel{index}.pdf"
        build_tiny_pdf(source)
        sources.append(source)
    config = CompressionConfig.from_preset("balanced").copy_with(max_workers=1)
    calls = []

    def progress(done: int, total: int, result) -> None:
        calls.append(done)
        if done == 1:
            raise BatchCancelledError()

    summary = run_batch_compression(sources, config, progress)
    assert len(calls) == 1
    assert summary.total_files == 1


def test_thumbnails_and_page_rendering(assets, tmp_path: Path) -> None:
    png = render_page_thumbnail_png(assets.ui)
    assert png and png.startswith(b"\x89PNG")
    small = render_page_png(assets.ui, zoom_percent=100)
    large = render_page_png(assets.ui, zoom_percent=200)
    assert small and large
    assert len(large) > len(small)


def test_page_renders_are_serialized_for_pdfium(assets, monkeypatch: pytest.MonkeyPatch) -> None:
    """N-13: every PDFium call runs under one lock.

    PDFium is not thread-safe. The inspector asks for the original and slimmed
    halves on two connections at once and the thumbnail pool renders in
    parallel; overlapping calls corrupt PDFium's global state and every later
    render fails, which is the permanently dark inspector. Asserting that two
    renders never run at the same time pins the fix, not the timing of a race.
    """
    import ozempdf.engine.batch as batch_module

    original = batch_module._render_with_pdfium
    guard = threading.Lock()
    active = 0
    peak = 0

    def probe(*args, **kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.05)
            return original(*args, **kwargs)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(batch_module, "_render_with_pdfium", probe)
    threads = [
        threading.Thread(target=render_page_png, args=(assets.ui,), kwargs={"max_dimension": 64})
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert peak == 1


def test_concurrent_page_renders_keep_working(assets) -> None:
    """After concurrent renders PDFium must still render (the old race poisoned it)."""
    results: list[bytes | None] = []
    guard = threading.Lock()

    def render() -> None:
        data = render_page_png(assets.ui, max_dimension=96)
        with guard:
            results.append(data)

    for _ in range(2):
        barrier = threading.Barrier(2)

        def racing_render(barrier: threading.Barrier = barrier) -> None:
            barrier.wait(timeout=30)
            render()

        threads = [threading.Thread(target=racing_render) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    assert len(results) == 4
    assert all(results), "a concurrent render returned no PNG"
    # The old race left PDFium unable to render anything afterwards.
    assert render_page_png(assets.ui, max_dimension=96)


def test_truncated_pdf_renders_from_a_repaired_copy(tmp_path: Path) -> None:
    """A file without a cross-reference table previews via qpdf's rebuild."""
    damaged = build_truncated_pdf(tmp_path / "truncated.pdf")
    with pytest.raises(pdfium.PdfiumError):
        pdfium.PdfDocument(str(damaged)).close()
    png = render_page_png(damaged, zoom_percent=100)
    assert png and png.startswith(b"\x89PNG")


def test_protected_document_page_renders_with_its_password(tmp_path: Path) -> None:
    """The preview must use the password the run used, not the empty one."""
    protected = build_protected_pdf(tmp_path / "protected.pdf", password="secret")
    assert render_page_png(protected, zoom_percent=100) is None
    png = render_page_png(protected, zoom_percent=100, password="secret")
    assert png and png.startswith(b"\x89PNG")


def test_failed_file_leaves_no_temp_and_keeps_the_original(tmp_path: Path) -> None:
    """US-054: compression is all-or-nothing per file."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a pdf")
    original = broken.read_bytes()
    output_dir = tmp_path / "out"

    outcome = compress_pdf_file(
        broken, output_dir / "broken.pdf", CompressionConfig.from_preset("balanced")
    )
    assert outcome.success is False
    assert broken.read_bytes() == original
    assert not (output_dir / "broken.pdf").exists()
    leftovers = list(output_dir.glob(".ozempdf_*")) if output_dir.exists() else []
    assert leftovers == []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io

    from ozempdf.cli import run_cli

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_cli(argv)
    return code, out.getvalue(), err.getvalue()


def test_failed_file_reports_no_savings(tmp_path: Path) -> None:
    """A file that fails contributes to failed_count only (FR-BAT-9)."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"")
    summary = run_batch_compression([(broken, None)], CompressionConfig.from_preset("balanced"))
    result = summary.results[0]
    assert not result.success
    assert result.bytes_saved == 0
    assert result.reduction_percent == 0.0
    assert result.to_dict()["bytes_saved"] == 0
    assert result.to_dict()["reduction_percent"] == 0.0


def test_cli_single_file_and_json_report(assets, tmp_path: Path) -> None:
    output = tmp_path / "out"
    report = tmp_path / "reports" / "result.json"
    code, out, _err = _run_cli(
        [str(assets.ui), "-o", str(output), "--json-report", str(report), "--preset", "balanced"]
    )
    assert code == 0
    assert (output / assets.ui.name).is_file()
    assert report.is_file()
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["succeeded_count"] == 1
    assert document["total_bytes_saved"] > 0
    assert document["files"][0]["output_path"].endswith(assets.ui.name)


def test_cli_json_stdout_is_pure(assets) -> None:
    code, out, err = _run_cli([str(assets.ui), "--json-report", "-"])
    assert code == 0
    document = json.loads(out)
    assert document["total_files"] == 1
    assert err == ""


def test_cli_no_pdfs_exits_one(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _out, err = _run_cli([str(empty)])
    assert code == 1
    assert "No PDF" in err


def test_cli_failure_exits_two(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    code, _out, _err = _run_cli([str(broken)])
    assert code == 2


def test_cli_help_is_localized() -> None:
    from ozempdf.cli import build_arg_parser
    from ozempdf.i18n import t

    help_text = " ".join(build_arg_parser().format_help().split())
    assert " ".join(t("cli.help_inputs").split()) in help_text
    assert "help_inputs" not in help_text


def test_cli_flag_mapping() -> None:
    from ozempdf.cli import build_arg_parser, build_config_from_args

    args = build_arg_parser().parse_args(
        ["a.pdf", "--preset", "lossless", "--max-dpi", "999", "--jpeg-quality", "10", "--overwrite", "--suffix", ""]
    )
    config = build_config_from_args(args)
    assert config.target_dpi == 600
    assert config.jpeg_quality == 50
    assert config.strict_lossless is False
    assert config.output_mode is OutputMode.OVERWRITE
    assert config.output_suffix == "_compressed"
