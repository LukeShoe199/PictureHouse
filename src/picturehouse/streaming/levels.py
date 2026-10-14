"""Precomputed music levels for the visualiser on TVs.

Browsers analyse playing audio themselves (Web Audio), but a webOS TV plays
<audio> through its hardware decoder and the page's analyser hears silence.
So the server analyses a track once with FFmpeg: 13 frequency bands, 20
times a second, as bytes 0-255. The TV draws the frame that matches the
playback position, which stays in time with the music and costs it nothing.

Cached in the data directory, keyed by item id and file mtime.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from pathlib import Path

from picturehouse.ffmpeg import no_window_flags
from picturehouse.paths import long_path

log = logging.getLogger(__name__)

FPS = 20
RATE = 22050  # Hz: enough for the top band, quick to filter
BANDS = (40, 63, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 9000)  # centre Hz
FLOOR_DB = -60.0
MAX_SECONDS = 2 * 3600  # longer recordings get no levels (the visualiser idles)
TIMEOUT = 180
_slots = threading.BoundedSemaphore(2)


class LevelsError(Exception):
    pass


def _graph(tmp: Path) -> str:
    n = len(BANDS)
    split = "".join(f"[s{i}]" for i in range(n))
    g = [f"[0:a]aformat=channel_layouts=mono,aresample={RATE},"
         f"asetnsamples=n={RATE // FPS}:p=0,asplit={n}{split}"]  # fmt: skip
    for i, f in enumerate(BANDS):
        out = (tmp / f"b{i}.txt").as_posix().replace(":", "\\:")
        g.append(f"[s{i}]bandpass=f={f}:width_type=o:w=1,astats=metadata=1:reset=1:"
                 "measure_overall=RMS_level:measure_perchannel=none,"
                 f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file='{out}'[o{i}]")  # fmt: skip
    g.append("".join(f"[o{i}]" for i in range(n)) + f"amix=inputs={n}[out]")
    return ";".join(g)


def _parse(path: Path) -> list[int]:
    vals = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("lavfi.astats.Overall.RMS_level="):
            v = line.split("=", 1)[1].strip()
            try:
                db = float(v)
            except ValueError:  # "-inf" for silence
                db = FLOOR_DB
            vals.append(max(0, min(255, round((db - FLOOR_DB) / -FLOOR_DB * 255))))
    return vals


def compute(ffmpeg: Path, src: Path) -> bytes:
    """frames x bands bytes (frame-major)."""
    with tempfile.TemporaryDirectory(prefix="ph-levels-") as t:
        tmp = Path(t)
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-i", str(long_path(src.absolute())), "-t", str(MAX_SECONDS),
               "-filter_complex", _graph(tmp), "-map", "[out]", "-f", "null", "-"]  # fmt: skip
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT,
                               creationflags=no_window_flags())  # fmt: skip
        except subprocess.TimeoutExpired as e:
            raise LevelsError("analysis took too long") from e
        if r.returncode != 0:
            raise LevelsError(r.stderr.decode("utf-8", "replace").strip()[-300:] or "ffmpeg failed")
        bands = [_parse(tmp / f"b{i}.txt") for i in range(len(BANDS))]
    frames = min(len(b) for b in bands)
    return bytes(bands[b][f] for f in range(frames) for b in range(len(BANDS)))


def levels(ffmpeg: Path, src: Path, cache: Path, item_id: int, mtime_ns: int) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{item_id}-{mtime_ns}.bin"
    if dest.exists():
        return dest.read_bytes()
    with _slots:
        if dest.exists():
            return dest.read_bytes()
        data = compute(ffmpeg, src)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        log.info(
            "visualiser levels computed", extra={"item": item_id, "frames": len(data) // len(BANDS)}
        )
        return data
