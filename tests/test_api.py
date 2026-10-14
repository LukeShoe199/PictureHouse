from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from conftest_services import LOCAL, make_services
from picturehouse.web.app import create_app


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    svc = make_services(media_library, tmp_path_factory.mktemp("api") / "data")
    svc.scanner.scan()
    with TestClient(create_app(svc), **LOCAL) as c:
        c.svc = svc
        yield c
    svc.close()


def _film(client, title):
    return next(i for i in client.get("/api/films").json()["items"] if i["title"] == title)


def test_status(client):
    s = client.get("/api/status").json()
    assert s["counts"]["films"] == 9
    assert s["counts"]["shows"] == 2 and s["counts"]["episodes"] == 6
    assert s["counts"]["failed"] == 4
    assert s["dlna"]["enabled"] is False


def test_films_sorting(client):
    az = [i["title"] for i in client.get("/api/films?sort=az").json()["items"]]
    assert az[0] == "Amélie"  # leading articles ("A ", "The ") are ignored
    assert az[-1] == "東京物語"
    assert "Amélie" in az and "東京物語" in az
    years = [i["year"] for i in client.get("/api/films?sort=year").json()["items"]]
    assert years == sorted(years, reverse=True)


def test_shows_and_albums(client):
    shows = client.get("/api/shows?sort=az").json()["shows"]
    assert [s["title"] for s in shows] == ["Another Show", "Test Show"]
    detail = client.get(f"/api/shows/{shows[1]['key']}").json()
    assert [s["season"] for s in detail["season_list"]] == [1, 2, 0]  # specials last
    assert detail["next_up"]["season"] == 1 and detail["next_up"]["episode"] == 1
    albums = client.get("/api/albums").json()["albums"]
    first = next(a for a in albums if a["title"] == "First Album")
    tracks = client.get(f"/api/albums/{first['key']}").json()["track_list"]
    assert [t["track_no"] for t in tracks] == [1, 2]
    assert client.get("/api/shows/bogus").status_code == 404


def test_search(client):
    r = client.get("/api/search", params={"q": "café"}).json()
    assert [t["title"] for t in r["tracks"]] == ["Café"]
    r = client.get("/api/search", params={"q": "test show"}).json()
    assert [s["title"] for s in r["shows"]] == ["Test Show"]


def test_item_detail_and_decisions(client):
    odd = client.get(f"/api/items/{_film(client, 'Odd Codecs')['id']}").json()
    assert odd["playback"]["browser"]["mode"] == "transcode"
    assert [s["language"] for s in odd["streams"] if s["type"] == "audio"] == ["eng", "fra"]
    amelie = client.get(f"/api/items/{_film(client, 'Amélie')['id']}").json()
    assert amelie["playback"]["browser"]["mode"] == "direct"
    assert amelie["playback"]["dlna"]["mode"] == "direct"


