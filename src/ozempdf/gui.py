"""Desktop entry point retained for the ``ozempdf-gui`` script.

The desktop experience is the local web app; the Electron shell in ``app/``
wraps it, and this function lets the console script launch the same surface.
"""

from __future__ import annotations

from collections.abc import Sequence


def launch_gui(initial_paths: Sequence[str] | None = None) -> int:
    """Open the OzemPDF interface in the default browser."""
    from .server import serve

    return serve(open_browser=True, preload=list(initial_paths or []))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(launch_gui())
