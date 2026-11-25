"""Guards for the frozen sidecar that the desktop app ships and launches.

The frozen engine is built by ``python/scripts/build_cross_platform.py`` from
``python/scripts/engine_entry.py``. Its packaging requirements cannot be
exercised from the source tree, so they are asserted against the entry script.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "python" / "scripts" / "engine_entry.py"


def test_frozen_entry_consumes_multiprocessing_spawn_arguments() -> None:
    """Call freeze_support before argv reaches the parser.

    Windows starts pool workers by re-running the frozen executable with a
    ``--multiprocessing-fork`` payload. Without ``freeze_support()`` argparse
    sees that flag, exits with ``unrecognized arguments``, and every batch of
    two or more files fails.
    """
    source = ENTRY.read_text(encoding="utf-8")
    assert "multiprocessing.freeze_support()" in source
    assert source.index("multiprocessing.freeze_support()") < source.index("run_cli()")


def test_batch_runs_files_in_parallel_when_more_than_one() -> None:
    """The pool path that needs the frozen fix is still the shipped default."""
    source = (ROOT / "python" / "src" / "ozempdf" / "engine" / "batch.py").read_text(
        encoding="utf-8"
    )
    assert "ProcessPoolExecutor" in source
