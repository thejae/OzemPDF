"""Command-line interface.

Flags mirror the web UI's knobs; every user-facing string (including ``--help``)
comes from the locale catalog, which fixes defect D-11.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._version import __version__
from .config import CompressionConfig, OutputMode, clamp
from .engine import (
    BatchSummary,
    discover_pdf_files,
    format_bytes,
    run_batch_compression,
)
from .i18n import bootstrap, t
from .logging_setup import configure_logging

EXIT_OK = 0
EXIT_NO_PDFS = 1
EXIT_FAILURES = 2


def _prescan_language(argv: list[str]) -> str | None:
    """Find ``--lang X`` / ``--lang=X`` before argparse builds its help text."""
    for index, token in enumerate(argv):
        if token == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--lang="):
            return token.split("=", 1)[1]
    return None


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the parser with localized descriptions and help strings."""
    parser = argparse.ArgumentParser(
        prog="ozempdf",
        description=t("cli.description"),
        epilog=t("cli.epilog"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help=t("cli.help_help"))
    parser.add_argument(
        "inputs", nargs="*", metavar=t("cli.metavar_inputs"), help=t("cli.help_inputs")
    )
    parser.add_argument("--gui", action="store_true", help=t("cli.help_gui"))
    parser.add_argument("--serve", action="store_true", help=t("cli.help_serve"))
    parser.add_argument("--port", type=int, default=0, help=t("cli.help_port"))
    parser.add_argument("--no-browser", action="store_true", help=t("cli.help_no_browser"))
    parser.add_argument(
        "-p", "--preset", choices=["lossless", "balanced", "max"], default="balanced",
        help=t("cli.help_preset"),
    )
    parser.add_argument("--max-dpi", type=int, help=t("cli.help_max_dpi"))
    parser.add_argument("--jpeg-quality", type=int, help=t("cli.help_jpeg_quality"))
    parser.add_argument("--zopfli-iterations", type=int, help=t("cli.help_zopfli"))
    parser.add_argument("--no-subset-fonts", action="store_true", help=t("cli.help_no_fonts"))
    parser.add_argument("--strip-metadata", action="store_true", help=t("cli.help_strip_meta"))
    parser.add_argument(
        "--flatten-signatures", action="store_true", help=t("cli.help_flatten")
    )
    parser.add_argument("-o", "--output-dir", help=t("cli.help_output_dir"))
    parser.add_argument("--suffix", default=None, help=t("cli.help_suffix"))
    parser.add_argument("--overwrite", action="store_true", help=t("cli.help_overwrite"))
    parser.add_argument("-w", "--workers", type=int, help=t("cli.help_workers"))
    parser.add_argument("--password", default="", help=t("cli.help_password"))
    parser.add_argument("--json-report", dest="json_report", help=t("cli.help_json_report"))
    parser.add_argument("--sign-p12", dest="sign_p12", help=t("cli.help_sign_p12"))
    parser.add_argument("--sign-password", dest="sign_password", help=t("cli.help_sign_password"))
    parser.add_argument("--sign-reason", dest="sign_reason", help=t("cli.help_sign_reason"))
    parser.add_argument("--sign-tsa", dest="sign_tsa", help=t("cli.help_sign_tsa"))
    parser.add_argument("--lang", help=t("cli.help_lang"))
    parser.add_argument("--verbose", action="store_true", help=t("cli.help_verbose"))
    parser.add_argument(
        "-v", "--version", action="version", version=t("cli.version", version=__version__)
    )
    return parser


def build_config_from_args(args: argparse.Namespace) -> CompressionConfig:
    """Map parsed flags onto a clamped configuration."""
    config = CompressionConfig.from_preset(args.preset)
    if args.max_dpi is not None:
        config.target_dpi = clamp(args.max_dpi, 72, 600)
        config.enable_downsampling = True
        config.strict_lossless = False
    if args.jpeg_quality is not None:
        config.jpeg_quality = args.jpeg_quality
        config.strict_lossless = False
    if args.zopfli_iterations is not None:
        config.zopfli_iterations = args.zopfli_iterations
    if args.no_subset_fonts:
        config.subset_fonts = False
    if args.strip_metadata:
        config.strip_metadata = True
    if args.flatten_signatures:
        config.unlock_and_flatten_signatures = True
    if args.overwrite:
        config.output_mode = OutputMode.OVERWRITE
    elif args.output_dir:
        config.output_mode = OutputMode.CUSTOM_FOLDER
        config.output_dir = str(Path(args.output_dir).expanduser())
    if args.suffix is not None:
        config.output_suffix = args.suffix
    if args.workers is not None:
        config.max_workers = max(0, args.workers)
    config.password = args.password or ""
    if args.sign_p12:
        config.sign_after_slimming = True
        config.sign_pkcs12_path = str(Path(args.sign_p12).expanduser())
        config.sign_pkcs12_password = args.sign_password or ""
    if args.sign_reason:
        config.sign_reason = args.sign_reason
    if args.sign_tsa:
        config.sign_tsa_url = args.sign_tsa
    return config.clamped()


def _summarise(summary: BatchSummary, as_json: bool) -> None:
    """Print the batch summary block, or the JSON document."""
    if as_json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
        return
    print("-" * 72)
    print(t("cli.summary_title", version=__version__))
    print(
        t(
            "cli.summary_line",
            done=summary.succeeded_count,
            total=summary.total_files,
            seconds=f"{summary.elapsed_seconds:.2f}",
            before=format_bytes(summary.total_original_bytes),
            after=format_bytes(summary.total_compressed_bytes),
            saved=format_bytes(summary.total_bytes_saved),
            percent=f"{summary.total_reduction_percent:.1f}",
        )
    )
    before = summary.aggregate_before_breakdown
    after = summary.aggregate_after_breakdown

    def delta(before_bytes: int, after_bytes: int) -> str:
        removed = max(0, before_bytes - after_bytes)
        return f"-{format_bytes(removed)}" if removed else format_bytes(0)

    parts = [
        f"{t('step3.cat_images')} {delta(before.images_bytes, after.images_bytes)}",
        f"{t('step3.cat_fonts')} {delta(before.fonts_bytes, after.fonts_bytes)}",
        f"{t('step3.cat_vectors')} {delta(before.vectors_bytes, after.vectors_bytes)}",
        f"{t('step3.cat_metadata')} {delta(before.metadata_bytes, after.metadata_bytes)}",
    ]
    print(t("cli.breakdown", items=" | ".join(parts)))
    if summary.already_optimal_count:
        print(t("cli.already_optimal", count=summary.already_optimal_count))
    if summary.protection_removed_count:
        if summary.protection_removed_count == 1:
            print(t("cli.unlocked_one"))
        else:
            print(t("cli.unlocked", count=summary.protection_removed_count))
    if summary.signed_count:
        print(t("cli.signed", count=summary.signed_count))


def _run_cli(args: argparse.Namespace) -> int:
    """Compress the requested inputs and return a deterministic exit code."""
    config = build_config_from_args(args)
    exclude = config.output_suffix if config.output_mode is OutputMode.SUFFIX else ""
    pairs = discover_pdf_files(args.inputs, exclude_suffix=exclude)
    if not pairs:
        print(t("cli.no_pdfs"), file=sys.stderr)
        return EXIT_NO_PDFS

    as_json_stdout = args.json_report == "-"

    def progress(done: int, count: int, result) -> None:
        if as_json_stdout:
            return
        if result.success:
            print(
                t(
                    "cli.file_ok",
                    index=done,
                    total=count,
                    name=Path(result.input_path).name,
                    before=format_bytes(result.original_bytes),
                    after=format_bytes(result.compressed_bytes),
                    percent=f"{result.reduction_percent:.1f}",
                    output=Path(result.output_path).name,
                )
            )
        else:
            print(
                t(
                    "cli.file_err",
                    index=done,
                    total=count,
                    name=Path(result.input_path).name,
                    error=result.error_message,
                ),
                file=sys.stderr,
            )

    summary = run_batch_compression(pairs, config, progress)

    if args.json_report:
        _write_json_report(summary, args.json_report)
    if not as_json_stdout:
        _summarise(summary, as_json=False)
    elif not args.json_report or args.json_report != "-":
        _summarise(summary, as_json=True)

    return EXIT_FAILURES if summary.failed_count else EXIT_OK


def _write_json_report(summary: BatchSummary, destination: str) -> None:
    """Write the JSON report to a file, ``-`` meaning stdout."""
    document = json.dumps(summary.to_dict(), indent=2, ensure_ascii=False)
    if destination == "-":
        print(document)
        return
    path = Path(destination).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
    except OSError as exc:
        print(t("cli.report_failed", error=str(exc)), file=sys.stderr)


def run_cli(argv: list[str] | None = None) -> int:
    """Entry point for the console script."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "serve" and not Path("serve").exists():
        args_list[0] = "--serve"
    bootstrap(_prescan_language(args_list))
    parser = build_arg_parser()
    args = parser.parse_args(args_list)
    configure_logging(verbose=args.verbose)
    if args.lang:
        bootstrap(args.lang)

    wants_gui = args.gui or args.serve or not args.inputs
    if wants_gui:
        from .server import serve

        return serve(
            port=args.port,
            open_browser=not args.no_browser,
            preload=args.inputs,
        )
    return _run_cli(args)


__all__ = ["build_arg_parser", "build_config_from_args", "run_cli"]
