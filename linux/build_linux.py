"""Build the Linux desktop app (window + tray icon) as .deb and .AppImage.

    uv run python linux/build_linux.py

Runs on any machine with Docker (Windows included): builds inside an
Ubuntu 22.04 container (linux/build.Dockerfile) from a copy of this folder,
so nothing here is touched. Output in dist/linux/:
  PictureHouse_<version>_amd64.deb      Ubuntu / Kubuntu / Debian / Mint
  PictureHouse_<version>_amd64.AppImage any distribution: chmod +x and run

Steps inside the container: freeze the server (scripts/build_server.py),
then `tauri build --bundles deb,appimage`, which ships the frozen server
next to the app (desktop/tauri.conf.json "resources"). The Rust and uv
caches live in Docker volumes, so rebuilds are quicker.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "dist" / "linux"
IMAGE = "picturehouse-linux-build"
TAURI_CLI = "@tauri-apps/cli@2.11.5"

# Copied into the container: the sources only, not Windows build output.
INCLUDE = [
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "src",
    "packaging",
    "scripts",
    "desktop",
]
EXCLUDE = {"target", "gen", "__pycache__", "node_modules"}

STEPS = f"""
set -euo pipefail
mkdir -p /work && cd /work
tar -xf -
uv sync --frozen --group dev
uv run python scripts/build_server.py
cd desktop
npx --yes {TAURI_CLI} build --bundles deb,appimage
cp target/release/bundle/deb/*.deb target/release/bundle/appimage/*.AppImage /out/
"""


def _sources_only(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if EXCLUDE & set(Path(info.name).parts[1:]):
        return None
    if info.name.endswith(".sh") or info.name.endswith("build_server.py"):
        info.mode = 0o755
    return info


def main() -> int:
    docker = shutil.which("docker") or sys.exit(
        "Docker is needed: https://docs.docker.com/get-docker/"
    )
    run = lambda *a, **k: subprocess.run([docker, *a], check=True, **k)  # noqa: E731
    print("+ building the build environment (first time: a few minutes)")
    run("build", "-t", IMAGE, "-f", str(REPO / "linux" / "build.Dockerfile"), str(REPO / "linux"))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*"):
        old.unlink()
    print("+ building PictureHouse for Linux")
    proc = subprocess.Popen(
        [docker, "run", "--rm", "-i",
         "-v", f"{OUT}:/out",
         "-v", "picturehouse-cargo:/root/.cargo/registry",
         "-v", "picturehouse-target:/work/desktop/target",
         "-v", "picturehouse-uv:/root/.cache/uv",
         IMAGE, "bash", "-c", STEPS],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    # Python's tarfile, not tar.exe: Windows' tar has cut archives short here.
    with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for name in INCLUDE:
            tar.add(REPO / name, arcname=name, filter=_sources_only)
    proc.stdin.close()
    if proc.wait() != 0:
        return 1
    for f in sorted(OUT.iterdir()):
        print(f"built {f.relative_to(REPO)} ({f.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
