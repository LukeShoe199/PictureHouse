"""Playback decisions: can a client play the file as-is, or does FFmpeg step in?

Prefer, in order: direct play (original bytes), remux (copy the streams into a
container the client accepts), then transcode. Device-specific profiles arrive
in Phase 4; for now there is one browser profile and one generic DLNA TV
profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from picturehouse.db.models import MediaItem, is_hdr

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
EIGHT_BIT = {"yuv420p", "yuvj420p", None}

BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
BROWSER_DIRECT_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".m4a", ".mp3", ".flac", ".ogg", ".opus",
                       ".oga", ".wav"}  # fmt: skip

DLNA_VIDEO = {"h264", "hevc", "mpeg2video", "mpeg4", "vc1", "msmpeg4v3", "mpeg1video"}
DLNA_AUDIO = {"aac", "ac3", "eac3", "mp3", "mp2", "pcm_s16le", "pcm_s16be", "lpcm"}
DLNA_CONTAINER_EXTS = {".mp4", ".m4v", ".mkv", ".ts", ".m2ts", ".mts", ".avi", ".mpg", ".mpeg",
                       ".mov", ".vob"}  # fmt: skip
DLNA_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".wav"}
DLNA_AUDIO_CODECS = {"mp3", "flac", "aac", "alac", "pcm_s16le", "pcm_s16be", "pcm_s24le"}

MIME_BY_EXT = {
    ".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".ts": "video/mp2t",
    ".m2ts": "video/mp2t", ".mts": "video/mp2t", ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    ".vob": "video/mpeg", ".webm": "video/webm", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
    ".3gp": "video/3gpp", ".ogv": "video/ogg",
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".oga": "audio/ogg", ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma", ".aiff": "audio/aiff", ".aif": "audio/aiff", ".mka": "audio/x-matroska",
}  # fmt: skip


@dataclass(frozen=True)
class Decision:
    mode: str  # direct | remux | transcode
    reason: str
    copy_video: bool = False
    copy_audio: bool = False
    hdr: bool = False  # source is HDR: re-encodes must tone-map
    max_bitrate: int | None = None  # bits/s the re-encoded video must stay under


def ext_of(item: MediaItem) -> str:
    return PurePosixPath(item.rel_path).suffix.lower()


def mime_of(item: MediaItem) -> str:
    return MIME_BY_EXT.get(ext_of(item), "application/octet-stream")


def _video_ok_browser(item: MediaItem) -> bool:
    if item.vcodec not in BROWSER_VIDEO:
        return False
    # Browsers decode 8-bit H.264 only; 10-bit (Hi10P) fails silently.
    return not (item.vcodec == "h264" and item.pix_fmt not in EIGHT_BIT)


def decide_browser(item: MediaItem) -> Decision:
    if item.media_type not in ("video", "audio"):
        return Decision("none", "the file could not be read")
    ext = ext_of(item)
    audio_ok = item.acodec is None or item.acodec in BROWSER_AUDIO
    if item.media_type == "audio":
        if audio_ok and ext in BROWSER_DIRECT_EXTS:
            return Decision("direct", "original file")
        return Decision("transcode", f"{item.acodec} audio converted to AAC")
    video_ok = _video_ok_browser(item)
    if is_hdr(item):
        # Browsers on SDR screens show PQ/HLG washed out; tone-map to SDR.
        return Decision("transcode", f"HDR {item.vcodec} tone-mapped to SDR H.264",
                        copy_audio=audio_ok, hdr=True)  # fmt: skip
    if video_ok and audio_ok and ext in BROWSER_DIRECT_EXTS:
        return Decision("direct", "original file, no conversion")
    if video_ok:
        if audio_ok:
            return Decision("remux", f"{ext[1:].upper()} repackaged as MP4, streams untouched",
                            copy_video=True, copy_audio=True)  # fmt: skip
        return Decision("remux", f"video untouched, {item.acodec} audio converted to AAC",
                        copy_video=True)  # fmt: skip
    why = f"{item.vcodec}" + (" 10-bit" if item.pix_fmt and "10" in item.pix_fmt else "")
    return Decision("transcode", f"{why} video converted to H.264", copy_audio=audio_ok)


def decide_dlna(item: MediaItem, max_bitrate: int = 0) -> Decision:
    """Generic TV profile: most DLNA TVs play these containers and codecs.

    ``max_bitrate`` (bits/s, 0 = off): videos above it are re-encoded to fit,
    for TVs whose Wi-Fi can't sustain the original."""
    d = _decide_dlna(item)
    if (max_bitrate and item.media_type == "video" and item.bitrate
            and item.bitrate > max_bitrate * 1.05):  # fmt: skip
        why = f"{item.bitrate / 1e6:.0f} Mbps is over the {max_bitrate / 1e6:g} Mbps TV limit"
        return Decision("transcode", f"{why}; converted to fit", copy_video=False,
                        hdr=is_hdr(item), max_bitrate=max_bitrate)  # fmt: skip
    return d


def _decide_dlna(item: MediaItem) -> Decision:
    if item.media_type not in ("video", "audio"):
        return Decision("none", "the file could not be read")
    ext = ext_of(item)
    if item.media_type == "audio":
        # Extension alone isn't enough: a .wav can hold 32-bit float PCM, which
        # TVs reject (seen with an audiobook exported from an editor).
        if ext in DLNA_AUDIO_EXTS and item.acodec in DLNA_AUDIO_CODECS:
            return Decision("direct", "original file")
        return Decision("transcode", f"{item.acodec} audio converted to MP3")
    video_ok = item.vcodec in DLNA_VIDEO
    audio_ok = item.acodec is None or item.acodec in DLNA_AUDIO
    if ext in DLNA_CONTAINER_EXTS and video_ok and audio_ok:
        return Decision("direct", "original file, no conversion")
    h264_8bit = item.vcodec == "h264" and item.pix_fmt in EIGHT_BIT
    if not video_ok or ext not in DLNA_CONTAINER_EXTS:
        reason = f"{ext[1:]} / {item.vcodec} / {item.acodec} sent as MPEG-TS H.264/AAC"
    else:
        reason = f"{item.acodec} audio converted to AAC"
    # TVs play HDR fine when sent direct; a transcode for them still tone-maps
    # (our H.264 output is SDR).
    return Decision("transcode", reason, copy_video=h264_8bit and not is_hdr(item),
                    copy_audio=False, hdr=is_hdr(item))  # fmt: skip