def test_direct_play_with_range(client):
    item = _film(client, "Amélie")
    play = client.get(f"/api/items/{item['id']}/play").json()
    assert play["seekable"] and play["url"].endswith("/file")
    r = client.get(play["url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    head = client.head(play["url"])
    assert head.status_code == 200 and int(head.headers["content-length"]) > 100


def test_remux_stream_is_valid_mp4(client, ffprobe, tmp_path):
    item = _film(client, "The Grand Test")
    play = client.get(f"/api/items/{item['id']}/play").json()
    assert play["mode"] == "remux" and not play["seekable"]
    data = client.get(play["url"]).content
    out = tmp_path / "remux.mp4"
    out.write_bytes(data)
    info = json.loads(subprocess.run(
        [str(ffprobe), "-v", "error", "-print_format", "json", "-show_streams", str(out)],
        capture_output=True, check=True).stdout)  # fmt: skip
    assert [s["codec_name"] for s in info["streams"]] == ["h264", "aac"]


def test_progress_round_trip_and_continue(client):
    """Regression: request bodies were silently ignored (models defined in create_app)."""
    item = _film(client, "Old Classic")
    r = client.put(f"/api/items/{item['id']}/progress", json={"position": 1.25})
    assert r.status_code == 204
    assert client.get(f"/api/items/{item['id']}").json()["progress"]["position"] == 1.25
    # continue-watching requires > 5 s
    client.put(f"/api/items/{item['id']}/progress", json={"position": 30})
    ids = [i["id"] for i in client.get("/api/continue").json()["items"]]
    assert item["id"] in ids
    client.put(f"/api/items/{item['id']}/progress", json={"position": 30, "completed": True})
    ids = [i["id"] for i in client.get("/api/continue").json()["items"]]
    assert item["id"] not in ids


def test_full_scan_flag_reaches_scanner(client):
    """Regression for the same body bug: {"full": true} must re-probe everything."""
    import time

    assert client.post("/api/scan", json={"libraries": ["TV"], "full": True}).json()["started"]
    for _ in range(100):
        state = client.get("/api/scan").json()
        if not state["running"] and state["results"]:
            break
        time.sleep(0.1)
    (res,) = state["results"]
    assert res["library"] == "TV" and res["changed"] == 6 and res["unchanged"] == 0


def test_scan_shows_running_before_its_thread_starts(client, monkeypatch):
    """CI race: right after POST /api/scan, a poll could see the *previous*
    scan's finished results (running=False) and take them as this scan's."""
    import threading
    import time

    release = threading.Event()
    real_run = client.svc.scanner._run_claimed

    def held(names, full):
        release.wait(10)  # the scan thread can't begin until we say so
        return real_run(names, full)

    monkeypatch.setattr(client.svc.scanner, "_run_claimed", held)
    assert client.post("/api/scan", json={"libraries": ["TV"]}).json()["started"]
    state = client.get("/api/scan").json()
    assert state["running"] is True and state["results"] == []
    assert client.post("/api/scan", json={}).json()["started"] is False  # one at a time
    release.set()
    for _ in range(100):
        if not client.get("/api/scan").json()["running"]:
            break
        time.sleep(0.1)
    assert [r["library"] for r in client.get("/api/scan").json()["results"]] == ["TV"]


def test_library_add_remove(client, tmp_path):
    folder = tmp_path / "New Films"
    folder.mkdir()
    r = client.post("/api/libraries", json={"name": "New", "path": str(folder), "kind": "films"})
    assert r.status_code == 201 and r.json()["name"] == "New"
    dup = client.post("/api/libraries", json={"name": "new", "path": str(folder)})
    assert dup.status_code == 400 and "already exists" in dup.json()["detail"]
    assert "New" in [lib["name"] for lib in client.get("/api/libraries").json()["libraries"]]
    assert client.delete("/api/libraries/New").status_code == 204
    assert "New" not in [lib["name"] for lib in client.get("/api/libraries").json()["libraries"]]
    # comments in the config file survive the edits
    assert "[[libraries]]" in client.svc.config_file.read_text(encoding="utf-8")


def test_problems_lists_broken_files(client):
    files = client.get("/api/problems").json()["files"]
    assert {f["file"] for f in files} >= {"Broken/Empty File (2016).mkv", "Broken/corrupt.mp3"}


def test_artwork(client):
    grand = _film(client, "The Grand Test")
    r = client.get(grand["art"]["poster"])
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"  # sidecar poster.jpg
    thumb = client.get(grand["art"]["thumb"])  # extracted frame
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"
    amelie = _film(client, "Amélie")  # loose file, no sidecar -> 404, UI falls back
    assert client.get(amelie["art"]["poster"]).status_code == 404
    cafe = client.get("/api/search", params={"q": "café"}).json()["tracks"][0]
    assert client.get(cafe["art"]["poster"]).status_code == 200  # embedded cover


def test_ui_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<" in r.text


# Regressions reported by the web console agent ------------------------------


def test_timestamps_carry_utc_offset(client):
    """SQLite returns naive datetimes; browsers then read them as local time."""
    item = client.get("/api/films").json()["items"][0]
    assert item["added_at"].endswith("+00:00")
    lib = client.get("/api/libraries").json()["libraries"][0]
    assert lib["last_scan_at"].endswith("+00:00")
    assert all(
        f["failed_at"].endswith("+00:00") for f in client.get("/api/problems").json()["files"]
    )


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"name": "Extra Music \U0001f3b5", "kind": "music"}, 201),  # emoji names are fine
        ({"name": "Música clásica", "kind": "music"}, 201),
        ({"name": "a/b", "kind": "music"}, 400),  # path separators are not
        ({"name": "", "kind": "music"}, 400),
        ({"name": "Ctl", "kind": "music", "path_suffix": "\nx"}, 400),  # mid-path control char
    ],
)
def test_add_library_validation_is_400_not_500(client, tmp_path, body, status):
    folder = tmp_path / "lib"
    folder.mkdir()
    payload = {"name": body["name"], "kind": body["kind"],
               "path": str(folder) + body.get("path_suffix", "")}  # fmt: skip
    r = client.post("/api/libraries", json=payload)
    assert r.status_code == status, r.text
    if status == 400:
        assert r.json()["detail"]
    else:
        assert client.delete(f"/api/libraries/{body['name']}").status_code == 204


