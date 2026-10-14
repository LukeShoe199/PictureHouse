"""Artwork lookup: local sidecar images first, then images FFmpeg extracts
(embedded cover art, a video frame). Extracted images are cached in the data
directory. Media folders are never written to.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath

import httpx

from picturehouse.db.models import MediaItem
from picturehouse.ffmpeg import no_window_flags
from picturehouse.paths import long_path, resolve_in_root

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
POSTER_NAMES = ("poster", "folder", "cover", "movie", "default")
COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
BACKDROP_NAMES = ("fanart", "backdrop", "background", "art")
EXTRACT_TIMEOUT = 30
_extract_slots = threading.BoundedSemaphore(2)  # don't let a grid of thumbs fork 50 ffmpegs


def _first_existing(folder: Path, stems: tuple[str, ...]) -> Path | None:
    try:
        names = {p.name.lower(): p for p in long_path(folder).iterdir() if p.is_file()}
    except OSError:
        return None
    for stem in stems:
        for ext in IMAGE_EXTS:
            if p := names.get(stem + ext):
                return p
    return None


def _sidecar(item: MediaItem, root: Path, which: str) -> Path | None:
    rel = PurePosixPath(item.rel_path)
    file = resolve_in_root(root, item.rel_path)
    own_folder = len(rel.parts) > 1
    names = BACKDROP_NAMES if which == "backdrop" else (
        COVER_NAMES if item.kind == "track" else POSTER_NAMES)  # fmt: skip
    # <stem>-poster.jpg / <stem>.jpg next to the file works even for loose files
    per_file = (f"{rel.stem}-{which}".lower(), f"{rel.stem}-fanart".lower()) if which == "backdrop" \
        else (f"{rel.stem}-poster".lower(), rel.stem.lower())  # fmt: skip
    if hit := _first_existing(file.parent, per_file):
        return hit
    if item.kind == "episode" and rel.parts[:-1]:
        # Show-level art lives in the show folder (first component).
        show_dir = root / rel.parts[0]
        season_dir = file.parent
        if which != "backdrop" and season_dir != show_dir:
            season_poster = (f"season{(item.season or 0):02d}-poster",)
            if hit := _first_existing(show_dir, season_poster):
                return hit
        return _first_existing(show_dir, names)
    if own_folder:
        return _first_existing(file.parent, names)
    return None


def _run_extract(cmd: list[str], dest: Path) -> Path | None:
    tmp = dest.with_suffix(".tmp.jpg")
    with _extract_slots:
        if dest.exists():
            return dest
        try:
            proc = subprocess.run(cmd + [str(tmp)], capture_output=True, timeout=EXTRACT_TIMEOUT,
                                  creationflags=no_window_flags())  # fmt: skip
        except (subprocess.TimeoutExpired, OSError) as e:
            log.info("artwork extraction failed: %s", e, extra={"dest": dest.name})
            proc = None
        if proc is None or proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            dest.with_suffix(".none").touch()  # negative cache; cleared when the file changes
            return None
        tmp.replace(dest)
        return dest


def _extracted(item: MediaItem, root: Path, which: str, ffmpeg: Path, cache: Path) -> Path | None:
    cache.mkdir(parents=True, exist_ok=True)
    key = f"{item.id}-{item.mtime_ns}-{which}"
    dest = cache / f"{key}.jpg"
    if dest.exists():
        return dest
    if dest.with_suffix(".none").exists():
        return None
    src = str(long_path(resolve_in_root(root, item.rel_path).absolute()))
    base = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if item.has_embedded_art:
        # Embedded cover (music, some MKVs): copy the attached picture out.
        cmd = [*base, "-i", src, "-map", "0:v:0", "-frames:v", "1", "-vf", "scale='min(800,iw)':-2"]
        return _run_extract(cmd, dest)
    if item.media_type != "video":
        return None
    at = min((item.duration or 0) * 0.15, 300.0)
    width = 1280 if which == "backdrop" else 480
    cmd = [*base, "-ss", f"{at:.2f}", "-i", src, "-map", "0:V:0", "-frames:v", "1",
           "-vf", f"scale='min({width},iw)':-2", "-q:v", "4"]  # fmt: skip
    return _run_extract(cmd, dest)


def _remote(url: str | None, cache: Path) -> Path | None:
    """Download an online image (TMDb) once; failures are remembered for a day."""
    if not url:
        return None
    folder = cache / "remote"
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(url.encode()).hexdigest()
    dest = folder / f"{name}.jpg"
    if dest.exists():
        return dest
    miss = dest.with_suffix(".none")
    if miss.exists() and time.time() - miss.stat().st_mtime < 86400:
        return None
    try:
        r = httpx.get(url, timeout=15.0, follow_redirects=True)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/"):
            raise ValueError("not an image")
    except (httpx.HTTPError, ValueError) as e:
        log.info("artwork download failed: %s", e, extra={"url": url})
        miss.touch()
        return None
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def find_artwork(
    item: MediaItem, root: Path, which: str, ffmpeg: Path, cache: Path,
    remote: dict[str, str | None] | None = None,
) -> Path | None:  # fmt: skip
    """``which``: poster (portrait / album cover), thumb (16:9 still), backdrop.

    Order: local files next to the media, then online artwork (``remote``,
    from TMDb), then what FFmpeg can extract from the file itself.
    """
    remote = remote or {}
    if which == "thumb":
        if item.kind == "track":
            which = "poster"
        else:
            try:  # <name>-thumb.jpg (Kodi; written by the opt-in local save)
                rel = PurePosixPath(item.rel_path)
                folder = resolve_in_root(root, item.rel_path).parent
                if hit := _first_existing(folder, (f"{rel.stem}-thumb".lower(),)):
                    return hit
            except (OSError, ValueError):
                pass
            return _remote(remote.get("thumb"), cache) or _extracted(
                item, root, "thumb", ffmpeg, cache)  # fmt: skip
    try:
        if hit := _sidecar(item, root, which):
            return hit
    except (OSError, ValueError):
        pass
    if hit := _remote(remote.get(which), cache):
        return hit
    if which == "poster":
        # Only real covers make a poster; video frames are the wrong shape.
        return _extracted(item, root, "cover", ffmpeg, cache) if item.has_embedded_art else None
    if which == "backdrop" and item.media_type == "video":
        return _extracted(item, root, "backdrop", ffmpeg, cache)
    return None
