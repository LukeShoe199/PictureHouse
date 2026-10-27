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


def test_direct_files_play_as_they_are(client):
    page = client.get(f"/lite/item/{film(client, 'Amélie')['id']}").text
    assert re.search(r'<source src="/api/items/\d+/file" type="video/', page)
    assert "hls.m3u8" not in page and "stream.mp4" not in page


def test_conversions_offer_hls_then_a_progressive_stream(client):
    fid = film(client, "Odd Codecs")["id"]
    page = client.get(f"/lite/item/{fid}").text
    hls = page.index(f'src="/api/items/{fid}/hls.m3u8" type="application/vnd.apple.mpegurl"')
    mp4 = page.index(f'src="/api/items/{fid}/stream.mp4?start=0" type="video/mp4"')
    assert hls < mp4  # iOS takes the first it can play: HLS (seekable)


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
