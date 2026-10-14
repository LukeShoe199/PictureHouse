"""Run ffprobe on one file and turn its JSON into a typed result.

Failures raise ``ProbeError`` with a short human-readable reason; the scanner
records it against the file and moves on.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from picturehouse.ffmpeg import no_window_flags
from picturehouse.paths import long_path

AUDIO_ONLY_CONTAINERS = {"mp3", "flac", "ogg", "wav", "aiff", "ape", "wv"}


class ProbeError(Exception):
    pass


@dataclass
class StreamInfo:
    index: int
    type: str
    codec: str | None
    profile: str | None = None
    language: str | None = None
    title: str | None = None
    channels: int | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    fps: float | None = None
    color_transfer: str | None = None
    is_default: bool = False
    is_forced: bool = False
    attached_pic: bool = False


@dataclass
class ProbeResult:
    container: str | None
    duration: float | None
    bitrate: int | None
    tags: dict[str, str]
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def video(self) -> StreamInfo | None:
        return next((s for s in self.streams if s.type == "video" and not s.attached_pic), None)

    @property
    def audio(self) -> StreamInfo | None:
        audios = [s for s in self.streams if s.type == "audio"]
        return next((s for s in audios if s.is_default), audios[0] if audios else None)

    @property
    def has_attached_pic(self) -> bool:
        return any(s.attached_pic for s in self.streams)

    @property
    def media_type(self) -> str:
        if self.video:
            return "video"
        return "audio" if self.audio else "unknown"

    def tag(self, *names: str) -> str | None:
        for n in names:
            v = self.tags.get(n)
            if v:
                return v.strip()
        return None

    def creation_time(self) -> datetime | None:
        raw = self.tag("creation_time", "com.apple.quicktime.creationdate")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None


def _num(v: Any, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _fps(rate: str | None) -> float | None:
    if not rate or "/" not in rate:
        return None
    n, d = rate.split("/", 1)
    try:
        return round(int(n) / int(d), 3) if int(d) else None
    except ValueError:
        return None


def parse_probe_json(data: dict[str, Any]) -> ProbeResult:
    fmt = data.get("format") or {}
    raw_streams = data.get("streams") or []
    if not raw_streams:
        raise ProbeError("no audio or video streams")
    streams = []
    for s in raw_streams:
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        disp = s.get("disposition") or {}
        streams.append(
            StreamInfo(
                index=int(s.get("index", len(streams))),
                type=s.get("codec_type") or "data",
                codec=s.get("codec_name"),
                profile=s.get("profile"),
                language=tags.get("language"),
                title=tags.get("title"),
                channels=_num(s.get("channels"), int),
                width=_num(s.get("width"), int),
                height=_num(s.get("height"), int),
                pix_fmt=s.get("pix_fmt"),
                color_transfer=s.get("color_transfer"),
                fps=_fps(s.get("avg_frame_rate")) or _fps(s.get("r_frame_rate")),
                is_default=bool(disp.get("default")),
                is_forced=bool(disp.get("forced")),
                attached_pic=bool(disp.get("attached_pic")),
            )
        )
    # Prefer a format tag, but fall back to stream-level tags (Vorbis comments
    # in Ogg/Opus live on the stream).
    tags: dict[str, str] = {}
    for s in raw_streams:
        for k, v in (s.get("tags") or {}).items():
            tags.setdefault(k.lower(), str(v))
    tags.update({k.lower(): str(v) for k, v in (fmt.get("tags") or {}).items()})
    for k in ("language", "handler_name", "vendor_id", "encoder", "duration"):
        if k in tags and k not in (fmt.get("tags") or {}):
            tags.pop(k)

    duration = _num(fmt.get("duration"))
    if duration is None:
        durations = [_num(s.get("duration")) for s in raw_streams]
        duration = max((d for d in durations if d), default=None)

    result = ProbeResult(
        container=fmt.get("format_name"),
        duration=duration,
        bitrate=_num(fmt.get("bit_rate"), int),
        tags=tags,
        streams=streams,
    )
    if result.media_type == "unknown":
        raise ProbeError("no audio or video streams")
    return result


def probe_file(ffprobe: Path, path: Path, *, timeout: float = 30.0) -> ProbeResult:
    cmd = [
        str(ffprobe),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(long_path(path.absolute())),
    ]  # fmt: skip
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=no_window_flags()
        )
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe timed out after {timeout:g}s") from e
    except OSError as e:
        raise ProbeError(f"could not run ffprobe: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        # The last line is usually the useful one ("Invalid data found ...").
        reason = err[-1] if err else f"ffprobe exited with code {proc.returncode}"
        # Strip the file path ffprobe prefixes, it's already known.
        if ": " in reason and str(path.name) in reason:
            reason = reason.rsplit(": ", 1)[-1]
        raise ProbeError(reason)
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError as e:
        raise ProbeError("ffprobe returned unreadable output") from e
    return parse_probe_json(data)
