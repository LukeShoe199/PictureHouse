"""Audio health check, against generated clips with known problems."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest_services import LOCAL
from picturehouse.config import parse_config
from picturehouse.library.audiocheck import AudioStats, evaluate, measure
from picturehouse.paths import DataLayout
from picturehouse.services import open_services
from picturehouse.web.app import create_app

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
FFMPEG = Path(shutil.which("ffmpeg") or "ffmpeg")

# Each clip: 20 s of video plus the audio described.
AUDIO = {
    # The South Park pattern: same quiet signal in both channels (no centre/dialogue).
    "dual_mono_quiet": "sine=f=300:d=20,volume=-30dB,pan=stereo|c0=c0|c1=c0",
    # A normal mix: different content left and right at a sensible level.
    "stereo_ok": "sine=f=300:d=20,pan=stereo|c0=c0|c1=0.5*c0[a];sine=f=500:d=20[b];"
                 "[a][b]amerge=inputs=2,pan=stereo|c0=c0|c1=c1+c2",
    "silent": "anullsrc=r=48000:cl=stereo:d=20",
    # Plain mono film: identical channels are expected, level is fine.
    "mono_ok": "sine=f=300:d=20",
}  # fmt: skip


def _make(dest: Path, audio_graph: str) -> Path:
    """Video plus audio built from a single lavfi filter graph."""
    subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=20",
         "-filter_complex", f"{audio_graph}[aout]",
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(dest)],
        check=True,
    )  # fmt: skip
    return dest


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("audio")
    return {name: _make(d / f"{name}.mp4", graph) for name, graph in AUDIO.items()}


def test_measurements(clips):
    dm = measure(FFMPEG, clips["dual_mono_quiet"], 20.0)
    assert dm.dual_mono and dm.level_db < -32
    ok = measure(FFMPEG, clips["stereo_ok"], 20.0)
    assert not ok.dual_mono and -30 < ok.level_db < -5
    assert measure(FFMPEG, clips["silent"], 20.0).level_db <= -60


def test_evaluate_rules():
    quiet_same = AudioStats(level_db=-38.0, diff_db=-150.0, windows=3)
    assert evaluate(quiet_same, channels=2)[0] == "warn"
    assert "dialogue" in evaluate(quiet_same, channels=2)[1]
    # identical channels in a mono source are normal, and loud dual-mono is fine
    assert evaluate(AudioStats(-24.0, -150.0, 3), channels=1) == ("ok", None)
    assert evaluate(AudioStats(-24.0, -150.0, 3), channels=2) == ("ok", None)
    assert evaluate(AudioStats(-70.0, 0.0, 3), channels=2)[0] == "warn"  # silent
    assert evaluate(AudioStats(-40.0, -6.0, 3), channels=2)[0] == "warn"  # very quiet
    assert evaluate(AudioStats(-22.0, -6.0, 3), channels=6) == ("ok", None)


@pytest.fixture
def svc(clips, tmp_path):
    lib = tmp_path / "Films"
    lib.mkdir()
    for name, src in clips.items():
        shutil.copy2(src, lib / f"{name.replace('_', ' ').title()} (2020).mp4")
    toml = f"[[libraries]]\nname='Films'\npath='{lib}'\nkind='films'\n"
    s = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    s.scanner.scan()
    s.lib = lib
    yield s
    s.close()


def _flagged(c: TestClient) -> dict[str, str]:
    return {a["title"]: a["note"] for a in c.get("/api/problems").json()["audio"]}


def test_check_problems_dismiss_and_recheck(svc):
    counts = svc.audio.run()
    assert counts == {"ok": 2, "warn": 2}
    with TestClient(create_app(svc), **LOCAL) as c:
        flagged = _flagged(c)
        assert set(flagged) == {"Dual Mono Quiet", "Silent"}
        assert "dialogue" in flagged["Dual Mono Quiet"]
        assert svc.audio.run() == {}  # unchanged files aren't checked again

        dm_id = next(a["id"] for a in c.get("/api/problems").json()["audio"]
                     if a["title"] == "Dual Mono Quiet")  # fmt: skip
        assert c.post(f"/api/items/{dm_id}/audio-check/dismiss").status_code == 204
        assert set(_flagged(c)) == {"Silent"}
        assert svc.audio.run() == {}  # dismissed stays dismissed...

        f = svc.lib / "Dual Mono Quiet (2020).mp4"  # ...until the file changes
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        svc.scanner.scan()
        assert svc.audio.run() == {"warn": 1}
        assert "Dual Mono Quiet" in _flagged(c)

        assert c.post("/api/audio-check", json={"force": True}).status_code == 202
        assert c.post("/api/items/999999/audio-check/dismiss").status_code == 404


def test_music_and_home_videos_are_not_checked(media_library, tmp_path):
    toml = (f"[[libraries]]\nname='M'\npath='{media_library / 'Music'}'\nkind='music'\n\n"
            f"[[libraries]]\nname='H'\npath='{media_library / 'Home Videos'}'\nkind='home_videos'\n")  # fmt: skip
    s = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    try:
        s.scanner.scan()
        assert s.audio.run() == {}
    finally:
        s.close()
