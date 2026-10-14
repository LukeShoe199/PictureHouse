"""Build the PictureHouse Docker image (for ZimaOS, other NAS systems, Linux).

    uv run python zimaos/build_docker.py            # image + dist/docker/*.tar.gz
    uv run python zimaos/build_docker.py --push     # image, pushed to ghcr.io
    uv run python zimaos/build_docker.py --no-save  # image only

Tags ghcr.io/lukeshoe199/picturehouse:<version> and :latest. --push uploads
them (after `docker login ghcr.io`), which is what ZimaOS installs from: its
installer always downloads the image, so a loaded-only image can't be
installed from its dashboard. The saved .tar.gz is for plain Docker hosts
without internet access (`docker load -i ...`). Needs Docker.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "dist" / "docker"
IMAGE = "ghcr.io/lukeshoe199/picturehouse"


def version() -> str:
    sys.path.insert(0, str(REPO / "src"))
    from picturehouse import __version__

    return __version__


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-save", action="store_true", help="don't write the .tar.gz")
    ap.add_argument("--push", action="store_true", help="push to ghcr.io (no .tar.gz)")
    a = ap.parse_args()
    docker = shutil.which("docker")
    if not docker:
        print("Docker is needed: https://docs.docker.com/get-docker/", file=sys.stderr)
        return 1
    v = version()
    tags = [f"{IMAGE}:{v}", f"{IMAGE}:latest"]
    cmd = [
        docker,
        "build",
        "--platform",
        "linux/amd64",
        *sum((["-t", t] for t in tags), []),
        str(REPO),
    ]
    print("+", " ".join(cmd[1:]))
    if subprocess.call(cmd) != 0:
        return 1
    if a.push:
        for t in tags:
            print("+ push", t)
            if subprocess.call([docker, "push", t]) != 0:
                return 1
        return 0
    if a.no_save:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"picturehouse-{v}-amd64.tar.gz"
    print(f"saving {dest} ...")
    with subprocess.Popen([docker, "save", *tags], stdout=subprocess.PIPE) as p, \
            gzip.open(dest, "wb", compresslevel=6) as out:  # fmt: skip
        shutil.copyfileobj(p.stdout, out, 1 << 20)
    if p.returncode != 0:
        return 1
    print(f"built {dest} ({dest.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