def test_unreachable_new_library_starts_offline(client, tmp_path):
    r = client.post("/api/libraries", json={"name": "Ghost", "path": str(tmp_path / "nope")})
    assert r.status_code == 201
    assert r.json()["available"] is False and r.json()["unavailable_since"]
    client.delete("/api/libraries/Ghost")


def test_unreadable_file_has_no_playback_mode(client):
    bad = next(f for f in client.get("/api/problems").json()["files"] if "Empty" in f["file"])
    pb = client.get(f"/api/items/{bad['id']}").json()["playback"]
    assert pb["browser"]["mode"] == "none" and "None" not in pb["browser"]["reason"]


def test_scan_unknown_library_is_404(client):
    r = client.post("/api/scan", json={"libraries": ["Nope"]})
    assert r.status_code == 404 and "Nope" in r.json()["detail"]


def test_static_files_must_revalidate(client):
    """Regression: browsers kept a stale app.js after an update."""
    r = client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert (
        client.get("/static/app.js", headers={"If-None-Match": r.headers["etag"]}).status_code
        == 304
    )


def test_hls_playlist_and_segment(client):
    odd = _film(client, "Odd Codecs")  # HEVC 10-bit: needs a transcode
    play = client.get(f"/api/items/{odd['id']}/play").json()
    assert play["hls_url"] == f"/api/items/{odd['id']}/hls.m3u8"
    r = client.get(play["hls_url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.apple")
    assert r.headers["access-control-allow-origin"] == "*"  # Chromecast fetches cross-origin
    seg_url = next(line for line in r.text.splitlines() if line.endswith(".ts"))
    seg = client.get(seg_url)
    assert seg.status_code == 200 and seg.content[:1] == b"\x47"  # MPEG-TS sync byte
    sid = r.headers["x-hls-session"]
    assert client.delete(f"/api/hls/{sid}").status_code == 204
    assert client.get(seg_url).status_code == 404
    amelie = _film(client, "Amélie")  # direct play: no HLS offered
    assert client.get(f"/api/items/{amelie['id']}/play").json()["hls_url"] is None


def test_progress_concurrent_first_saves(client):
    """Regression: two first-time saves at once raised UNIQUE constraint (500)."""
    import concurrent.futures

    item = _film(client, "Web Rip")
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda pos: client.put(
            f"/api/items/{item['id']}/progress", json={"position": pos}).status_code,
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]))  # fmt: skip
    assert codes == [204] * 8


def test_hls_defaults_to_default_audio_and_isolates_players(client):
    odd = _film(client, "Odd Codecs")
    a = client.get(f"/api/items/{odd['id']}/hls.m3u8?c=tab1")
    b = client.get(f"/api/items/{odd['id']}/hls.m3u8?c=tab2")
    assert a.headers["x-hls-session"] != b.headers["x-hls-session"]
    hls = client.app.state.hls
    sess = hls.get(a.headers["x-hls-session"])
    detail = client.get(f"/api/items/{odd['id']}").json()
    audio = [s for s in detail["streams"] if s["type"] == "audio"]
    default = next((s for s in audio if s["default"]), audio[0])
    assert sess.audio_index == default["index"]
    client.delete(f"/api/hls/{a.headers['x-hls-session']}")
    assert hls.get(b.headers["x-hls-session"]) is not None  # the other tab survives
    client.delete(f"/api/hls/{b.headers['x-hls-session']}")


