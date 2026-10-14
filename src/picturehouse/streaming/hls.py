"""HLS transcoding sessions (iPhone/iPad/Safari, Chromecast, seekable transcodes).

The playlist is written up front as a fixed grid of ``SEGMENT``-second
segments covering the whole file, so a player can seek anywhere at once.
FFmpeg produces segments on demand:

* a request for a segment that exists is served from disk;
* a request just ahead of a running job waits for it;
* anything else (a seek) restarts FFmpeg at that segment.

Keyframes are forced on the segment grid and output timestamps are offset to
the segment's start, so segments from different FFmpeg runs line up.

Throttling: a job more than ``AHEAD`` segments ahead of the last request is
stopped and restarted when the player catches up. Idle sessions are stopped
after ``IDLE_STOP`` seconds and deleted after ``IDLE_DELETE``.
"""

from __future__ import annotations

import logging
import math
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from picturehouse.streaming.procs import FFmpegProcess, Lease, video_filter

log = logging.getLogger(__name__)

SEGMENT = 6  # seconds
AHEAD = 20  # segments (2 minutes) the encoder may run ahead of the player
WAIT_AHEAD = 4  # a request this close to the job's progress waits instead of restarting
SEGMENT_TIMEOUT = 45.0  # seconds to wait for one segment before giving up
IDLE_STOP = 90.0
IDLE_DELETE = 20 * 60.0


class HlsError(Exception):
    pass


@dataclass
class Job:
    proc: FFmpegProcess
    start: int

    def alive(self) -> bool:
        return self.proc.proc.poll() is None


@dataclass
class HlsSession:
    id: str
    item_id: int
    src: Path
    duration: float
    dir: Path
    audio_index: int | None
    copy_video: bool = False
    hdr: bool = False
    client: str | None = None
    who: str = "a viewer"  # for "server busy" messages
    lease: Lease | None = None  # transcode slot, held while the session is in use
    last_access: float = field(default_factory=time.monotonic)
    last_requested: int = 0
    job: Job | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def segments(self) -> int:
        return max(1, math.ceil(self.duration / SEGMENT))

    def seg_path(self, k: int) -> Path:
        return self.dir / f"seg{k:05d}.ts"

    def produced_up_to(self, start: int) -> int:
        """Highest consecutive finished segment from ``start`` (start-1 if none)."""
        k = start
        while self.seg_path(k).exists():
            k += 1
        return k - 1


