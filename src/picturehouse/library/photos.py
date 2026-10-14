"""Photos: what a photo library holds, read with Pillow (not ffprobe).

A library of kind ``photos`` holds pictures (and usually a few phone videos,
which are treated as home videos). Each picture becomes a MediaItem with
``media_type="image"``, ``kind="photo"``:

* ``album``       the folder it's in, relative to the library ("2023/Cornwall")
* ``title``       the file name without extension
* ``recorded_at`` when it was taken (EXIF), else the file's modified time
* ``width``/``height`` as displayed, i.e. after the EXIF rotation

iPhone HEIC/HEIF photos are read through pillow-heif. Browsers and TVs can't
show HEIC, so they're always served converted (streaming/images.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from picturehouse.paths import long_path

log = logging.getLogger(__name__)

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
# Camera RAW files are never photos here: they're huge, slow to decode, and
# almost always sit beside the JPEG the camera made of the same shot.
RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".raf",
            ".orf", ".rw2", ".rwl", ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".mos",
            ".mrw", ".kdc", ".dcr", ".erf", ".raw"}  # fmt: skip
# Served as they are (when not rotated by EXIF); everything else is converted.
WEB_SAFE = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME = 306
_EXIF_ORIENTATION = 274
_EXIF_IFD = 0x8769

_heif_ready = False


def _register_heif() -> None:
    global _heif_ready
    if not _heif_ready:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:  # optional: HEIC photos then fail to read, nothing else
            log.warning("pillow-heif not installed: HEIC photos can't be read")
        _heif_ready = True


def is_photo(name: str) -> bool:
    ext = PurePosixPath(name).suffix.lower()
    return ext in PHOTO_EXTS and ext not in RAW_EXTS


class PhotoError(Exception):
    pass


@dataclass
class PhotoInfo:
    width: int
    height: int
    taken_at: datetime | None
    orientation: int  # EXIF 1..8; 1 = upright
    format: str  # JPEG, PNG, HEIF...


def _parse_exif_date(v) -> datetime | None:
    if not v:
        return None
    try:
        s = v.decode() if isinstance(v, bytes) else str(v)
        # "2023:08:12 09:30:00"; no time zone in EXIF: treat as local wall time,
        # stored as UTC-naive-equivalent (it's only used for ordering/display).
        return datetime.strptime(s.strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def read_photo(path: Path) -> PhotoInfo:
    """Size, orientation and date taken, without decoding the pixels."""
    _register_heif()
    try:
        with Image.open(long_path(path.absolute())) as im:
            w, h = im.size
            exif = im.getexif()
            orientation = int(exif.get(_EXIF_ORIENTATION) or 1)
            taken = _parse_exif_date(exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL)) \
                or _parse_exif_date(exif.get(_EXIF_DATETIME))  # fmt: skip
            fmt = im.format or ""
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as e:
        raise PhotoError(f"not a readable picture: {e}") from e
    if orientation in (5, 6, 7, 8):  # rotated 90/270: displayed sideways
        w, h = h, w
    return PhotoInfo(w, h, taken, orientation, fmt)


def album_of(rel_path: str) -> str:
    """The folder a photo is in ("" for the library's top level)."""
    parent = str(PurePosixPath(rel_path).parent)
    return "" if parent == "." else parent
