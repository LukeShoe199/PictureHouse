"""Freeze the PictureHouse server into a standalone folder with PyInstaller.

    uv run python scripts/build_server.py

Output: dist/picturehouse-server/picturehouse-server[.exe] (+ _internal/). The
desktop app bundles this folder, so the installed app needs no Python.
FFmpeg is not bundled: the server finds it on PATH or via config, and says
so clearly if it is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "picturehouse"
SEP = os.pathsep  # PyInstaller's --add-data separator is ';' on Windows


def _strip_build_paths(out: Path) -> None:
    """Drop files that record where it was built (the builder's folders and
    user name): pip's direct_url.json, and .pyc caches that came along with
    the migration scripts (they're recompiled from the .py on first use)."""
    for f in out.rglob("direct_url.json"):
        f.unlink()
    for d in sorted(out.rglob("__pycache__"), reverse=True):
        if "picturehouse" in d.parts:
            shutil.rmtree(d)


def main() -> int:
    args = [
        str(REPO / "packaging" / "picturehouse_server.py"),
        "--name", "picturehouse-server",
        "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", str(REPO / "dist"),
        "--workpath", str(REPO / "build" / "pyinstaller"),
        "--specpath", str(REPO / "build" / "pyinstaller"),
        "--icon", str(REPO / "desktop" / "icons" / "icon.ico"),
        # Alembic reads migration scripts from disk: ship them as files, .py included.
        "--add-data", f"{PKG / 'db' / 'migrations'}{SEP}picturehouse/db/migrations",
        # Web console, config template and other package data.
        "--collect-data", "picturehouse",
        "--copy-metadata", "picturehouse",  # so --version and the API report it
        "--collect-submodules", "picturehouse",
        # guessit/babelfish/rebulk load data tables and rules at runtime.
        "--collect-all", "guessit", "--collect-all", "babelfish", "--collect-all", "rebulk",
        # Imported dynamically by name.
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "anyio",
        "--hidden-import", "sqlalchemy.dialects.sqlite",
    ]  # fmt: skip
    PyInstaller.__main__.run(args)
    _strip_build_paths(REPO / "dist" / "picturehouse-server")
    exe = (
        REPO
        / "dist"
        / "picturehouse-server"
        / f"picturehouse-server{'.exe' if os.name == 'nt' else ''}"
    )
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    print(f"built {exe}: {out.stdout.strip() or out.stderr.strip()}")
    return out.returncode


if __name__ == "__main__":
    sys.exit(main())
