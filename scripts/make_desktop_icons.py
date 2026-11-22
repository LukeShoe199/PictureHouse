"""Generate the app icons from the same artwork the server uses
(picturehouse.dlna.xmldocs.logo_image).

    uv run python scripts/make_desktop_icons.py

Writes desktop/icons/: icon.ico (16-256 px, PNG-compressed entries) plus the
PNG sizes Tauri's bundler expects, and the Android app's launcher icons
(square and round, every density).
"""

from __future__ import annotations

import struct
from pathlib import Path

from picturehouse.dlna.xmldocs import icon_png

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "desktop" / "icons"
ANDROID = ROOT / "Android APK" / "app" / "src" / "main" / "res"
ANDROID_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNGS = {"32x32.png": 32, "128x128.png": 128, "128x128@2x.png": 256, "icon.png": 512}


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    images = [icon_png(n, rgba=True) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for n, data in zip(sizes, images, strict=True):
        # width/height 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_ico(OUT / "icon.ico", ICO_SIZES)
    for name, size in PNGS.items():
        (OUT / name).write_bytes(icon_png(size, rgba=True))
    print(f"wrote icons to {OUT}")
    if ANDROID.is_dir():
        for density, size in ANDROID_SIZES.items():
            d = ANDROID / f"mipmap-{density}"
            d.mkdir(exist_ok=True)
            (d / "ic_launcher.png").write_bytes(icon_png(size, rgba=True))
            (d / "ic_launcher_round.png").write_bytes(icon_png(size, rgba=True, round_=True))
        print(f"wrote Android icons to {ANDROID}")


if __name__ == "__main__":
    main()
