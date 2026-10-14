"""Supervised FFmpeg processes.

Every FFmpeg process that feeds a client:

* is registered so shutdown can kill it;
* is tied to our own lifetime by the OS (a kill-on-close Job Object on
  Windows, PR_SET_PDEATHSIG on Linux) so a crash can't orphan it;
* has its stderr drained into the log (last lines kept for error reports);
* is killed when the client disconnects or output stalls.
"""

from __future__ import annotations

import collections
import contextlib
import functools
import logging
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import anyio

from picturehouse.ffmpeg import no_window_flags

log = logging.getLogger(__name__)

CHUNK = 64 * 1024
STALL_TIMEOUT = 60.0  # seconds without output before we give up on a process

_active: set[subprocess.Popen] = set()
_active_lock = threading.Lock()


# --------------------------------------------------------------------------
# Tie child lifetime to ours
# --------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]  # fmt: skip

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9
    _job_handle = None
    _job_lock = threading.Lock()

    def _job():
        global _job_handle
        with _job_lock:
            if _job_handle is None:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.CreateJobObjectW.restype = wintypes.HANDLE
                h = k32.CreateJobObjectW(None, None)
                if not h:
                    return None
                info = _ExtendedLimit()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                ok = k32.SetInformationJobObject(
                    wintypes.HANDLE(h), _JobObjectExtendedLimitInformation,
                    ctypes.byref(info), ctypes.sizeof(info),
                )  # fmt: skip
                if not ok:
                    return None
                _job_handle = h  # deliberately never closed: closing kills the children
            return _job_handle

    def _bind_lifetime(proc: subprocess.Popen) -> None:
        job = _job()
        if job is None:
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(proc._handle)):
            log.debug("could not assign ffmpeg to job object", extra={"pid": proc.pid})

    def tie_children_to_this_process() -> bool:
        """Put this process in the kill-on-close job. Every child it starts from
        now on (ffprobe, artwork, audio checks, transcodes) inherits the job, so
        Windows ends them the moment this process exits, however it exits."""
        job = _job()
        if job is None:
            return False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        ok = bool(k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()))
        if not ok:
            log.debug("could not put the server in its own job object")
        return ok

    _preexec = None

else:

    def _bind_lifetime(proc: subprocess.Popen) -> None:
        pass

    def tie_children_to_this_process() -> bool:
        return False  # Linux: long-running children use PR_SET_PDEATHSIG instead

    def _preexec() -> None:  # runs in the child before exec
        try:
            import ctypes
            import signal

            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
        except Exception:
            pass


# PR_SET_PDEATHSIG fires when the *thread* that started the child exits, not
# the process. HLS starts FFmpeg from anyio worker threads, which exit after
# ~10 s idle: FFmpeg would be killed mid-film. So every child is started from
# this one thread, which lives as long as the server.
_spawner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ffmpeg-spawn")


def popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen for children that must not outlive the server."""
    if sys.platform == "win32" or _preexec is None:
        return subprocess.Popen(cmd, **kwargs)
    kwargs["preexec_fn"] = _preexec
    return _spawner.submit(subprocess.Popen, cmd, **kwargs).result()


def exit_with_parent(parent: int | None = None, poll: float = 1.0) -> threading.Thread | None:
    """Linux/macOS: stop this server (cleanly, SIGTERM) when the process that
    started it goes away, e.g. the desktop app being killed. A thread-scoped
    PR_SET_PDEATHSIG can't do this: the app starts us from a short-lived thread.

    ``parent`` is the app's pid as it told us (PICTUREHOUSE_PARENT_PID), so an
    app that died while we were still starting up is noticed too."""
    if sys.platform == "win32":
        return None  # the desktop app's Job Object covers Windows
    import os
    import signal

    if parent is None:
        parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(poll)
        log.warning("the app that started this server has gone; stopping")
        os.kill(os.getpid(), signal.SIGTERM)

    t = threading.Thread(target=watch, name="exit-with-parent", daemon=True)
    t.start()
    return t


# --------------------------------------------------------------------------


class FFmpegProcess:
    def __init__(self, cmd: list[str], *, label: str) -> None:
        self.label = label
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self.proc = popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=no_window_flags(),
        )
        _bind_lifetime(self.proc)
        with _active_lock:
            _active.add(self.proc)
        threading.Thread(target=self._drain, name=f"ffmpeg-stderr-{self.proc.pid}",
                         daemon=True).start()  # fmt: skip
        log.info("ffmpeg started", extra={"pid": self.proc.pid, "label": label})

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr_tail.append(line)
                log.debug("ffmpeg: %s", line, extra={"pid": self.proc.pid})

    def read(self, n: int = CHUNK) -> bytes:
        assert self.proc.stdout is not None
        return self.proc.stdout.read(n)

    def kill(self) -> None:
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                self.proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=5)
        with _active_lock:
            _active.discard(self.proc)
        rc = self.proc.returncode
        extra = {"pid": self.proc.pid, "label": self.label, "returncode": rc}
        if rc not in (0, None, -9, 1) and self.stderr_tail:
            extra["stderr"] = " | ".join(self.stderr_tail)
        log.info("ffmpeg stopped", extra=extra)


async def stream_ffmpeg(cmd: list[str], *, label: str) -> AsyncIterator[bytes]:
    """Run FFmpeg and yield its stdout. The process is killed when the
    consumer stops iterating (client disconnect), on stall, or at the end."""
    ff = FFmpegProcess(cmd, label=label)
    try:
        while True:
            with anyio.fail_after(STALL_TIMEOUT):
                data = await anyio.to_thread.run_sync(ff.read, CHUNK, abandon_on_cancel=True)
            if not data:
                if ff.proc.wait() not in (0, None) and ff.stderr_tail:
                    log.warning("ffmpeg failed: %s", ff.stderr_tail[-1],
                                extra={"label": label})  # fmt: skip
                break
            yield data
    except TimeoutError:
        log.warning("ffmpeg produced no output for %ss; stopped", STALL_TIMEOUT,
                    extra={"label": label})  # fmt: skip
    finally:
        ff.kill()


def active_count() -> int:
    with _active_lock:
        return sum(1 for p in _active if p.poll() is None)


def kill_all() -> None:
    with _active_lock:
        procs = list(_active)
    for p in procs:
        with contextlib.suppress(OSError):
            p.kill()
    for p in procs:
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            p.wait(timeout=5)
    with _active_lock:
        _active.clear()


# --------------------------------------------------------------------------
# Command builders
# --------------------------------------------------------------------------

_SCALE_1080 = "scale='min(1920,iw)':-2"
# HDR (PQ/HLG) -> SDR BT.709: linearise, convert primaries, Hable tone-map.
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")  # fmt: skip


@functools.lru_cache(maxsize=4)
def has_filter(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                             timeout=15, creationflags=no_window_flags()).stdout  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == [name] for line in out.decode("utf-8", "replace").splitlines())


def video_filter(ffmpeg: Path, hdr: bool) -> str:
    """Scale to at most 1080p; tone-map HDR to SDR when FFmpeg can (zscale)."""
    if hdr and has_filter(str(ffmpeg), "zscale") and has_filter(str(ffmpeg), "tonemap"):
        return f"{_SCALE_1080},{_TONEMAP}"
    return _SCALE_1080


def x264_args(ffmpeg: Path, hdr: bool = False, max_bitrate: int | None = None) -> list[str]:
    """``max_bitrate``: total bits/s the stream must fit in (audio included)."""
    args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
            "-vf", video_filter(ffmpeg, hdr), "-g", "48",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]  # fmt: skip
    if max_bitrate:
        # Capped CRF: quality-driven, but never above the cap (minus audio).
        video = max(1_000_000, int(max_bitrate * 0.9) - 192_000)
        args += ["-maxrate", str(video), "-bufsize", str(video * 2)]
    return args


def _input(ffmpeg: Path, src: Path, start: float) -> list[str]:
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    return [*cmd, "-i", str(src)]


def fmp4_cmd(
    ffmpeg: Path, src: Path, *, start: float, audio_only: bool,
    copy_video: bool, copy_audio: bool, audio_index: int | None = None, hdr: bool = False,
) -> list[str]:  # fmt: skip
    """Fragmented MP4 to stdout, playable progressively by browsers."""
    cmd = _input(ffmpeg, src, start)
    a_map = f"0:{audio_index}" if audio_index is not None else "0:a:0?"
    if audio_only:
        cmd += ["-vn", "-map", a_map]
    else:
        cmd += ["-map", "0:V:0", "-map", a_map]  # V = real video, not cover art
        cmd += ["-c:v", "copy"] if copy_video else x264_args(ffmpeg, hdr)
    cmd += ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-sn", "-dn", "-map_metadata", "-1",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof"]  # fmt: skip
    if audio_only:
        # frag_keyframe cuts on *video* keyframes: with no video the whole
        # stream would arrive as one fragment at the end. Cut every second.
        cmd += ["-frag_duration", "1000000"]
    cmd += ["-f", "mp4", "pipe:1"]
    return cmd


def mpegts_cmd(
    ffmpeg: Path, src: Path, *, start: float, copy_video: bool, hdr: bool = False,
    max_bitrate: int | None = None,
) -> list[str]:  # fmt: skip
    """MPEG-TS H.264/AAC to stdout: the format nearly every DLNA TV streams."""
    cmd = _input(ffmpeg, src, start)
    cmd += ["-map", "0:V:0", "-map", "0:a:0?"]
    cmd += (["-c:v", "copy", "-bsf:v", "h264_mp4toannexb"] if copy_video
            else x264_args(ffmpeg, hdr, max_bitrate))  # fmt: skip
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-sn", "-dn", "-f", "mpegts", "pipe:1"]
    return cmd


def mp3_cmd(ffmpeg: Path, src: Path, *, start: float) -> list[str]:
    cmd = _input(ffmpeg, src, start)
    return [*cmd, "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "256k", "-f", "mp3",
            "pipe:1"]  # fmt: skip


# --------------------------------------------------------------------------
# Transcode slots: a cap on simultaneous video encodes
# --------------------------------------------------------------------------

QUEUE_WAIT = 8.0  # seconds a new stream waits for a slot before "busy"
UNCLAIMED_LEASE = 30.0  # a slot nobody started streaming from is reclaimed


class TranscodeBusy(Exception):
    """Every transcode slot is taken. ``holders`` describes who has them."""

    def __init__(self, limit: int, holders: list[str]) -> None:
        self.limit, self.holders = limit, holders
        who = f" ({', '.join(holders)})" if holders else ""
        super().__init__(
            f"PictureHouse is already converting {limit} video{'s' if limit != 1 else ''} "
            f"for other viewers{who}. Try again when one finishes, or play a file that "
            "doesn't need converting."
        )


@dataclass
class _Slot:
    label: str
    holds: int = 0
    since: float = 0.0
    started: bool = False  # someone actually began streaming


class TranscodeSlots:
    """At most ``limit`` video encodes at once (0 = unlimited).

    Slots are keyed by *who* is watching (a player/session id, or client
    address + item), so one viewer seeking or reopening never queues behind
    themselves: the same key shares its slot. A new viewer waits up to
    ``QUEUE_WAIT`` seconds for a slot (covers the moment a previous stream is
    still closing), then gets :class:`TranscodeBusy`.
    """

    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self._slots: dict[str, _Slot] = {}
        self._cond = threading.Condition()

    def _reclaim(self) -> None:
        now = time.monotonic()
        for key, s in list(self._slots.items()):
            if not s.started and now - s.since > UNCLAIMED_LEASE:
                del self._slots[key]  # handed out, but the stream never began

    def acquire(self, key: str, label: str, wait: float | None = None) -> None:
        deadline = time.monotonic() + (QUEUE_WAIT if wait is None else wait)
        with self._cond:
            while True:
                self._reclaim()
                slot = self._slots.get(key)
                if slot is None and (self.limit <= 0 or len(self._slots) < self.limit):
                    slot = self._slots[key] = _Slot(label, since=time.monotonic())
                if slot is not None:
                    slot.holds += 1
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    holders = [s.label for s in self._slots.values()]
                    log.warning("transcode refused: all slots busy",
                                extra={"label": label, "holders": holders})  # fmt: skip
                    raise TranscodeBusy(self.limit, holders)
                self._cond.wait(min(remaining, 1.0))

    def started(self, key: str) -> None:
        with self._cond:
            if key in self._slots:
                self._slots[key].started = True

    def release(self, key: str) -> None:
        with self._cond:
            slot = self._slots.get(key)
            if slot is None:
                return
            slot.holds -= 1
            if slot.holds <= 0:
                del self._slots[key]
                self._cond.notify_all()

    def holders(self) -> list[str]:
        with self._cond:
            return [s.label for s in self._slots.values()]


# One limiter for the whole server: browser streams, HLS and DLNA share it.
slots = TranscodeSlots()


class Lease:
    """A held slot, released exactly once."""

    def __init__(self, key: str, label: str, wait: float | None = None) -> None:
        slots.acquire(key, label, wait)
        self.key = key
        self._done = False

    def started(self) -> None:
        slots.started(self.key)

    def release(self) -> None:
        if not self._done:
            self._done = True
            slots.release(self.key)


async def stream_with_lease(
    cmd: list[str], *, label: str, lease: Lease | None
) -> AsyncIterator[bytes]:
    """:func:`stream_ffmpeg` that gives its transcode slot back when done."""
    try:
        if lease:
            lease.started()
        async for chunk in stream_ffmpeg(cmd, label=label):
            yield chunk
    finally:
        if lease:
            lease.release()
