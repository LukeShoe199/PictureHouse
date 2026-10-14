from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest_services import LOCAL, make_services
from fake_omdb import FakeOMDb
from fake_tmdb import FakeTMDb
from picturehouse.db.models import MediaItem, ShowMeta
from picturehouse.metadata import api_client
from picturehouse.metadata.matcher import Matcher
from picturehouse.metadata.omdb import OMDb, OMDbError, OMDbLimit
from picturehouse.metadata.tmdb import TMDb
from picturehouse.web.app import create_app


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)


def _omdb(tmp_path, fake: FakeOMDb, key: str = "omdb-key") -> OMDb:
    return OMDb(key, tmp_path / "omdb-cache", transport=fake.transport())


def _item(svc, **where) -> MediaItem:
    with svc.db.session() as s:
        q = select(MediaItem)
        for k, v in where.items():
            q = q.where(getattr(MediaItem, k) == v)
        return s.scalars(q).one()


# ------------------------------------------------------------------ client


def test_client_quirks(tmp_path):
    fake = FakeOMDb()
    c = _omdb(tmp_path, fake)
    assert c.search("Amélie", 2001, "movie")[0]["imdbID"] == "tt0211915"
    assert c.search("No Such Film", None, "movie") == []  # "Movie not found!" is not an error
    assert c.by_id("tt0211915")["Title"] == "Amélie"
    n = len(fake.calls)
    c.by_id("tt0211915")
    assert len(fake.calls) == n  # cached
    with pytest.raises(OMDbError, match="rejected"):
        _omdb(tmp_path / "x", FakeOMDb(), key="wrong").by_id("tt0211915")
    fake2 = FakeOMDb()
    fake2.limit_after = 0
    with pytest.raises(OMDbLimit):
        _omdb(tmp_path / "y", fake2).by_id("tt0211915")


# ------------------------------------------------------------------ matcher


@pytest.fixture
def svc(media_library, tmp_path):
    s = make_services(media_library, tmp_path / "data")
    s.scanner.scan()
    yield s
    s.close()


def test_omdb_only(svc, tmp_path):
    fake = FakeOMDb()
    m = Matcher(svc.db, None, _omdb(tmp_path, fake))
    summary = m.match()
    assert summary.online and not summary.errors

    amelie = _item(svc, parsed_title="Amélie")
    assert (amelie.match_status, amelie.metadata_source, amelie.imdb_id) == ("matched", "omdb",
                                                                             "tt0211915")  # fmt: skip
    assert amelie.overview == "OMDb plot for Amélie." and amelie.rating == 8.3
    assert json.loads(amelie.people) == {"director": ["Jean-Pierre Jeunet"],
                                         "cast": ["Audrey Tautou", "Mathieu Kassovitz"]}  # fmt: skip
    assert amelie.poster_url.endswith("amelie.jpg") and amelie.tmdb_id is None
    broadcast = _item(svc, parsed_title="Broadcast Capture")
    assert broadcast.certification is None and broadcast.poster_url is None  # "N/A" -> None
    odd = _item(svc, parsed_title="Odd Codecs")
    assert odd.match_status == "ambiguous"
    cands = json.loads(odd.match_candidates)
    assert {c["imdb_id"] for c in cands} == {"tt5000001", "tt5000002"}
    assert all(c["provider"] == "omdb" and c["tmdb_id"] is None for c in cands)

    # .nfo still wins for Test Show (tvshow.nfo); Another Show comes from OMDb
    with svc.db.session() as s:
        another = s.get(ShowMeta, "Another Show")
        assert (another.metadata_source, another.year, another.certification) == ("omdb", 2015,
                                                                                  "TV-14")  # fmt: skip
        assert json.loads(another.people)["director"] == ["Show Creator"]  # creators
    third = _item(svc, parsed_show="Another Show", episode=3)
    assert third.title == "Third" and third.metadata_source == "omdb" and third.imdb_id == "tt093"
    double = _item(svc, parsed_show="Another Show", episode=1)
    assert double.title == "Pilot Part 1 / Pilot Part 2"


