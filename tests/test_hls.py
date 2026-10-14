"""HLS sessions against a real FFmpeg, using a generated 40-second clip."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from picturehouse.streaming import hls, procs
from picturehouse.streaming.hls import SEGMENT, HlsManager, playlist

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    """40 s HEVC + AC-3 in MKV: something browsers can't play directly."""
    out = tmp_path_factory.mktemp("hls") / "clip.mkv"
    subprocess.run(
        [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=40",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
         "-c:a", "ac3", str(out)],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture
def mgr(tmp_path):
    m = HlsManager(Path(shutil.which("ffmpeg")), tmp_path / "hls")
    yield m
    m.shutdown()
    assert procs.active_count() == 0


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams",
         "-show_format", str(path)], capture_output=True, check=True).stdout  # fmt: skip
    return json.loads(out)


def test_playlist_covers_whole_file(mgr, clip):
    s = mgr.open(1, clip, 40.0)
    text = playlist(s, "/seg/{k}.ts")
    assert text.count("#EXTINF") == 7  # 6+6+6+6+6+6+4
    assert "#EXTINF:4.000," in text and text.rstrip().endswith("#EXT-X-ENDLIST")
    assert mgr.open(1, clip, 40.0) is s  # reused


def test_segments_are_h264_aac_and_aligned(mgr, clip):
    s = mgr.open(1, clip, 40.0)
    first = mgr.segment(s, 0)
    info = _probe(first)
    assert {st["codec_name"] for st in info["streams"]} == {"h264", "aac"}
    assert abs(float(info["format"]["duration"]) - SEGMENT) < 0.5


def test_seek_restarts_with_continuous_timestamps(mgr, clip):
    s = mgr.open(1, clip, 40.0)
    mgr.segment(s, 0)
    seg5 = mgr.segment(s, 5)  # far ahead: a new job starting at segment 5
    start = float(_probe(seg5)["format"]["start_time"])
    # Timestamps continue the timeline (~30 s plus the MPEG-TS mux delay), so
    # players see no jump when segments from different runs are mixed.
    assert 29.5 < start < 32.0
    assert s.job is None or s.job.start == 5


def test_throttles_and_cleans_up(mgr, clip, monkeypatch):
    monkeypatch.setattr(hls, "AHEAD", 1)
    s = mgr.open(1, clip, 40.0)
    mgr.segment(s, 0)
    for _ in range(100):  # the reaper stops a job that runs >1 segment ahead
        if s.job is None:
            break
        time.sleep(0.1)
    assert s.job is None
    mgr.close(s.id)
    assert not s.dir.exists() and mgr.get(s.id) is None


def test_bad_segment_and_missing_duration(mgr, clip):
    s = mgr.open(1, clip, 40.0)
    with pytest.raises(hls.HlsError):
        mgr.segment(s, 99)
    with pytest.raises(hls.HlsError):
        mgr.open(2, clip, 0)


@pytest.fixture(scope="module")
def hdr_clip(tmp_path_factory) -> Path:
    """12 s HEVC 10-bit tagged as HDR10 (PQ / BT.2020)."""
    out = tmp_path_factory.mktemp("hdr") / "hdr.mkv"
    subprocess.run(
        [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=12",
         # Tag the frames themselves; container-level -color_trc alone isn't kept.
         "-vf", "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084"
                ":colorspace=bt2020nc",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
         str(out)],
        check=True,
    )  # fmt: skip
    return out


def test_hdr_is_detected_and_tone_mapped(mgr, hdr_clip):
    from picturehouse.library.probe import probe_file

    probe = probe_file(Path(shutil.which("ffprobe")), hdr_clip)
    assert probe.video.color_transfer == "smpte2084"
    s = mgr.open(7, hdr_clip, 12.0, hdr=True)
    seg = _probe(mgr.segment(s, 0))
    video = next(st for st in seg["streams"] if st["codec_type"] == "video")
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert video.get("color_transfer") == "bt709"  # SDR output, tagged correctly
