"""PyInstaller entry point for the frozen OzemPDF engine.

Windows starts pool workers by re-running this executable with a
``--multiprocessing-fork`` payload. ``multiprocessing.freeze_support()`` must be
called before anything reads ``sys.argv``, so the payload is consumed here
instead of reaching argparse; otherwise every batch of two or more files fails
with ``unrecognized arguments: --multiprocessing-fork``.
"""

import multiprocessing

from ozempdf.cli import run_cli

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(run_cli())
