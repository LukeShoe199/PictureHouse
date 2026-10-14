from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from conftest_services import LOCAL, make_services
from fake_tmdb import FakeTMDb
from picturehouse.metadata import api_client
from picturehouse.metadata.tmdb import TMDb
from picturehouse.web.app import create_app


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(api_client.time, "sleep", lambda s: None)
    data = tmp_path_factory.mktemp("apimeta") / "data"
    svc = make_services(media_library, data)
    svc.scanner.scan()
    svc.matcher.tmdb = TMDb("k", data / "tmdb", transport=FakeTMDb().transport())
    svc.matcher.match()
    with TestClient(create_app(svc), **LOCAL) as c:
        c.svc = svc
        yield c
    svc.close()
    mp.undo()


def _film(c, parsed_title):
    for i in c.get("/api/films").json()["items"]:
        d = c.get(f"/api/items/{i['id']}").json()
        if d["file"].startswith(parsed_title) or i["title"] == parsed_title:
            return d
    raise AssertionError(parsed_title)


def test_item_metadata_fields(client):
    amelie = _film(client, "Amélie")
    assert amelie["overview"].startswith("A shy waitress")
    assert amelie["genres"] == "Comedy, Romance" and amelie["certification"] == "12A"
    m = amelie["metadata"]
    assert m["source"] == "tmdb" and m["status"] == "matched" and m["tmdb_id"] == 194
    assert m["people"]["director"] == ["Some Director"]


def test_status_and_unmatched(client):
    st = client.get("/api/status").json()["metadata"]
    assert st["tmdb"] is True and st["needs_attention"] >= 2
    un = client.get("/api/unmatched").json()
    titles = {f["title"]: f["metadata"]["status"] for f in un["films"]}
    assert titles["Odd Codecs"] == "ambiguous" and titles["Broadcast Capture"] == "unmatched"
    odd = next(f for f in un["films"] if f["title"] == "Odd Codecs")
    assert {c["tmdb_id"] for c in odd["metadata"]["candidates"]} == {5001, 5002}


def test_candidates_and_fix(client):
    odd = next(
        f for f in client.get("/api/unmatched").json()["films"] if f["title"] == "Odd Codecs"
    )
    cands = client.get(f"/api/items/{odd['id']}/candidates").json()["candidates"]
    assert {c["tmdb_id"] for c in cands} == {5001, 5002}
    custom = client.get(f"/api/items/{odd['id']}/candidates", params={"q": "Amélie"}).json()
    assert custom["candidates"][0]["tmdb_id"] == 194
    fixed = client.post(f"/api/items/{odd['id']}/match", json={"tmdb_id": 5002}).json()
    assert fixed["overview"] == "Two." and fixed["metadata"]["source"] == "manual"
    assert odd["id"] not in [f["id"] for f in client.get("/api/unmatched").json()["films"]]


def test_edit_locks_and_unlock(client):
    amelie = _film(client, "Amélie")
    r = client.put(f"/api/items/{amelie['id']}/metadata", json={"title": "Amélie (Director's)"})
    assert r.status_code == 200 and r.json()["title"] == "Amélie (Director's)"
    assert "title" in r.json()["metadata"]["locked"]
    assert client.put(f"/api/items/{amelie['id']}/metadata", json={}).status_code == 400
    # unlock -> rematch puts TMDb's title back
    assert client.post(f"/api/items/{amelie['id']}/unlock").status_code == 202
    for _ in range(100):
        d = client.get(f"/api/items/{amelie['id']}").json()
        if d["title"] == "Amélie" and not client.svc.matcher.running:
            break
        time.sleep(0.05)
    assert d["title"] == "Amélie" and d["metadata"]["locked"] == []


def test_show_metadata_and_rename(client):
    shows = client.get("/api/shows").json()["shows"]
    test_show = next(s for s in shows if s["title"] == "Test Show")
    assert (
        test_show["overview"] == "A show about tests." and test_show["metadata"]["source"] == "nfo"
    )
    another = next(s for s in shows if s["title"] == "Another Show")
    new_key = client.put(f"/api/shows/{another['key']}/metadata",
                         json={"title": "Another Show (UK)"}).json()["key"]  # fmt: skip
    detail = client.get(f"/api/shows/{new_key}").json()
    assert detail["title"] == "Another Show (UK)"
    ep = detail["season_list"][0]["episodes"][0]
    assert ep["show"] == "Another Show (UK)"


def test_tmdb_artwork_is_served(client, monkeypatch):
    """Online posters are downloaded once and cached; local art still wins."""
    import httpx

    from picturehouse.streaming import artwork

    calls = []
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"

    def fake_get(url, **kw):
        calls.append(url)
        return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"},
                              request=httpx.Request("GET", url))  # fmt: skip

    monkeypatch.setattr(artwork.httpx, "get", fake_get)
    amelie = _film(client, "Amélie")
    r = client.get(amelie["art"]["poster"])
    assert r.status_code == 200 and r.content == jpeg
    client.get(amelie["art"]["poster"])
    assert len(calls) == 1 and calls[0].endswith("/w500/amelie.jpg")
    grand = _film(client, "The Grand Test")  # has poster.jpg next to it
    assert client.get(grand["art"]["poster"]).content != jpeg


