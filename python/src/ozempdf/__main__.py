"""Module entry point: ``python -m ozempdf``."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    """Run the CLI, with frozen-build multiprocessing support (NFR-12)."""
    multiprocessing.freeze_support()
    from .cli import run_cli

    return run_cli()


if __name__ == "__main__":
    sys.exit(main())
