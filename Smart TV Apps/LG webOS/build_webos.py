"""Build (and optionally install) the PictureHouse app for LG webOS TVs.

    uv run python "Smart TV Apps/LG webOS/build_webos.py" --setup tv --tv-ip 192.168.1.50   # once
    uv run python "Smart TV Apps/LG webOS/build_webos.py"                 # build dist/webos/*.ipk
    uv run python "Smart TV Apps/LG webOS/build_webos.py" --install tv    # build, install, start it
    uv run python "Smart TV Apps/LG webOS/build_webos.py" --extend tv     # extend Developer Mode

"tv" is just a name for the TV, given once with --setup (see README.md here).
Uses LG's webOS CLI through npx, so only Node.js needs to be installed.
The app is a small launcher: it finds the server and opens /tv/, the
interface PictureHouse serves, so most updates need no reinstall.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent  # the app's files sit next to this script
REPO = SRC.parent.parent
BUILD = REPO / "build" / "webos-app"
OUT = REPO / "dist" / "webos"
CLI = "@webos-tools/cli@3.2.6"  # LG's official webOS CLI (ares-*)
APP_ID = json.loads((SRC / "appinfo.json").read_text(encoding="utf-8"))["id"]
DEVMODE_APP = "com.palmdts.devmode"


def ares(tool: str, *args: str) -> int:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        print("Node.js is needed (it provides npx): https://nodejs.org", file=sys.stderr)
        return 1
    cmd = [npx, "--yes", "-p", CLI, tool, *args]
    print("+", " ".join(cmd[3:]))
    return subprocess.call(cmd)


def build() -> Path | None:
    sys.path.insert(0, str(REPO / "src"))
    from picturehouse.dlna.xmldocs import icon_png

    if BUILD.exists():
        shutil.rmtree(BUILD)
    # The app itself only: not this script or the README.
    shutil.copytree(SRC, BUILD, ignore=shutil.ignore_patterns("*.py", "*.md", "__pycache__"))
    (BUILD / "icon.png").write_bytes(icon_png(80))  # launcher bar
    (BUILD / "largeIcon.png").write_bytes(icon_png(130))  # app list
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.ipk"):
        old.unlink()
    # --no-minify: the scripts are tiny, and readable on the TV's web inspector.
    if ares("ares-package", str(BUILD), "--outdir", str(OUT), "--no-minify") != 0:
        return None
    ipk = next(OUT.glob("*.ipk"), None)
    if ipk:
        print(f"built {ipk}")
    return ipk


def setup(device: str, ip: str) -> int:
    """Register the TV with the webOS CLI and fetch its SSH key (asks for the
    passphrase shown in the TV's Developer Mode app; Key Server must be on)."""
    ares("ares-setup-device", "--remove", device)  # fine if it wasn't there yet
    rc = ares("ares-setup-device", "--add", device, "--info", "username=prisoner",
              "--info", f"host={ip}", "--info", "port=9922")  # fmt: skip
    if rc != 0:
        return rc
    print("\nType the 6-character passphrase shown in the TV's Developer Mode app:")
    if ares("ares-novacom", "--device", device, "--getkey") != 0:
        print("Couldn't get the key: is Key Server switched on in the Developer Mode app?",
              file=sys.stderr)  # fmt: skip
        return 1
    return ares("ares-device", "--device", device, "--system-info")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup", metavar="DEVICE", help="register the TV once (with --tv-ip)")
    ap.add_argument("--tv-ip", help="the TV's IP address, shown in its Developer Mode app")
    ap.add_argument("--install", metavar="DEVICE", help="install on this TV and start it")
    ap.add_argument("--extend", metavar="DEVICE",
                    help="extend the TV's Developer Mode session (else apps vanish when it ends)")  # fmt: skip
    a = ap.parse_args()
    if a.setup:
        if not a.tv_ip:
            ap.error("--setup needs --tv-ip (shown in the TV's Developer Mode app)")
        return setup(a.setup, a.tv_ip)
    if a.extend:
        return ares("ares-launch", "--device", a.extend, DEVMODE_APP, "--params", '{"extend":true}')
    ipk = build()
    if ipk is None:
        return 1
    if a.install:
        if ares("ares-install", "--device", a.install, str(ipk)) != 0:
            return 1
        return ares("ares-launch", "--device", a.install, APP_ID)
    return 0


if __name__ == "__main__":
    sys.exit(main())