def test_settings_roundtrip_never_returns_key(client):
    r = client.put("/api/settings/metadata", json={"region": "US"})
    assert r.status_code == 200 and r.json()["region"] == "US"
    assert "tmdb_api_key" not in r.json()
    assert client.put("/api/settings/metadata", json={"region": "usa"}).status_code == 400
    text = client.svc.config_file.read_text(encoding="utf-8")
    assert 'region = "US"' in text and "[[libraries]]" in text
    client.put("/api/settings/metadata", json={"region": "GB"})


def test_candidates_without_key_is_503(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "d")
    svc.scanner.scan(["Films"])
    with TestClient(create_app(svc), **LOCAL) as c:
        item = c.get("/api/films").json()["items"][0]
        r = c.get(f"/api/items/{item['id']}/candidates")
        assert r.status_code == 503 and "key" in r.json()["detail"]
    svc.close()


def test_dlna_didl_includes_overview(client):
    from picturehouse.db.models import MediaItem
    from picturehouse.dlna.content import item_didl

    amelie = _film(client, "Amélie")
    with client.svc.db.session() as s:
        xml = item_didl(s.get(MediaItem, amelie["id"]), "films", "http://x:1")
    assert "<upnp:longDescription>A shy waitress" in xml
    assert "<upnp:genre>Comedy</upnp:genre>" in xml


# Regressions reported by the console agent ----------------------------------


def test_manual_match_without_key_is_503_not_silent(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "d")
    svc.scanner.scan(["Films", "TV"])
    with TestClient(create_app(svc), **LOCAL) as c:
        film = c.get("/api/films").json()["items"][0]
        r = c.post(f"/api/items/{film['id']}/match", json={"tmdb_id": 111})
        assert r.status_code == 503 and "key" in r.json()["detail"]
        show = c.get("/api/shows").json()["shows"][0]
        r = c.post(f"/api/shows/{show['key']}/match", json={"tmdb_id": 111})
        assert r.status_code == 503
    svc.close()


def test_unlock_clears_fields_the_sources_dont_supply(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "d")
    svc.scanner.scan(["Films", "TV"])
    svc.matcher.match()  # no key: .nfo only
    with TestClient(create_app(svc), **LOCAL) as c:
        grand = next(
            i for i in c.get("/api/films").json()["items"] if i["title"] == "The Grand Test"
        )
        c.put(f"/api/items/{grand['id']}/metadata",
              json={"genres": "Drama, Test & <Fun>", "certification": "12A", "title": "Mine"})  # fmt: skip
        c.post(f"/api/items/{grand['id']}/unlock")
        for _ in range(100):
            d = c.get(f"/api/items/{grand['id']}").json()
            if d["metadata"]["status"] == "matched" and not svc.matcher.running:
                break
            time.sleep(0.05)
        assert d["title"] == "The Grand Test" and d["genres"] is None
        assert d["certification"] is None and d["metadata"]["locked"] == []

        show = next(s for s in c.get("/api/shows").json()["shows"] if s["title"] == "Test Show")
        new_key = c.put(f"/api/shows/{show['key']}/metadata", json={"title": "Renamed"}).json()[
            "key"
        ]
        r = c.post(f"/api/shows/{new_key}/unlock").json()
        assert c.get(f"/api/shows/{r['key']}").json()["title"] == "Test Show"
        ep = c.get(f"/api/shows/{r['key']}").json()["season_list"][0]["episodes"][0]
        assert ep["show_key"] == r["key"]
    svc.close()


def test_art_urls_change_when_a_match_changes(client, monkeypatch):
    """Bug: posters stayed missing after matching / fixing a match. The art URL
    never changed, so the console's "no image" memory and the browser's
    1-hour image cache kept serving the pre-match result."""
    import httpx

    from picturehouse.streaming import artwork

    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    monkeypatch.setattr(artwork.httpx, "get", lambda url, **kw: httpx.Response(
        200, content=jpeg, headers={"content-type": "image/jpeg"},
        request=httpx.Request("GET", url)))  # fmt: skip
    # An earlier test saves settings without a key, which drops the client.
    tmdb_dir = client.svc.layout.root / "tmdb"
    monkeypatch.setattr(client.svc.matcher, "tmdb",
                        TMDb("k", tmdb_dir, transport=FakeTMDb().transport()))  # fmt: skip
    before = next(f for f in client.get("/api/unmatched").json()["films"]
                  if f["title"] == "Broadcast Capture")  # fmt: skip
    old = client.get(f"/api/items/{before['id']}").json()["art"]["poster"]
    assert client.get(old).status_code == 404  # unmatched: no poster yet

    r = client.post(f"/api/items/{before['id']}/match", json={"tmdb_id": 194})
    assert r.status_code == 200, r.text
    new = client.get(f"/api/items/{before['id']}").json()["art"]["poster"]
    assert new != old and new.split("?")[0] == old.split("?")[0]
    r = client.get(new)
    assert r.status_code == 200 and r.content == jpeg
    # Lists and DLNA hand out the same fresh URL.
    listed = next(i for i in client.get("/api/films").json()["items"] if i["id"] == before["id"])
    assert listed["art"]["poster"] == new

    # Bug: fixing the match to a record with no poster kept the old film's.
    r = client.post(f"/api/items/{before['id']}/match", json={"tmdb_id": 5002})
    assert r.status_code == 200, r.text
    after = r.json()["art"]["poster"]
    assert after != new and client.get(after).status_code == 404
    with client.svc.db.session() as s:
        from picturehouse.db.models import MediaItem

        it = s.get(MediaItem, before["id"])
        assert it.poster_url is None and it.backdrop_url is None
