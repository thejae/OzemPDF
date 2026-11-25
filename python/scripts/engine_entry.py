"""PyInstaller entry point for the frozen OzemPDF engine."""

from ozempdf.cli import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
