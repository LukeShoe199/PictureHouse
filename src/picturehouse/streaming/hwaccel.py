"""Hardware video conversion: Intel Quick Sync through VAAPI (Linux).

A small server (a NAS, a mini PC running ZimaOS) can't convert HD video in
software in real time, but its Intel graphics can. At start-up
:func:`detect` asks FFmpeg to encode a second of test video on the GPU
(normal mode, then low-power mode, which some chips such as Jasper Lake
need); if either works, conversions use it, otherwise everything stays in
software exactly as before.

Two hardware pipelines:

* **full**: the GPU decodes, scales and encodes (codecs the chip decodes:
  H.264 8-bit, HEVC 8/10-bit, VP9, MPEG-2);
* **upload**: FFmpeg decodes on the processor, the GPU scales/encodes
  (anything else).

HDR sources stay in software: they need tone-mapping (procs._TONEMAP).
A conversion that fails on the GPU falls back to software (streaming/hls.py).

``PICTUREHOUSE_HWACCEL=off`` turns it off. In Docker the container needs the
GPU: ``devices: [/dev/dri:/dev/dri]`` (zimaos/docker-compose.yml).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from picturehouse.ffmpeg import no_window_flags

log = logging.getLogger(__name__)

RENDER_NODE = Path("/dev/dri/renderD128")
# What Intel graphics since ~2015 decode (Jasper Lake/N5105 included).
HW_DECODE = {"h264", "hevc", "vp9", "mpeg2video"}
EIGHT_BIT = {"yuv420p", "yuvj420p", "nv12", None}
DETECT_TIMEOUT = 30


@dataclass(frozen=True)
class HwVideo:
    device: str
    low_power: bool  # VDENC: the only H.264 encoder on some chips

    def full(self, vcodec: str | None, pix_fmt: str | None) -> bool:
        """Can the GPU also decode this source?"""
        if vcodec not in HW_DECODE:
            return False
        return not (vcodec == "h264" and pix_fmt not in EIGHT_BIT)  # no Hi10P decoding

    def input_args(self, vcodec: str | None, pix_fmt: str | None) -> list[str]:
        """Goes before ``-i``."""
        if self.full(vcodec, pix_fmt):
            return ["-hwaccel", "vaapi", "-hwaccel_device", self.device,
                    "-hwaccel_output_format", "vaapi"]  # fmt: skip
        return ["-init_hw_device", f"vaapi=va:{self.device}", "-filter_hw_device", "va"]

    def video_args(self, vcodec: str | None, pix_fmt: str | None,
                   max_bitrate: int | None = None) -> list[str]:  # fmt: skip
        """Scale to at most 1080p and encode H.264 on the GPU. ``max_bitrate``:
        total bits/s the stream must fit in (audio included)."""
        if self.full(vcodec, pix_fmt):
            vf = "scale_vaapi=w='min(1920,iw)':h=-2:format=nv12"
        else:
            vf = "scale='min(1920,iw)':-2,format=nv12,hwupload"
        # Hardware encoders have no CRF: a generous average with a ceiling.
        peak = max(1_000_000, int(max_bitrate * 0.9) - 192_000) if max_bitrate else 10_000_000
        avg = min(7_000_000, int(peak * 0.75))
        return ["-vf", vf, "-c:v", "h264_vaapi", "-profile:v", "high",
                *(["-low_power", "1"] if self.low_power else []),
                "-b:v", str(avg), "-maxrate", str(peak), "-bufsize", str(peak * 2), "-g", "48",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]  # fmt: skip

    def describe(self) -> dict:
        return {"kind": "vaapi", "device": self.device, "encoder": "h264_vaapi",
                "low_power": self.low_power}  # fmt: skip


ACTIVE: HwVideo | None = None  # set by detect()
LAST_ERROR: str | None = None  # why there is none, for the status page


def detect(ffmpeg: Path | str, device: Path = RENDER_NODE) -> HwVideo | None:
    """Find a working GPU encoder (call once, at start-up; takes a second or two)."""
    global ACTIVE, LAST_ERROR
    ACTIVE = None
    if os.environ.get("PICTUREHOUSE_HWACCEL", "auto").strip().lower() in (
        "off",
        "0",
        "no",
        "false",
    ):
        LAST_ERROR = "turned off (PICTUREHOUSE_HWACCEL=off)"
        return None
    if sys.platform != "linux" or not device.exists():
        LAST_ERROR = f"no GPU device ({device})" if sys.platform == "linux" else "Linux only"
        return None
    for low_power in (False, True):
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
               "-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va",
               "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=1",
               "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi",
               *(["-low_power", "1"] if low_power else []), "-f", "null", "-"]  # fmt: skip
        try:
            run = subprocess.run(cmd, capture_output=True, timeout=DETECT_TIMEOUT,
                                 creationflags=no_window_flags())  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as e:
            LAST_ERROR = str(e)
            continue
        if run.returncode == 0:
            ACTIVE = HwVideo(str(device), low_power)
            LAST_ERROR = None
            log.info("hardware video conversion: Intel VAAPI%s", " (low power)" if low_power else "",
                     extra={"device": str(device)})  # fmt: skip
            return ACTIVE
        lines = run.stderr.decode("utf-8", "replace").strip().splitlines()
        LAST_ERROR = lines[-1] if lines else f"FFmpeg exit code {run.returncode}"
    log.info("no hardware video conversion, using the processor: %s", LAST_ERROR)
    return None


def for_video(vcodec: str | None, pix_fmt: str | None, hdr: bool) -> HwVideo | None:
    """The GPU pipeline for converting this source, or None (software)."""
    if ACTIVE is None or hdr or vcodec is None:
        return None
    return ACTIVE


def status() -> dict:
    return {"active": ACTIVE.describe() if ACTIVE else None, "why_not": LAST_ERROR}
