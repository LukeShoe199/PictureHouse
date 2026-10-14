from __future__ import annotations

import asyncio
import shutil
import sys

import pytest

from picturehouse.db.models import MediaItem
from picturehouse.streaming import procs
from picturehouse.streaming.decide import decide_browser, decide_dlna


def item(rel, vcodec="h264", acodec="aac", pix_fmt="yuv420p", media_type="video"):
    return MediaItem(rel_path=rel, vcodec=vcodec, acodec=acodec, pix_fmt=pix_fmt,
                     media_type=media_type, size=1, mtime_ns=0)  # fmt: skip


@pytest.mark.parametrize(("it", "browser", "dlna"), [
    (item("a.mp4"), "direct", "direct"),
    (item("a.mkv"), "remux", "direct"),
    (item("a.mkv", acodec="ac3"), "remux", "direct"),
    (item("a.mkv", vcodec="hevc", pix_fmt="yuv420p10le"), "transcode", "direct"),
    (item("a.mp4", pix_fmt="yuv420p10le"), "transcode", "direct"),  # browsers: 8-bit H.264 only
    (item("a.webm", vcodec="vp9", acodec="opus"), "direct", "transcode"),
    (item("a.avi", vcodec="mpeg4", acodec="mp3"), "transcode", "direct"),
    (item("a.flac", vcodec=None, acodec="flac", pix_fmt=None, media_type="audio"), "direct", "direct"),
    (item("a.wma", vcodec=None, acodec="wmav2", pix_fmt=None, media_type="audio"), "transcode", "transcode"),
])  # fmt: skip
def test_decisions(it, browser, dlna):
    assert decide_browser(it).mode == browser
    assert decide_dlna(it).mode == dlna


def test_float_wav_is_transcoded_for_tvs():
    """Regression: 32-bit float PCM in .wav was offered to TVs as direct play."""
    wav = item("book.wav", vcodec=None, acodec="pcm_f32le", pix_fmt=None, media_type="audio")
    assert decide_dlna(wav).mode == "transcode"
    s16 = item("cd.wav", vcodec=None, acodec="pcm_s16le", pix_fmt=None, media_type="audio")
    assert decide_dlna(s16).mode == "direct"


def test_10bit_h264_mp4_is_not_direct_in_browser():
    d = decide_browser(item("a.mp4", pix_fmt="yuv420p10le"))
    assert d.mode != "direct"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_ffmpeg_killed_when_consumer_stops():
    """A client that disconnects mid-stream must not leave FFmpeg running."""
    cmd = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-re",
           "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-c:v", "mpeg2video",
           "-f", "mpegts", "pipe:1"]  # endless source  # fmt: skip

    async def consume_a_little():
        gen = procs.stream_ffmpeg(cmd, label="test")
        chunk = await gen.__anext__()
        assert chunk
        assert procs.active_count() == 1
        await gen.aclose()  # what Starlette does when the client goes away

    asyncio.run(consume_a_little())
    assert procs.active_count() == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_kill_all():
    p = procs.FFmpegProcess([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
                             "-re", "-f", "lavfi", "-i", "anullsrc", "-f", "null", "-"],
                            label="t")  # fmt: skip
    assert procs.active_count() == 1
    procs.kill_all()
    assert p.proc.poll() is not None
    assert procs.active_count() == 0


def test_hdr_decisions():
    hdr = item("film.mkv", vcodec="hevc", pix_fmt="yuv420p10le")
    hdr.color_transfer = "smpte2084"
    b = decide_browser(hdr)
    assert b.mode == "transcode" and b.hdr and "tone-mapped" in b.reason
    assert decide_dlna(hdr).mode == "direct"  # TVs display HDR themselves
    web = item("hdr.mp4", vcodec="h264", pix_fmt="yuv420p10le")
    web.color_transfer = "arib-std-b67"  # HLG
    assert decide_browser(web).hdr


@pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("ffmpeg"), reason="Windows job objects"
)
def test_children_die_with_the_server(tmp_path):
    """Even a plain subprocess (ffprobe, artwork, audio check) must not outlive
    the server when it is killed abruptly: clean restarts need free ports and
    no stray FFmpeg."""
    import subprocess
    import time

    pidfile = tmp_path / "child.pid"
    script = tmp_path / "server_like.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "from picturehouse.streaming import procs\n"
        "assert procs.tie_children_to_this_process()\n"
        f"p = subprocess.Popen([{shutil.which('ffmpeg')!r}, '-hide_banner', '-loglevel', 'error',\n"
        "    '-re', '-f', 'lavfi', '-i', 'anullsrc', '-f', 'null', '-'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen([sys.executable, str(script)])
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    child_pid = int(pidfile.read_text())
    assert _alive(child_pid)
    parent.kill()  # TerminateProcess: no cleanup code runs in the parent
    parent.wait()
    for _ in range(50):
        if not _alive(child_pid):
            break
        time.sleep(0.1)
    assert not _alive(child_pid), "ffmpeg outlived its parent"


def _alive(pid: int) -> bool:
    import ctypes

    k32 = ctypes.WinDLL("kernel32")
    h = k32.OpenProcess(0x1000 | 0x00100000, False, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(h)


def test_tv_bitrate_limit_converts_big_videos():
    big = item("a.mp4")
    big.bitrate = 24_000_000
    assert decide_dlna(big).mode == "direct"
    d = decide_dlna(big, max_bitrate=12_000_000)
    assert d.mode == "transcode" and not d.copy_video and d.max_bitrate == 12_000_000
    assert "24 Mbps is over the 12 Mbps TV limit" in d.reason
    small = item("b.mp4")
    small.bitrate = 8_000_000
    assert decide_dlna(small, max_bitrate=12_000_000).mode == "direct"
    music = item("c.flac", vcodec=None, acodec="flac", media_type="audio")
    music.bitrate = 50_000_000
    assert "TV limit" not in decide_dlna(music, max_bitrate=1_000_000).reason  # video only


def test_capped_encode_has_maxrate():
    from pathlib import Path

    cmd = procs.mpegts_cmd(Path("ffmpeg"), Path("x.mp4"), start=0, copy_video=False,
                           max_bitrate=12_000_000)  # fmt: skip
    rate = int(cmd[cmd.index("-maxrate") + 1])
    assert 9_000_000 < rate < 12_000_000
    assert int(cmd[cmd.index("-bufsize") + 1]) == rate * 2
    plain = procs.mpegts_cmd(Path("ffmpeg"), Path("x.mp4"), start=0, copy_video=False)
    assert "-maxrate" not in plain


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_audio_transcode_streams_in_small_fragments(tmp_path):
    """Bug: a 32-bit float WAV wouldn't play in the console or on the TV.
    Audio-only fMP4 was fragmented on video keyframes, and there are none, so
    FFmpeg sent one fragment at the very end: players got only the header
    and gave up ("no supported source")."""
    import subprocess
    from pathlib import Path

    wav = tmp_path / "f32.wav"
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=330:d=6", "-ac", "2", "-c:a", "pcm_f32le",
                    str(wav)], check=True)  # fmt: skip
    cmd = procs.fmp4_cmd(Path(shutil.which("ffmpeg")), wav, start=0, audio_only=True,
                         copy_video=False, copy_audio=False)  # fmt: skip
    out = subprocess.run(cmd, capture_output=True, timeout=60, check=True).stdout
    assert out.count(b"moof") >= 5  # about one fragment a second, playable as it arrives