def test_fallback_when_tmdb_finds_nothing(svc, tmp_path):
    fake_o = FakeOMDb()
    tmdb = TMDb("k", tmp_path / "tmdb", transport=FakeTMDb().transport())
    m = Matcher(svc.db, tmdb, _omdb(tmp_path, fake_o))
    m.match()
    # TMDb knows Amélie: OMDb isn't asked about it
    assert _item(svc, parsed_title="Amélie").metadata_source == "tmdb"
    assert not [c for c in fake_o.calls if c.get("s", "").lower() == "amélie"]
    # TMDb has no results for Broadcast Capture: OMDb fills in
    bc = _item(svc, parsed_title="Broadcast Capture")
    assert (bc.match_status, bc.metadata_source) == ("matched", "omdb")
    # Ambiguous on TMDb stays TMDb's question (OMDb isn't a tie-breaker)
    assert _item(svc, parsed_title="Odd Codecs").match_status == "ambiguous"


def test_daily_limit_leaves_items_pending(svc, tmp_path):
    fake = FakeOMDb()
    fake.limit_after = 2
    m = Matcher(svc.db, None, _omdb(tmp_path, fake))
    summary = m.match()
    assert not summary.errors
    with svc.db.session() as s:
        statuses = [i.match_status for i in s.scalars(
            select(MediaItem).where(MediaItem.kind == "film", MediaItem.probe_ok))]  # fmt: skip
    assert "pending" in statuses  # picked up next run, nothing marked unmatched
    calls = len(fake.calls)
    assert calls <= 4  # once the limit hits, OMDb isn't hammered for every remaining item
    fake.limit_after = None  # next day
    m.match()
    assert _item(svc, parsed_title="Amélie").match_status == "matched"


def test_manual_imdb_fix_is_kept(svc, tmp_path):
    fake = FakeOMDb()
    m = Matcher(svc.db, None, _omdb(tmp_path, fake))
    m.match()
    odd = _item(svc, parsed_title="Odd Codecs")
    assert m.match_film(odd.id, imdb_id="tt5000002", manual=True) == "matched"
    fixed = _item(svc, id=odd.id)
    assert (fixed.overview, fixed.metadata_source, fixed.imdb_id) == ("Two.", "manual", "tt5000002")
    fake.calls.clear()
    m.match(force=True)
    m.refresh()
    assert _item(svc, id=odd.id).imdb_id == "tt5000002"
    assert not [c for c in fake.calls if c.get("s", "").lower() == "odd codecs"]


def test_api_fix_by_imdb_and_settings(svc, tmp_path):
    svc.matcher.omdb = _omdb(tmp_path, FakeOMDb())
    svc.matcher.match()
    with TestClient(create_app(svc), **LOCAL) as c:
        st = c.get("/api/metadata").json()
        assert st["omdb"] is True and st["tmdb"] is False
        odd = next(f for f in c.get("/api/unmatched").json()["films"] if f["title"] == "Odd Codecs")
        cands = c.get(f"/api/items/{odd['id']}/candidates").json()["candidates"]
        assert {x["imdb_id"] for x in cands} == {"tt5000001", "tt5000002"}
        r = c.post(f"/api/items/{odd['id']}/match", json={"imdb_id": "tt5000001"})
        assert r.status_code == 200 and r.json()["overview"] == "One."
        assert c.post(f"/api/items/{odd['id']}/match", json={"imdb_id": "nope"}).status_code == 422
        assert c.post(f"/api/items/{odd['id']}/match", json={}).status_code == 400
        r = c.put("/api/settings/metadata", json={"omdb_api_key": "abcd1234"})
        assert r.json()["omdb_api_key_set"] and r.json()["omdb_api_key_hint"] == "1234"
        assert "abcd1234" not in r.text
