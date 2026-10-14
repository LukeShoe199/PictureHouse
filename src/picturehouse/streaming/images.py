"""Photo renditions: thumbnails for grids, screen-sized images for the viewer,
slideshows and the TV.

Rendered once with Pillow (EXIF rotation applied, HEIC converted) and cached
in the data directory under artwork/photos/, keyed by item id, file mtime
and size, so an edited photo gets new renditions and nothing is ever written
next to the originals.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import Image, ImageOps

from picturehouse.library.photos import _register_heif
from picturehouse.paths import long_path

log = logging.getLogger(__name__)

# Longest side, pixels. "tv": slideshows on TVs, a quarter of the pixels of
# "screen" to decode, which a 2020 TV's CPU needs while it also plays music.
SIZES = {"thumb": 480, "tv": 1280, "screen": 1920}
_render_slots = threading.BoundedSemaphore(3)  # a grid of thumbs mustn't eat every core


def rendition(src: Path, cache: Path, item_id: int, mtime_ns: int, size: str) -> Path:
    """JPEG of the photo at most SIZES[size] on its longest side, upright."""
    box = SIZES[size]
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{item_id}-{mtime_ns}-{size}.jpg"
    if dest.exists():
        return dest
    _register_heif()
    with _render_slots:
        if dest.exists():  # rendered by a parallel request while we waited
            return dest
        with Image.open(long_path(src.absolute())) as im:
            im.draft("RGB", (box, box))  # JPEG: decode at reduced size, much faster
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, (10, 13, 17))  # transparent PNGs on the app's dark
                bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
                im = bg
            im.thumbnail((box, box), Image.Resampling.LANCZOS)
            tmp = dest.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=84 if size == "thumb" else 88, optimize=True,
                    progressive=True)  # fmt: skip
        tmp.replace(dest)
    return dest


WIDTHS = (160, 240, 320, 480, 640, 960)  # sizes we resize artwork to (cache stays small)


def resized(src: Path, cache: Path, width: int) -> Path:
    """Artwork (a poster, cover or still) scaled down to ``width`` pixels wide
    for grids: a 300 px card doesn't need a 2000 px poster, and on a TV with
    little memory every decoded image counts. Never enlarged; cached."""
    import hashlib

    w = next((x for x in WIDTHS if x >= width), WIDTHS[-1])
    st = src.stat()
    key = hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20]
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{key}-{w}.jpg"
    if dest.exists():
        return dest
    with _render_slots:
        if dest.exists():
            return dest
        with Image.open(long_path(src.absolute())) as im:
            if im.width <= w:
                return src  # already small enough
            im.draft("RGB", (w, w * 3))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((w, w * 4), Image.Resampling.LANCZOS)
            tmp = dest.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=82, optimize=True, progressive=True)
        tmp.replace(dest)
    return dest