def playlist(session: HlsSession, segment_url: str) -> str:
    """VOD playlist for the whole file. ``segment_url`` has a ``{k}`` placeholder."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{SEGMENT}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD",
             "#EXT-X-INDEPENDENT-SEGMENTS"]  # fmt: skip
    remaining = session.duration
    for k in range(session.segments):
        dur = min(SEGMENT, remaining) if k == session.segments - 1 else SEGMENT
        remaining -= SEGMENT
        lines += [f"#EXTINF:{max(dur, 0.1):.3f},", segment_url.format(k=k)]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def hls_cmd(ffmpeg: Path, session: HlsSession, start: int) -> list[str]:
    t = start * SEGMENT
    a_map = f"0:{session.audio_index}" if session.audio_index is not None else "0:a:0?"
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if t:
        cmd += ["-ss", str(t)]
    cmd += ["-i", str(session.src), "-map", "0:V:0", "-map", a_map, "-sn", "-dn"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level:v", "4.1", "-vf", video_filter(ffmpeg, session.hdr),
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        # Keyframe exactly on the segment grid, so every run cuts identical segments.
        "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT})", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-output_ts_offset", str(t),
        "-f", "hls", "-hls_time", str(SEGMENT), "-hls_list_size", "0",
        "-hls_segment_type", "mpegts", "-hls_flags", "temp_file",
        "-start_number", str(start),
        "-hls_segment_filename", str(session.dir / "seg%05d.ts"),
        str(session.dir / "ffmpeg.m3u8"),
    ]  # fmt: skip
    return cmd


class HlsManager:
    def __init__(self, ffmpeg: Path, root: Path) -> None:
        self.ffmpeg = ffmpeg
        self.root = root
        self._sessions: dict[str, HlsSession] = {}
        self._by_key: dict[tuple[int, int | None, str | None], str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # Leftovers from a previous run (crash) are useless: start clean.
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self._reaper = threading.Thread(target=self._reap, name="hls-reaper", daemon=True)
        self._reaper.start()

    # ---------------------------------------------------------------- sessions

    def open(
        self, item_id: int, src: Path, duration: float, audio_index: int | None = None,
        hdr: bool = False, client: str | None = None, who: str = "a viewer",
    ) -> HlsSession:  # fmt: skip
        """Get (or create) the session for this item + audio track.

        Raises :class:`~picturehouse.streaming.procs.TranscodeBusy` when every
        transcode slot is taken, so the player hears "busy" before it starts."""
        if not duration or duration <= 0:
            raise HlsError("file has no known duration; HLS needs one")
        key = (item_id, audio_index, client)
        with self._lock:
            sid = self._by_key.get(key)
            existing = self._sessions.get(sid) if sid else None
        if existing:
            existing.last_access = time.monotonic()
            with existing.lock:
                self._ensure_lease(existing)
            return existing
        sid = secrets.token_hex(8)
        lease = Lease(self._slot_key(client, sid), who)  # may raise TranscodeBusy
        lease.started()
        with self._lock:  # opened by a parallel request while we waited?
            other = self._sessions.get(self._by_key.get(key, ""))
        if other:
            lease.release()
            return other
        d = self.root / sid
        d.mkdir(parents=True)
        s = HlsSession(sid, item_id, src, duration, d, audio_index, hdr=hdr, client=client,
                       who=who, lease=lease)  # fmt: skip
        with self._lock:
            self._sessions[sid] = s
            self._by_key[key] = sid
        log.info("HLS session opened", extra={"session": sid, "item": item_id})
        return s

    @staticmethod
    def _slot_key(client: str | None, sid: str) -> str:
        # One player shares one slot across items and audio tracks, so moving
        # on to the next episode never waits for the last session to expire.
        return f"hls:{client}" if client else f"hls:{sid}"

    @classmethod
    def _ensure_lease(cls, s: HlsSession) -> None:
        """Re-take a slot given up while the session sat idle (call with s.lock)."""
        if s.lease is None:
            s.lease = Lease(cls._slot_key(s.client, s.id), s.who)
            s.lease.started()

    @staticmethod
    def _drop_lease(s: HlsSession) -> None:
        if s.lease is not None:
            s.lease.release()
            s.lease = None

    def get(self, sid: str) -> HlsSession | None:
        return self._sessions.get(sid)

    def close(self, sid: str) -> None:
        with self._lock:
            s = self._sessions.pop(sid, None)
            if s:
                self._by_key.pop((s.item_id, s.audio_index, s.client), None)
        if s:
            self._stop_job(s)
            self._drop_lease(s)
            shutil.rmtree(s.dir, ignore_errors=True)
            log.info("HLS session closed", extra={"session": sid})

    def shutdown(self) -> None:
        self._stop.set()
        for sid in list(self._sessions):
            self.close(sid)

    @property
    def active_jobs(self) -> int:
        return sum(1 for s in self._sessions.values() if s.job and s.job.alive())

    # ---------------------------------------------------------------- segments

    def segment(self, s: HlsSession, k: int) -> Path:
        """Block until segment ``k`` exists (starting/restarting FFmpeg as needed)."""
        if not 0 <= k < s.segments:
            raise HlsError(f"no segment {k}")
        path = s.seg_path(k)
        with s.lock:
            s.last_access = time.monotonic()
            s.last_requested = k
            if path.exists():
                return path
            job = s.job
            if (
                job is None
                or not job.alive()
                or not (job.start <= k <= s.produced_up_to(job.start) + WAIT_AHEAD)
            ):
                self._start_job(s, k)
        deadline = time.monotonic() + SEGMENT_TIMEOUT
        restarted = False
        while time.monotonic() < deadline:
            if path.exists():
                return path
            with s.lock:
                job = s.job
                if job is None or not job.alive():
                    # Stopped (throttled by another request's timing, or crashed)
                    # before reaching k. Restart once; a second failure is real.
                    if restarted:
                        err = (job.proc.stderr_tail[-1] if job and job.proc.stderr_tail
                               else "FFmpeg stopped")  # fmt: skip
                        raise HlsError(f"transcode failed: {err}")
                    if not path.exists():
                        self._start_job(s, k)
                        restarted = True
            time.sleep(0.1)
        raise HlsError(f"segment {k} not ready after {SEGMENT_TIMEOUT:.0f}s")

    def _start_job(self, s: HlsSession, k: int) -> None:
        self._ensure_lease(s)
        self._stop_job(s)
        # Drop a half-written segment from the job we just stopped.
        for tmp in s.dir.glob("*.tmp"):
            tmp.unlink(missing_ok=True)
        log.info("HLS encoding from segment %d", k, extra={"session": s.id, "item": s.item_id})
        s.job = Job(FFmpegProcess(hls_cmd(self.ffmpeg, s, k), label=f"hls:{s.item_id}:{k}"), k)

    @staticmethod
    def _stop_job(s: HlsSession) -> None:
        if s.job is not None:
            s.job.proc.kill()
            s.job = None

    # ---------------------------------------------------------------- reaper

    def _reap(self) -> None:
        while not self._stop.wait(2.0):
            now = time.monotonic()
            for sid, s in list(self._sessions.items()):
                idle = now - s.last_access
                if idle > IDLE_DELETE:
                    self.close(sid)
                    continue
                with s.lock:
                    job = s.job
                    if job is None:
                        if idle > IDLE_STOP:
                            self._drop_lease(s)  # paused/abandoned: free the slot
                        continue
                    if not job.alive():
                        self._stop_job(s)  # finished or crashed: reap it
                        continue
                    if idle > IDLE_STOP:
                        log.info("HLS session idle; encoder stopped", extra={"session": sid})
                        self._stop_job(s)
                        self._drop_lease(s)
                    elif s.produced_up_to(job.start) - s.last_requested > AHEAD:
                        log.debug("HLS encoder far enough ahead; pausing", extra={"session": sid})
                        self._stop_job(s)
