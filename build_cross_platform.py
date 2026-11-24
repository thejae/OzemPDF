"""Build the frozen Python engine that the Electron shell ships as a sidecar.

Usage:
    python build_cross_platform.py

Output:
    dist/engine/ozempdf-engine/ozempdf-engine[.exe]

Then package the desktop app:
    cd app && npm install && npm run dist
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
DIST = ROOT / "dist" / "engine"
REQUIRED_MODULES = (
    "pikepdf",
    "PIL",
    "zopfli",
    "fontTools",
    "pypdfium2",
    "pyhanko",
)


def check_dependencies() -> None:
    """Fail early with a useful message when a runtime dependency is missing."""
    missing = []
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise SystemExit(
            "Missing runtime dependencies: "
            + ", ".join(missing)
            + '\nRun: pip install -e ".[dev]"'
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit('PyInstaller is required: pip install -e ".[dev]"') from None


def build() -> Path:
    """Run PyInstaller and return the produced binary."""
    check_dependencies()
    separator = ";" if os.name == "nt" else ":"
    data = [
        (SRC / "ozempdf" / "locales", "ozempdf/locales"),
        (SRC / "ozempdf" / "web", "ozempdf/web"),
    ]
    if DIST.exists():
        shutil.rmtree(DIST)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "ozempdf-engine",
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(SRC),
        "--collect-all",
        "pikepdf",
        "--collect-all",
        "pypdfium2",
        "--collect-all",
        "pypdfium2_raw",
        "--collect-all",
        "zopfli",
        "--collect-submodules",
        "fontTools",
        "--collect-submodules",
        "pyhanko",
        "--hidden-import",
        "pikepdf._core",
        "--hidden-import",
        "pikepdf.models",
        "--hidden-import",
        "zopfli.zlib",
        "--hidden-import",
        "fontTools.ttLib",
        "--hidden-import",
        "fontTools.subset",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "pypdfium2",
        "--hidden-import",
        "pyhanko.sign",
        "--hidden-import",
        "pyhanko.sign.signers",
        "--hidden-import",
        "pyhanko.pdf_utils.incremental_writer",
    ]
    for source, destination in data:
        command += ["--add-data", f"{source}{separator}{destination}"]
    command.append(str(ROOT / "scripts" / "engine_entry.py"))

    print(" ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller failed with exit code {result.returncode}")

    name = "ozempdf-engine.exe" if os.name == "nt" else "ozempdf-engine"
    binary = DIST / "ozempdf-engine" / name
    if not binary.is_file():
        raise SystemExit(f"Expected binary not found: {binary}")
    print(f"\nEngine built: {binary}")
    print("Next: cd app && npm install && npm run dist")
    return binary


if __name__ == "__main__":
    build()