def test_admin_shutdown_only_from_this_machine(media_library, tmp_path):
    from conftest_services import make_services

    svc = make_services(media_library, tmp_path / "d")
    app = create_app(svc)
    calls = []
    app.state.request_shutdown = lambda: calls.append(1)
    with TestClient(app, client=("192.168.1.21", 5000)) as lan:
        assert lan.post("/api/admin/shutdown").status_code == 403
    with TestClient(app, **LOCAL) as local:
        assert local.post("/api/admin/shutdown").status_code == 202
    assert calls == [1]
    svc.close()


def test_android_apk_offered_with_install_type(client):
    import mimetypes

    from picturehouse.web.app import STATIC_DIR

    apk = STATIC_DIR / "downloads" / "PictureHouse.apk"
    assert client.get("/api/status").json()["android_apk"] is apk.is_file()
    # Phones offer to install an APK only when it's served with this type.
    assert mimetypes.guess_type("PictureHouse.apk")[0] == "application/vnd.android.package-archive"
    if apk.is_file():
        r = client.get("/static/downloads/PictureHouse.apk")
        assert r.status_code == 200 and r.content[:2] == b"PK"
        assert r.headers["content-type"] == "application/vnd.android.package-archive"


# ---------------------------------------------------------------- LG TV app


def test_tv_app_finds_server_and_page(client):
    r = client.get("/api/hello", headers={"Origin": "null"})
    assert r.json()["app"] == "picturehouse" and r.json()["tv"] == "/tv/"
    assert r.headers["access-control-allow-origin"] == "*"  # the app runs from file://
    page = client.get("/tv/")
    assert page.status_code == 200 and "/static/tv/tv.js" in page.text
    assert client.get("/static/tv/tv.js").status_code == 200


def test_tv_gets_originals_the_tv_can_play_else_hls(client):
    amelie = _film(client, "Amélie")  # H.264/AAC MP4
    d = client.get(f"/api/items/{amelie['id']}/play", params={"client": "tv"}).json()
    assert d["mode"] == "direct" and d["url"].endswith("/file") and d["seekable"]
    assert d["hls_url"]  # fallback if the TV refuses it after all
    web = _film(client, "Web Rip")  # WebM: not in the TV profile
    d = client.get(f"/api/items/{web['id']}/play", params={"client": "tv"}).json()
    assert d["mode"] == "transcode" and d["url"].endswith("/hls.m3u8")
    # The browser profile is unchanged (Chrome plays WebM itself).
    assert client.get(f"/api/items/{web['id']}/play").json()["mode"] == "direct"


def test_tv_music_plays_or_streams_and_tracks_know_their_album(client):
    tracks = client.get("/api/search", params={"q": "Track"}).json()["tracks"]
    t = tracks[0]
    assert t["album_key"]  # the TV queues the rest of the album with it
    albums = {a["key"] for a in client.get("/api/albums").json()["albums"]}
    assert t["album_key"] in albums
    d = client.get(f"/api/items/{t['id']}/play", params={"client": "tv"}).json()
    assert d["mode"] in ("direct", "transcode")
    if d["mode"] == "transcode":  # audio the TV can't take: an AAC stream, never HLS
        assert d["url"].endswith("/stream.mp4") and d["hls_url"] is None and not d["seekable"]


def test_continue_includes_audiobook_chapters_only_when_asked(client):
    svc = client.svc
    with svc.db.session() as s:
        from sqlalchemy import select

        from picturehouse.db.models import MediaItem as MI

        t = s.scalars(select(MI).where(MI.kind == "track", MI.probe_ok)).first()
        tid, old = t.id, t.duration
        t.duration = 25 * 60  # an audiobook chapter
    try:
        client.put(f"/api/items/{tid}/progress", json={"position": 300, "completed": False})
        assert tid not in [i["id"] for i in client.get("/api/continue").json()["items"]]
        ids = [i["id"] for i in client.get("/api/continue?audiobooks=1").json()["items"]]
        assert tid in ids
    finally:
        with svc.db.session() as s:
            s.get(MI, tid).duration = old
        client.put(f"/api/items/{tid}/progress", json={"position": 0, "completed": True})
