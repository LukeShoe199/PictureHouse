"""Build the Windows downloads: an installer and a portable zip.

    uv run python windows/build_windows.py               # build everything
    uv run python windows/build_windows.py --skip-build  # repackage last build

Output in dist/windows/:
  PictureHouse-<version>-setup.exe              Inno Setup installer
  PictureHouse-<version>-windows-portable.zip   unzip anywhere and run

Steps: freeze the server (scripts/build_server.py), build the desktop app
(Tauri, no bundle), stage both in dist/windows/PictureHouse, compile
windows/picturehouse.iss, then zip the staged folder.
Needs Node (npx), Rust and Inno Setup 6.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "dist" / "windows"
STAGE = OUT / "PictureHouse"
TAURI_CLI = "@tauri-apps/cli@2.11.5"


def version() -> str:
    sys.path.insert(0, str(REPO / "src"))
    from picturehouse import __version__

    desktop = json.loads((REPO / "desktop" / "tauri.conf.json").read_text("utf-8"))["version"]
    if desktop != __version__:
        sys.exit(f"version mismatch: package {__version__}, desktop/tauri.conf.json {desktop}")
    return __version__


def iscc() -> str:
    found = shutil.which("iscc")
    if found:
        return found
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(env)
        sub = "Programs/Inno Setup 6" if env == "LOCALAPPDATA" else "Inno Setup 6"
        if root and (Path(root) / sub / "ISCC.exe").is_file():
            return str(Path(root) / sub / "ISCC.exe")
    sys.exit("Inno Setup 6 is needed: winget install JRSoftware.InnoSetup")


def run(cmd: list[str], cwd: Path = REPO) -> None:
    print("+", " ".join(cmd), flush=True)
    if subprocess.call(cmd, cwd=cwd) != 0:
        sys.exit(f"failed: {cmd[0]}")


def build() -> None:
    run([sys.executable, str(REPO / "scripts" / "build_server.py")])
    npx = shutil.which("npx") or sys.exit("Node.js (npx) is needed to build the desktop app")
    # Rust records source paths (for panic messages); don't ship the builder's
    # user name and folders: C:\Users\<name>\.cargo\... -> ~/..., repo -> /build.
    home = Path.home()
    # CARGO_ENCODED_RUSTFLAGS (0x1f-separated), as the paths may contain spaces.
    flags = [f"--remap-path-prefix={home}=~", f"--remap-path-prefix={REPO}=/build"]
    env = {**os.environ, "CARGO_ENCODED_RUSTFLAGS": chr(0x1F).join(flags)}
    env.pop("RUSTFLAGS", None)
    print("+", npx, "--yes", TAURI_CLI, "build", "--no-bundle", flush=True)
    if subprocess.call(
        [npx, "--yes", TAURI_CLI, "build", "--no-bundle"], cwd=REPO / "desktop", env=env
    ):
        sys.exit("failed: tauri build")


def stage() -> None:
    exe = REPO / "desktop" / "target" / "release" / "PictureHouse.exe"
    server = REPO / "dist" / "picturehouse-server"
    for need in (exe, server / "picturehouse-server.exe"):
        if not need.is_file():
            sys.exit(f"missing {need}: run without --skip-build")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    shutil.copy2(exe, STAGE / exe.name)
    shutil.copytree(server, STAGE / "server")


def portable_zip(v: str) -> Path:
    dest = OUT / f"PictureHouse-{v}-windows-portable.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                z.write(f, Path("PictureHouse") / f.relative_to(STAGE))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-build", action="store_true", help="reuse the last server/app build")
    a = ap.parse_args()
    v = version()
    if not a.skip_build:
        build()
    stage()
    iss = REPO / "windows" / "picturehouse.iss"
    run([iscc(), "/Q", f"/DAppVersion={v}", f"/DSourceDir={STAGE}", f"/DOutputDir={OUT}", str(iss)])
    setup = OUT / f"PictureHouse-{v}-setup.exe"
    zipped = portable_zip(v)
    for f in (setup, zipped):
        print(f"built {f.relative_to(REPO)} ({f.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
