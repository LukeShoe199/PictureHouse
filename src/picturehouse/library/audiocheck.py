"""Audio health check: flag films and episodes whose sound looks broken.

The case that prompted it: a "stereo" episode whose two channels were
bit-identical and quiet — music and effects, no voices. That is what you get
when a 5.1 source is converted by keeping a front channel and dropping the
centre channel, where dialogue lives. Nothing downstream can restore it, but
the user should know which files to replace.

Method: decode three short windows (at 20/50/80% of the duration) of the
default audio track to 8 kHz stereo float PCM and measure
  * the programme level (RMS of L+R, dBFS),
  * how different the channels are (RMS of L−R relative to L+R).

Findings, deliberately conservative (old films are often plain mono, and
some mixes are quiet):
  * silent        — every window below SILENT_DB
  * no dialogue?  — 2-channel source, channels identical, and quiet
  * very quiet    — average level below QUIET_DB

Only films and episodes are checked: quiet music or home videos are normal.
"""

from __future__ import annotations

import array
import logging
import math
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select

from picturehouse.db import Database
from picturehouse.db.models import LibraryRootRow, MediaItem, utcnow
from picturehouse.ffmpeg import no_window_flags
from picturehouse.paths import long_path, resolve_in_root

log = logging.getLogger(__name__)

RATE = 8000  # Hz; plenty for level and channel-difference measurements
WINDOW = 45.0  # seconds per sample window
POSITIONS = (0.2, 0.5, 0.8)
SILENT_DB = -60.0
DUAL_MONO_DB = -50.0  # L−R this far below L+R: the channels are the same signal
NO_DIALOGUE_DB = -32.0  # identical channels *and* quieter than this
QUIET_DB = -36.0  # anything quieter than this is worth a look
KINDS = ("film", "episode")


@dataclass(frozen=True)
class AudioStats:
    level_db: float  # programme level, dBFS (RMS of the mono mix)
    diff_db: float  # L−R relative to L+R, dB (very negative = identical channels)
    windows: int

    @property
    def dual_mono(self) -> bool:
        return self.diff_db <= DUAL_MONO_DB


def _db(power: float) -> float:
    return 10 * math.log10(power) if power > 1e-20 else -200.0


