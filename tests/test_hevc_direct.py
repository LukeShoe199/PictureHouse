"""HEVC for browsers that decode it (bug: every HEVC file was converted to
H.264, which a NAS's processor can't do in real time, so nothing played)."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest_services import LOCAL, make_services
from picturehouse.db.models import MediaItem
from picturehouse.ffmpeg import locate_tool
from picturehouse.streaming.decide import decide_browser, parse_caps
from picturehouse.web.app import create_app


def item(rel: str, vcodec="hevc", pix_fmt="yuv420p", acodec="aac", **kw) -> MediaItem:
    return MediaItem(rel_path=rel, media_type="video", vcodec=vcodec, pix_fmt=pix_fmt,
                     acodec=acodec, **kw)  # fmt: skip


HEVC = parse_caps("hevc")
HEVC10 = parse_caps("hevc,hevc10")


@pytest.mark.parametrize(
    ("it", "caps", "mode", "copy_video"),
    [
        (item("a.mp4"), frozenset(), "transcode", False),  # no caps: as before
        (item("a.mp4"), HEVC, "direct", False),
        (item("a.mp4", pix_fmt="yuv420p10le"), HEVC, "transcode", False),  # 10-bit needs hevc10
        (item("a.mp4", pix_fmt="yuv420p10le"), HEVC10, "direct", False),
        (item("a.mkv", pix_fmt="yuv420p10le"), HEVC10, "remux", True),  # repackaged, not re-encoded
        (item("a.mkv", acodec="ac3"), HEVC, "remux", True),  # video copied, only audio converted
        (item("a.mp4", color_transfer="smpte2084"), HEVC10, "transcode", False),  # HDR: tone-mapped
        (item("a.mp4", vcodec="h264"), HEVC, "direct", False),  # unaffected
    ],
)
def test_decisions(it, caps, mode, copy_video):
    d = decide_browser(it, caps)
    assert (d.mode, d.copy_video) == (mode, copy_video), d.reason


def test_unknown_caps_are_ignored():
    assert parse_caps(" hevc ,av2,hevc10,,x") == {"hevc", "hevc10"}


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    svc = make_services(media_library, tmp_path_factory.mktemp("hevc") / "data")
    svc.scanner.scan()
    with TestClient(create_app(svc), **LOCAL) as c:
        yield c
    svc.close()


def odd(client):
    return next(i for i in client.get("/api/films").json()["items"] if i["title"] == "Odd Codecs")


def test_play_without_caps_converts_as_before(client):
    d = client.get(f"/api/items/{odd(client)['id']}/play").json()
    assert d["mode"] == "transcode" and d["hls_url"]


def test_play_with_caps_repackages_and_keeps_the_hevc_picture(client, tmp_path):
    fid = odd(client)["id"]
    d = client.get(f"/api/items/{fid}/play", params={"caps": "hevc,hevc10"}).json()
    assert d["mode"] == "remux" and d["hls_url"] is None  # HLS would re-encode
    assert d["url"] == f"/api/items/{fid}/stream.mp4?start=0&caps=hevc,hevc10"
    out = tmp_path / "s.mp4"
    out.write_bytes(client.get(d["url"]).content)
    probe = subprocess.run([str(locate_tool("ffprobe")), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True)  # fmt: skip
    assert probe.stdout.strip() == "hevc"


def test_console_detects_hevc_and_falls_back():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "src/picturehouse/web/static/app.js").read_text(
        encoding="utf-8"
    )
    assert "const CAPS = (() =>" in js and "'&caps=' + caps" in js
    assert "p.noCaps = true" in js  # a browser that overpromised gets the conversion
