"""PictureHouse Lite (web/lite.py): plain pages for browsers too old for the console."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest_services import LAN, make_services
from picturehouse.web import lite
from picturehouse.web.app import create_app

STATIC = Path(__file__).resolve().parent.parent / "src/picturehouse/web/static"


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    svc = make_services(media_library, tmp_path_factory.mktemp("lite") / "data")
    svc.scanner.scan()
    with TestClient(create_app(svc), **LAN) as c:  # any device, no PIN
        yield c
    svc.close()


def film(client, title):
    return next(i for i in client.get("/api/films").json()["items"] if i["title"] == title)


def links(html: str) -> list[str]:
    return re.findall(r'href="(/lite/[^"]*)"', html)


def test_every_section_is_a_plain_page(client):
    for path in ("/lite", "/lite/", "/lite/films", "/lite/tv", "/lite/music",
                 "/lite/home-videos", "/lite/photos"):  # fmt: skip
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), path
        assert "/static/lite.css" in r.text and "<script" not in r.text  # lists need no JS
    films = client.get("/lite/films").text
    assert "Amélie" in films and "東京物語" in films and "Emoji 🎬 Night" in films
    assert "Nothing here yet." in client.get("/lite/photos").text  # no photo library here


def test_series_seasons_and_episodes(client):
    keys = {s["title"]: s["key"] for s in client.get("/api/shows").json()["shows"]}
    show_link = f"/lite/tv/{keys['Test Show']}"
    assert show_link in links(client.get("/lite/tv").text)
    show = client.get(show_link).text
    assert "Season 1" in show and "Specials" in show
    assert "Play S1 E1" in show  # next up
    ep = next(h for h in links(show) if h.startswith("/lite/item/"))
    page = client.get(ep).text
    assert '<video id="pv"' in page and "Next episode" in page


def test_music_artist_album_track(client):
    arts = {a["name"]: a["key"] for a in client.get("/api/music/artists").json()["artists"]}
    artist = f"/lite/music/{arts['Test Artist']}"
    assert artist in links(client.get("/lite/music").text)
    album = next(h for h in links(client.get(artist).text) if h.startswith("/lite/album/"))
    page = client.get(album).text
    assert "First Album" in page and page.index("Opening") < page.index("Second Song")
    first = next(h for h in links(page) if h.startswith("/lite/item/"))
    track = client.get(first.replace("&amp;", "&")).text
    assert '<audio id="pv"' in track and "Next track: 2. Second Song" not in track
    assert "Next track: Second Song" in track and "&lsaquo; First Album" in track


IPAD = {"user-agent": "Mozilla/5.0 (iPad; CPU OS 9_3_6 like Mac OS X) AppleWebKit/601.1.46 "
                      "(KHTML, like Gecko) Version/9.0 Mobile/13G37 Safari/601.1"}  # fmt: skip
OLD_ANDROID = {"user-agent": "Mozilla/5.0 (Linux; U; Android 4.1; en-gb) AppleWebKit/534.30 "
                             "(KHTML, like Gecko) Version/4.0 Mobile Safari/534.30"}  # fmt: skip


def player_src(html: str) -> str:
    """The one place the page's player loads from (no <source> list)."""
    assert "<source" not in html
    return re.search(r'<(?:video|audio) id="pv" controls preload="none" src="([^"]+)"', html)[1]


def test_direct_files_play_as_they_are(client):
    fid = film(client, "Amélie")["id"]
    for ua in (IPAD, OLD_ANDROID):
        page = client.get(f"/lite/item/{fid}", headers=ua).text
        assert player_src(page) == f"/api/items/{fid}/file"
        assert f'<a class="btn" href="/api/items/{fid}/file">Play full screen</a>' in page


def test_conversions_go_as_hls_to_apple_devices(client):
    """iOS won't play a live progressive stream (no byte ranges): HLS it is."""
    fid = film(client, "Odd Codecs")["id"]
    assert player_src(client.get(f"/lite/item/{fid}", headers=IPAD).text) == (
        f"/api/items/{fid}/hls.m3u8"
    )
    assert player_src(client.get(f"/lite/item/{fid}", headers=OLD_ANDROID).text) == (
        f"/api/items/{fid}/stream.mp4?start=0"
    )


def _it(file, vcodec="h264", acodec="aac", height=1080):
    return {"id": 5, "file": file, "duration": 60.0, "video": {"codec": vcodec, "height": height},
            "streams": [{"type": "audio", "codec": acodec}]}  # fmt: skip


DIRECT_V = {"mode": "direct", "url": "/api/items/5/file", "mime": "video/x-matroska"}
DIRECT_A = {"mode": "direct", "url": "/api/items/5/file", "mime": "audio/flac"}