def measure(ffmpeg: Path, src: Path, duration: float | None,
            audio_index: int | None = None, timeout: float = 60.0) -> AudioStats:  # fmt: skip
    """Decode the sample windows and measure them. Raises RuntimeError on failure."""
    a_map = f"0:{audio_index}" if audio_index is not None else "0:a:0"
    if duration and duration > WINDOW * 4:
        starts = [max(0.0, duration * p - WINDOW / 2) for p in POSITIONS]
        length = WINDOW
    else:
        starts, length = [0.0], duration or WINDOW  # short file: take all of it
    sum_pow = diff_pow = 0.0
    n_total = 0
    for start in starts:
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(src),
               "-map", a_map, "-vn", "-ac", "2", "-ar", str(RATE), "-f", "f32le", "pipe:1"]  # fmt: skip
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  creationflags=no_window_flags())  # fmt: skip
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("audio decode timed out") from e
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(err[-1] if err else f"ffmpeg exited {proc.returncode}")
        pcm = array.array("f")
        pcm.frombytes(proc.stdout[: len(proc.stdout) // 8 * 8])
        left, right = pcm[0::2], pcm[1::2]
        for lv, rv in zip(left, right, strict=True):
            m = (lv + rv) * 0.5
            d = (lv - rv) * 0.5
            sum_pow += m * m
            diff_pow += d * d
        n_total += len(left)
    if n_total == 0:
        raise RuntimeError("no audio samples decoded")
    level = _db(sum_pow / n_total)
    diff = _db(diff_pow / n_total) - level if level > -200 else 0.0
    return AudioStats(level_db=round(level, 1), diff_db=round(diff, 1), windows=len(starts))


def evaluate(stats: AudioStats, channels: int | None) -> tuple[str, str | None]:
    """(status, note): status is ``ok`` or ``warn``; note explains a warning."""
    if stats.level_db <= SILENT_DB:
        return "warn", "No audible sound in the parts checked. The audio track may be empty."
    if channels == 2 and stats.dual_mono and stats.level_db <= NO_DIALOGUE_DB:
        return "warn", (
            f"Probably missing dialogue: both stereo channels carry the same signal and it is "
            f"unusually quiet ({stats.level_db:.0f} dB). This is what a 5.1 source looks like "
            "when the centre (dialogue) channel was dropped during conversion. A better copy "
            "of the file is the only fix."
        )
    if stats.level_db <= QUIET_DB:
        return "warn", (
            f"Audio is very quiet ({stats.level_db:.0f} dB on average). Dialogue may be "
            "missing or the track may have been converted badly."
        )
    return "ok", None


def default_audio_index(item: MediaItem) -> tuple[int | None, int | None]:
    """(stream index, channels) of the track a player would pick."""
    tracks = [s for s in item.streams if s.type == "audio"]
    if not tracks:
        return None, None
    t = next((s for s in tracks if s.is_default), tracks[0])
    return t.index, t.channels


class AudioChecker:
    def __init__(self, db: Database, ffmpeg: Path) -> None:
        self.db = db
        self.ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self.running = False
        self.on_change: list[Callable[[], None]] = []

    def _todo(self, force: bool, item_ids: list[int] | None) -> list[int]:
        q = (select(MediaItem.id).join(LibraryRootRow)
             .where(MediaItem.kind.in_(KINDS), MediaItem.probe_ok, MediaItem.available,
                    LibraryRootRow.configured, MediaItem.acodec.is_not(None)))  # fmt: skip
        if item_ids:
            q = q.where(MediaItem.id.in_(item_ids))
        elif not force:
            # Unchecked, or the file changed since the last check; never re-flag
            # something the user dismissed unless the file itself changed.
            q = q.where(or_(MediaItem.audio_check.is_(None),
                            MediaItem.audio_checked_mtime != MediaItem.mtime_ns))  # fmt: skip
        with self.db.session() as s:
            return list(s.scalars(q))

    def run(self, *, force: bool = False, item_ids: list[int] | None = None) -> dict[str, int]:
        """Check pending items. Returns counts by result. One at a time per process."""
        if not self._lock.acquire(blocking=False):
            return {}
        self.running = True
        counts: dict[str, int] = {}
        try:
            for item_id in self._todo(force, item_ids):
                result = self.check_one(item_id)
                counts[result] = counts.get(result, 0) + 1
            if counts.get("warn"):
                for cb in self.on_change:
                    cb()
            if counts:
                log.info("audio check finished", extra=counts)
            return counts
        finally:
            self.running = False
            self._lock.release()

    def check_one(self, item_id: int) -> str:
        with self.db.session() as s:
            item = s.get(MediaItem, item_id)
            src = long_path(resolve_in_root(Path(item.root.path), item.rel_path).absolute())
            index, channels = default_audio_index(item)
            duration, mtime = item.duration, item.mtime_ns
        try:
            stats = measure(self.ffmpeg, src, duration, index)
            status, note = evaluate(stats, channels)
        except Exception as e:  # one file never stops the run
            log.info("audio check failed: %s", e, extra={"item": item_id})
            stats, status, note = None, "error", f"Couldn't analyse the audio: {e}"
        with self.db.session() as s:
            item = s.get(MediaItem, item_id)
            item.audio_check = status
            item.audio_note = note
            item.audio_level_db = stats.level_db if stats else None
            item.audio_dual_mono = stats.dual_mono if stats else None
            item.audio_checked_mtime = mtime
            item.audio_checked_at = utcnow()
        if status == "warn":
            log.warning("audio check: %s", note, extra={"item": item_id})
        return status

    def dismiss(self, item_id: int) -> None:
        """The user says this file is fine: stop listing it until the file changes."""
        with self.db.session() as s:
            item = s.get(MediaItem, item_id)
            if item is None:
                raise LookupError("no such item")
            item.audio_check = "dismissed"
