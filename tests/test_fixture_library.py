"""The fixture library itself: every file exists and ffprobe agrees with the manifest.

If these fail, later phases' tests can't be trusted, so they run first in CI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import fixture_library
from picturehouse.paths import long_path


def _probe(ffprobe: Path, path: Path) -> dict | None:
    proc = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(long_path(path.absolute())),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout.decode("utf-8"))
    return data if data.get("streams") else None


@pytest.fixture(scope="module")
def entries(media_library):
    return fixture_library.load_manifest(media_library)


def test_manifest_covers_every_category(entries):
    built = [e for e in entries if not e.skipped]
    assert {e.category for e in built} == {"films", "tv", "music", "home_videos", "photos"}
    assert {e.kind for e in built} == {"media", "broken", "sidecar", "noise"}


def test_core_entries_never_skipped(entries):
    """Anything built from FFmpeg's native encoders must exist on every platform."""
    skipped = {e.path for e in entries if e.skipped}
    assert "Films/Broken/Truncated Film (2015).mp4" not in skipped
    assert "Music/Test Artist/First Album (2020)/01 - Opening.flac" not in skipped
    assert not [e for e in entries if e.expect.get("long_path") and e.skipped]


def test_every_entry_exists(media_library, entries):
    for e in entries:
        if e.skipped:
            continue
        p = media_library.joinpath(*e.path.split("/"))
        assert long_path(p.absolute()).exists(), e.path


def test_names_are_preserved(media_library):
    names = {p.name for p in media_library.rglob("*")}
    assert "Amélie (2001).mp4" in names
    assert "東京物語 (1953).mkv" in names
    assert "Emoji 🎬 Night (2022).mp4" in names


def test_long_path_really_is_long(media_library, entries):
    (e,) = [e for e in entries if e.expect.get("long_path")]
    assert len(str(media_library.joinpath(*e.path.split("/")).absolute())) > 260


def test_media_probes_match_manifest(media_library, entries, ffprobe):
    for e in entries:
        if e.skipped or e.kind not in ("media", "noise") or "duration" not in e.expect:
            continue
        info = _probe(ffprobe, media_library.joinpath(*e.path.split("/")))
        assert info is not None, f"ffprobe could not read {e.path}"
        streams = info["streams"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        video = [
            s
            for s in streams
            if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
        ]
        exp = e.expect
        if "vcodec" in exp:
            assert video[0]["codec_name"] == exp["vcodec"], e.path
        if "acodec" in exp:
            assert audio[0]["codec_name"] == exp["acodec"], e.path
        if "pix_fmt" in exp:
            assert video[0]["pix_fmt"] == exp["pix_fmt"], e.path
        if "audio_streams" in exp:
            assert len(audio) == exp["audio_streams"], e.path
        if "audio_languages" in exp:
            assert [s["tags"]["language"] for s in audio] == exp["audio_languages"]
        if "subtitle_streams" in exp:
            assert sum(s["codec_type"] == "subtitle" for s in streams) == exp["subtitle_streams"]
        if exp.get("embedded_art"):
            assert any(s.get("disposition", {}).get("attached_pic") for s in streams), e.path
        assert abs(float(info["format"]["duration"]) - exp["duration"]) < 0.5, e.path
        tags = {k.lower(): v for k, v in info["format"].get("tags", {}).items()}
        for key in ("artist", "album", "title"):
            if key in exp and e.category == "music":
                assert tags.get(key) == exp[key], (e.path, key)


def test_broken_files_fail_to_probe(media_library, entries, ffprobe):
    # Photos are read with Pillow, not ffprobe: test_photos.py covers them.
    broken = [e for e in entries if e.kind == "broken" and not e.skipped and e.category != "photos"]
    assert len(broken) >= 4
    for e in broken:
        assert _probe(ffprobe, media_library.joinpath(*e.path.split("/"))) is None, e.path


def test_rebuild_is_cached(media_library):
    before = fixture_library.manifest_path(media_library).stat().st_mtime_ns
    fixture_library.build(media_library)
    assert fixture_library.manifest_path(media_library).stat().st_mtime_ns == before
