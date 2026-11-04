"""Hardware video conversion (streaming/hwaccel.py): the commands it builds,
when it's used, and that a GPU failure falls back to the processor."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from picturehouse.streaming import hls, hwaccel, procs
from picturehouse.streaming.hls import HlsManager, hls_cmd

GPU = hwaccel.HwVideo("/dev/dri/renderD128", low_power=False)


@pytest.fixture
def gpu(monkeypatch):
    monkeypatch.setattr(hwaccel, "ACTIVE", GPU)
    return GPU


def test_full_gpu_pipeline_for_codecs_the_chip_decodes(gpu):
    cmd = procs.fmp4_cmd(Path("ff"), Path("a.mkv"), start=0, audio_only=False, copy_video=False,
                         copy_audio=False, vcodec="hevc", pix_fmt="yuv420p10le")  # fmt: skip
    assert cmd.index("-hwaccel") < cmd.index("-i")  # decode on the GPU
    assert "-hwaccel_output_format" in cmd and "h264_vaapi" in cmd
    assert any(a.startswith("scale_vaapi=") and "format=nv12" in a for a in cmd)  # 10 -> 8 bit
    assert "libx264" not in cmd and "-crf" not in cmd


def test_upload_pipeline_for_everything_else(gpu):
    cmd = procs.mpegts_cmd(Path("ff"), Path("a.avi"), start=0, copy_video=False,
                           vcodec="mpeg4", pix_fmt="yuv420p", max_bitrate=8_000_000)  # fmt: skip
    assert "-hwaccel" not in cmd and "-init_hw_device" in cmd  # decoded by the processor
    assert any("hwupload" in a for a in cmd) and "h264_vaapi" in cmd
    assert int(cmd[cmd.index("-maxrate") + 1]) <= 8_000_000  # the TV's bitrate cap holds
    hi10p = procs.hwaccel.ACTIVE.full("h264", "yuv420p10le")
    assert hi10p is False  # 10-bit H.264: Intel can't decode it


def test_software_when_gpu_cant_or_needn_t(gpu):
    hdr = procs.fmp4_cmd(Path("ff"), Path("a.mkv"), start=0, audio_only=False, copy_video=False,
                         copy_audio=False, vcodec="hevc", pix_fmt="yuv420p10le", hdr=True)  # fmt: skip
    assert "libx264" in hdr and "h264_vaapi" not in hdr  # HDR needs tone-mapping
    copy = procs.fmp4_cmd(Path("ff"), Path("a.mkv"), start=0, audio_only=False, copy_video=True,
                          copy_audio=False, vcodec="hevc")  # fmt: skip
    assert "-hwaccel" not in copy and "-init_hw_device" not in copy  # nothing to encode


def test_no_gpu_means_exactly_the_old_commands(monkeypatch):
    monkeypatch.setattr(hwaccel, "ACTIVE", None)
    cmd = procs.fmp4_cmd(Path("ff"), Path("a.mkv"), start=0, audio_only=False, copy_video=False,
                         copy_audio=False, vcodec="hevc", pix_fmt="yuv420p")  # fmt: skip
    assert "libx264" in cmd and "-hwaccel" not in cmd and "-init_hw_device" not in cmd


def test_detect_without_a_gpu(monkeypatch, tmp_path):
    assert hwaccel.detect("ffmpeg", device=tmp_path / "renderD128") is None
    assert hwaccel.status()["active"] is None and hwaccel.status()["why_not"]
    monkeypatch.setenv("PICTUREHOUSE_HWACCEL", "off")
    assert hwaccel.detect("ffmpeg") is None and "turned off" in hwaccel.LAST_ERROR


def test_hls_session_uses_gpu_with_codec_info(gpu, tmp_path):
    s = hls.HlsSession("s", 1, Path("a.mkv"), 60.0, tmp_path, None, vcodec="hevc",
                       pix_fmt="yuv420p")  # fmt: skip
    assert "h264_vaapi" in hls_cmd(Path("ff"), s, 0)
    s.software = True
    assert "libx264" in hls_cmd(Path("ff"), s, 0)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_gpu_failure_falls_back_to_the_processor(monkeypatch, tmp_path):
    """A GPU that fails (here: a device that doesn't exist) mustn't cost the
    viewer the film: the session carries on in software."""
    monkeypatch.setattr(hwaccel, "ACTIVE", hwaccel.HwVideo(str(tmp_path / "no-gpu"), False))
    clip = tmp_path / "clip.mkv"
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=14",
                    "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
                    str(clip)], check=True)  # fmt: skip
    mgr = HlsManager(Path(shutil.which("ffmpeg")), tmp_path / "hls")
    try:
        s = mgr.open(1, clip, 14.0, vcodec="hevc", pix_fmt="yuv420p")
        seg = mgr.segment(s, 0)
        assert seg.is_file() and seg.stat().st_size > 0
        assert s.software is True
    finally:
        mgr.shutdown()