@pytest.mark.parametrize(
    ("item", "decision", "is_apple", "route"),
    [
        # Chrome plays MKV and FLAC as they are; Safari on iOS doesn't.
        (_it("a.mkv"), DIRECT_V, False, "original file"),
        (_it("a.mkv"), DIRECT_V, True, "HLS"),
        (_it("a.mp4"), {**DIRECT_V, "mime": "video/mp4"}, True, "original file"),
        (_it("a.mp4", height=2160), {**DIRECT_V, "mime": "video/mp4"}, True, "HLS"),  # 4K
        (_it("a.mp4", acodec="opus"), {**DIRECT_V, "mime": "video/mp4"}, True, "HLS"),
        (_it("a.flac", None, "flac"), DIRECT_A, False, "original file"),
        (_it("a.flac", None, "flac"), DIRECT_A, True, "MP3 stream"),
        (_it("a.mp3", None, "mp3"), {**DIRECT_A, "mime": "audio/mpeg"}, True, "original file"),
        (
            _it("a.wma", None, "wmav2"),
            {"mode": "transcode", "url": "x", "mime": "audio/mp4"},
            False,
            "MP3 stream",
        ),  # fmt: skip
    ],
)
def test_each_device_gets_a_stream_it_can_play(item, decision, is_apple, route):
    assert lite.choose(item, decision, 0, is_apple).route == route


def test_apple_detection():
    assert lite.apple(IPAD["user-agent"])
    assert not lite.apple(OLD_ANDROID["user-agent"])
    assert not lite.apple("Mozilla/5.0 (iPad; CPU OS 17_0) AppleWebKit Chrome/120 Safari")


def test_mp3_stream_for_any_audio(client):
    track = client.get("/api/music/songs").json()["items"][0]
    r = client.get(f"/api/items/{track['id']}/stream.mp3")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content[:3] == b"ID3" or r.content[0] == 0xFF  # an MP3, frame sync
    film_id = film(client, "Amélie")["id"]
    assert client.get(f"/api/items/{film_id}/stream.mp3").status_code == 400


def test_resumes_where_you_left_off(client):
    it = film(client, "Old Classic")
    half = round(it["duration"] / 2, 1)
    assert client.put(f"/api/items/{it['id']}/progress", json={"position": half}).status_code == 204
    page = client.get(f"/lite/item/{it['id']}").text
    assert f'data-t="{half:g}"' in page and "Resuming from" in page
    assert f"stream.mp4?start={half:g}" in page  # the progressive stream starts there too
    fresh = client.get(f"/lite/item/{it['id']}?t=0").text
    assert 'data-t="0"' in fresh and "Resuming" not in fresh


def test_progress_script_is_es3():
    """It has to run on Safari 9: no arrows, let/const, template strings, fetch."""
    js = lite.PLAYER_JS
    for modern in ("=>", "let ", "const ", "`", "fetch(", "class "):
        assert modern not in js, modern
    assert "XMLHttpRequest" in js and "/progress" in js


def test_missing_things_get_a_lite_page_not_json(client):
    r = client.get("/lite/item/999999")
    assert r.status_code == 404 and "Not available" in r.text and "<html" in r.text
    assert client.get("/lite/tv/bm9wZQ").status_code == 404


def test_text_is_escaped():
    html = lite.card("/lite/item/1", '<img src=x onerror="alert(1)">', "a&b", '/x"y')
    assert "<img" not in html and "&lt;img" in html and "a&amp;b" in html
    assert '"y' not in html  # a quote can't end the style attribute


def test_old_browsers_are_sent_to_lite():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    head = index.split("</head>", 1)[0]
    script = head.split("<script>", 1)[1].split("</script>", 1)[0]
    assert 'location.replace("/lite/")' in script and "ph.full" in script
    # The test itself is ES3; modern syntax only inside the new Function() string.
    outside = re.sub(r'new Function\("[^"]*"\)', "", script)
    assert "=>" not in outside and "?." not in outside and "async" not in outside
    assert head.index(script) < head.index("/static/app.css")  # before anything heavy
    assert 'url=/lite/"' in head  # no JavaScript at all: Lite too


def test_lite_css_avoids_newer_css():
    css = (STATIC / "lite.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for modern in ("display: grid", "display: flex", "var(--", "gap:", "object-fit", "clamp("):
        assert modern not in body, modern


def test_lite_moves_https_visitors_to_plain_http(client):
    """iOS's media player won't use an https stream whose certificate isn't
    installed, even after Safari's "continue" for the page."""
    r = client.get("https://192.168.1.2:9596/lite/item/5?t=0", follow_redirects=False)
    assert (
        r.status_code == 302 and r.headers["location"] == "http://192.168.1.2:9595/lite/item/5?t=0"
    )
    assert client.get("/lite/", follow_redirects=False).status_code == 200  # http: stays


def test_player_reports_reach_the_log(client, caplog):
    with caplog.at_level("INFO", logger="picturehouse.web.lite"):
        r = client.get("/lite/report", params={"ev": "error", "err": "4", "ns": "3",
                                                "p": "https:", "src": "/api/items/5/file"})  # fmt: skip
    assert r.status_code == 204
    rec = next(x for x in caplog.records if x.getMessage() == "Lite player: error")
    assert rec.levelname == "WARNING" and rec.error_code == "4" and rec.protocol == "https:"
    assert 'report("error")' in lite.PLAYER_JS and 'report("play")' in lite.PLAYER_JS
