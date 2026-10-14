from __future__ import annotations

import json
import os
import time

import pytest
from sqlalchemy import select

from conftest_services import make_services
from fake_tmdb import FakeTMDb
from picturehouse.db.models import MediaItem, ShowMeta
from picturehouse.metadata import api_client
from picturehouse.metadata import tmdb as tmdb_mod
from picturehouse.metadata.matcher import Matcher, decide, rank_movies, title_similarity
from picturehouse.metadata.nfo import parse_nfo
from picturehouse.metadata.tmdb import TMDb, TMDbError

# ------------------------------------------------------------------ nfo


def test_parse_movie_nfo():
    d = parse_nfo("""<?xml version="1.0"?><movie><title>Alien</title><year>1979</year>
      <plot>In space.</plot><uniqueid type="tmdb">348</uniqueid><uniqueid type="imdb">tt0078748
      </uniqueid><genre>Horror</genre><genre>Sci-Fi</genre><mpaa>UK:18</mpaa>
      <director>Ridley Scott</director><actor><name>Sigourney Weaver</name></actor></movie>""")
    assert (d.kind, d.title, d.year, d.tmdb_id, d.imdb_id) == ("movie", "Alien", 1979, 348,
                                                               "tt0078748")  # fmt: skip
    assert d.genres == ["Horror", "Sci-Fi"] and d.certification == "18"
    assert d.directors == ["Ridley Scott"] and d.cast == ["Sigourney Weaver"]


def test_parse_url_nfo_and_junk():
    assert parse_nfo("https://www.themoviedb.org/movie/603-the-matrix\n").tmdb_id == 603
    assert parse_nfo("see https://www.imdb.com/title/tt0133093/").imdb_id == "tt0133093"
    assert parse_nfo("just some notes") is None
    assert parse_nfo("<notmovie/>") is None


# ------------------------------------------------------------------ scoring


def test_similarity_and_decide():
    assert title_similarity("Amélie", "Amelie") == 1.0
    assert title_similarity("The Matrix", "Matrix") == 1.0
    assert title_similarity("Fast & Furious", "Fast and Furious") == 1.0
    cands = rank_movies(
        [{"id": 1, "title": "Amélie", "release_date": "2001-01-01"},
         {"id": 2, "title": "Amelie Returns", "release_date": "2015-01-01"}], "Amélie", 2001)  # fmt: skip
    status, best = decide(cands)
    assert status == "matched" and best.tmdb_id == 1
    two = rank_movies(
        [{"id": 1, "title": "Odd Codecs", "release_date": "2020-01-01"},
         {"id": 2, "title": "Odd Codecs", "release_date": "2020-06-01"}], "Odd Codecs", 2020)  # fmt: skip
    assert decide(two)[0] == "ambiguous"
    assert decide([])[0] == "unmatched"


# ------------------------------------------------------------------ matcher end to end


@pytest.fixture
def env(media_library, tmp_path, monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)  # no real backoff waits
    svc = make_services(media_library, tmp_path / "data")
    svc.scanner.scan()
    fake = FakeTMDb()
    client = TMDb("test-key", tmp_path / "tmdb-cache", transport=fake.transport())
    m = Matcher(svc.db, client)
    yield svc, m, fake
    client.close()
    svc.close()


def _item(svc, **where) -> MediaItem:
    with svc.db.session() as s:
        q = select(MediaItem)
        for k, v in where.items():
            q = q.where(getattr(MediaItem, k) == v)
        return s.scalars(q).one()


def test_full_match(env):
    svc, m, fake = env
    summary = m.match()
    assert summary.online and not summary.errors

    grand = _item(svc, parsed_title="The Grand Test")
    # movie.nfo carries tmdb id 999001: TMDb details, then the .nfo's own fields win
    assert grand.metadata_source == "nfo" and grand.tmdb_id == 999001
    assert grand.overview == "A film that exists only to be scanned."  # from .nfo
    assert grand.genres == "Drama" and grand.certification == "12A"  # from TMDb
    assert json.loads(grand.people)["director"] == ["Some Director"]
    assert grand.poster_url.endswith("/w500/grand.jpg")
    assert "/search/movie" not in [c for c in fake.calls if "grand" in c]

    amelie = _item(svc, parsed_title="Amélie")
    assert (amelie.match_status, amelie.metadata_source, amelie.tmdb_id) == ("matched", "tmdb", 194)
    assert amelie.overview.startswith("A shy waitress")

    odd = _item(svc, parsed_title="Odd Codecs")
    assert odd.match_status == "ambiguous"
    assert {c["tmdb_id"] for c in json.loads(odd.match_candidates)} == {5001, 5002}
    assert odd.title == "Odd Codecs" and odd.overview is None  # nothing applied

    assert _item(svc, parsed_title="Broadcast Capture").match_status == "unmatched"
    assert _item(svc, parsed_title="IMG_0001").match_status == "skipped"

    # TV: tvshow.nfo -> tmdb 999002, episodes filled from season data
    with svc.db.session() as s:
        meta = s.get(ShowMeta, "Test Show")
        assert meta.tmdb_id == 999002 and meta.certification == "15"
    pilot = _item(svc, parsed_show="Test Show", season=1, episode=1)
    assert pilot.title == "Pilot (TMDb)" and pilot.show == "Test Show"
    assert pilot.poster_url.endswith("/still1.jpg")
    double = _item(svc, parsed_show="Another Show", episode=1)
    assert double.title == "Double Part 1 / Double Part 2"  # multi-episode file


def test_rescan_keeps_matched_titles(env):
    svc, m, _ = env
    m.match()
    svc.scanner.scan(full=True)
    assert _item(svc, parsed_title="Amélie").overview.startswith("A shy waitress")
    assert _item(svc, parsed_show="Test Show", season=1, episode=1).title == "Pilot (TMDb)"


def test_manual_edits_survive_rematch_and_refresh(env):
    svc, m, _ = env
    m.match()
    amelie = _item(svc, parsed_title="Amélie")
    m.edit(amelie.id, None, {"title": "Le Fabuleux Destin d'Amélie Poulain"})
    m.match(force=True)
    m.refresh()
    svc.scanner.scan(full=True)
    a = _item(svc, id=amelie.id)
    assert a.title == "Le Fabuleux Destin d'Amélie Poulain"
    assert a.overview.startswith("A shy waitress")  # unlocked fields still refresh
    assert "title" in json.loads(a.locked)


def test_fix_ambiguous_with_tmdb_id(env):
    svc, m, fake = env
    m.match()
    odd = _item(svc, parsed_title="Odd Codecs")
    assert m.match_film(odd.id, tmdb_id=5002, manual=True) == "matched"
    fixed = _item(svc, id=odd.id)
    assert (fixed.overview, fixed.metadata_source) == ("Two.", "manual")
    fake.calls.clear()
    m.match(force=True)  # a manual match is refreshed by id, never re-searched
    assert _item(svc, id=odd.id).tmdb_id == 5002
    assert not [c for c in fake.calls if c.startswith("/search/movie")
                and "odd" in c.lower()]  # fmt: skip


def test_show_edit_renames_episodes(env):
    svc, m, _ = env
    m.match()
    m.edit(None, "Another Show", {"title": "Another Show (UK)"})
    assert _item(svc, parsed_show="Another Show", episode=3).show == "Another Show (UK)"


def test_new_episode_reuses_show_match(env):
    svc, m, fake = env
    m.match()
    fake.calls.clear()
    with svc.db.session() as s:
        ep = s.scalars(select(MediaItem).where(MediaItem.parsed_show == "Another Show")).first()
        ep.match_status = "pending"  # what a newly scanned episode looks like
    m.match()
    assert "/search/tv" not in fake.calls


def test_without_tmdb_key_nfo_still_applies(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        svc.scanner.scan()
        Matcher(svc.db, None).match()
        grand = _item(svc, parsed_title="The Grand Test")
        assert grand.metadata_source == "nfo" and grand.overview.startswith("A film")
        assert _item(svc, parsed_title="Amélie").match_status == "pending"  # waits for a key
    finally:
        svc.close()


# ------------------------------------------------------------------ client robustness


def test_retries_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)
    fake = FakeTMDb()
    fake.fail_next = 2
    c = TMDb("k", tmp_path, transport=fake.transport())
    assert c.movie(194)["title"] == "Amélie"
    assert len(fake.calls) == 3


def test_gives_up_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)
    fake = FakeTMDb()
    fake.down = True
    c = TMDb("k", tmp_path, transport=fake.transport())
    with pytest.raises(TMDbError):
        c.movie(194)
    assert len(fake.calls) == tmdb_mod.MAX_ATTEMPTS


def test_offline_uses_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)
    fake = FakeTMDb()
    c = TMDb("k", tmp_path, transport=fake.transport())
    c.movie(194)
    for f in tmp_path.glob("*.json"):  # make the cache entry expired
        old = time.time() - tmdb_mod.DETAIL_TTL - 10
        os.utime(f, (old, old))
    fake.down = True
    assert c.movie(194)["title"] == "Amélie"


def test_cache_avoids_repeat_calls(tmp_path):
    fake = FakeTMDb()
    c = TMDb("k", tmp_path, transport=fake.transport())
    c.movie(194)
    c.movie(194)
    assert len(fake.calls) == 1


def test_bearer_token_vs_api_key(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["key"] = request.url.params.get("api_key")
        return __import__("httpx").Response(200, json={"results": []})

    import httpx

    TMDb("eyJhbGciOi.token", tmp_path / "a", transport=httpx.MockTransport(handler)).search_movie(
        "x"
    )
    assert seen["auth"] == "Bearer eyJhbGciOi.token" and seen["key"] is None
    TMDb("plainkey", tmp_path / "b", transport=httpx.MockTransport(handler)).search_movie("x")
    assert seen["key"] == "plainkey" and seen["auth"] is None


# ---------------------------------------------------------------- matcher v2
# Cases from a real Blu-ray library where file names carry no year.


def test_title_comparison_tolerates_how_people_name_files():
    from picturehouse.metadata.matcher import title_similarity as sim

    assert sim("Back To Future 2", "Back to the Future Part II") == 1.0
    assert sim("The Kings Speech", "The King's Speech") == 1.0
    assert sim("Spider Man Across Spider Verse", "Spider-Man: Across the Spider-Verse") == 1.0
    assert sim("Rogue One", "Rogue One: A Star Wars Story") == 0.92  # main title
    assert sim("Dune", "Dune: Part Two") == 0.92
    assert sim("Alien", "Aliens") < 0.92


def test_query_variants():
    from picturehouse.metadata.matcher import query_variants

    def qs(q):
        return [v for v, _ in query_variants(q)]

    assert qs("Marvels Iron Man 3")[1] == "Iron Man 3"
    assert qs("Green Mile The")[1] == "The Green Mile"
    assert "Rush Hour" in qs("Rush Hour 1")
    v = query_variants("Star Wars - Solo")
    assert ("Solo", "Star Wars") in v and "Star Wars: Solo" in [q for q, _ in v]
    assert "Star Wars" not in [q for q, _ in v]  # would find the 1977 film
    assert ("Dawn Of The Planet Of The Apes", "The Planet Of The Apes") in query_variants(
        "The Planet Of The Apes 1 - Dawn Of The Planet Of The Apes")  # fmt: skip


def _movie(i, title, year, votes):
    return {"id": i, "title": title, "original_title": title,
            "release_date": f"{year}-01-01", "vote_count": votes}  # fmt: skip


def test_popular_film_wins_same_title_without_a_year():
    from picturehouse.metadata.matcher import decide, rank_movies

    results = [_movie(1, "Dune", 2021, 15691), _movie(2, "Dune", 1984, 3575),
               _movie(3, "Dune: Part Two", 2024, 7000)]  # fmt: skip
    status, best = decide(rank_movies(results, "Dune", None), None)
    assert status == "matched" and best.tmdb_id == 1
    # Two popular versions: the user decides.
    results = [_movie(1, "Godzilla", 2014, 10064), _movie(2, "Godzilla", 1998, 4381)]
    assert decide(rank_movies(results, "Godzilla", None), None)[0] == "ambiguous"
    # A year in the file name decides instead of popularity.
    results = [_movie(1, "Dune", 2021, 15691), _movie(2, "Dune", 1984, 3575)]
    status, best = decide(rank_movies(results, "Dune", 1984), 1984)
    assert status == "matched" and best.tmdb_id == 2
    # Unknown popularity (OMDb, old cache): no guessing.
    results = [_movie(1, "Dune", 2021, None), _movie(2, "Dune", 1984, None)]
    assert decide(rank_movies(results, "Dune", None), None)[0] == "ambiguous"


def test_variant_without_the_dropped_words_does_not_win():
    from picturehouse.metadata.matcher import decide, merge_ranked, rank_movies

    solo_wars = _movie(1, "Solo: A Star Wars Story", 2018, 9000)
    other_solo = _movie(2, "Solo", 2008, 30)
    cands = merge_ranked(rank_movies([solo_wars, other_solo], "Solo", None, "Star Wars"))
    status, best = decide(cands, None)
    assert best is None or best.tmdb_id == 1
    assert cands[0].tmdb_id == 1


def test_hand_named_files_are_taken_literally():
    from picturehouse.library.naming import derive

    def film(name):
        n = derive(name, "films", None)
        return n.title, n.year

    assert film("The Darjeeling Limited.mp4") == ("The Darjeeling Limited", None)
    assert film("Mickey 17.mp4") == ("Mickey 17", None)
    assert film("Batman - The Dark Knight 1080p.mp4") == ("Batman - The Dark Knight", None)
    assert film("Heat 1995.mkv") == ("Heat", 1995)
    assert film("Blade Runner 2049.mkv") == ("Blade Runner 2049", None)
    assert film("Alien (Director's Cut).mkv") == ("Alien", None)
    assert film("1917.mp4") == ("1917", None)
    assert film("The.Matrix.1999.1080p.BluRay.x264.mkv") == ("The Matrix", 1999)  # scene: guessit


def test_unsettled_items_retried_once_after_matcher_improves(env, tmp_path):
    from picturehouse.metadata import matcher as matcher_mod

    svc, m, _ = env
    m.match()
    amelie = _item(svc, parsed_title="Amélie")
    assert amelie.match_status == "matched"

    def mark_ambiguous():
        with svc.db.session() as s:
            s.get(MediaItem, amelie.id).match_status = "ambiguous"

    state = tmp_path / "matcher-version"
    m.state_file = state
    mark_ambiguous()
    m.match()  # no version recorded yet: retried
    assert _item(svc, id=amelie.id).match_status == "matched"
    assert state.read_text() == str(matcher_mod.MATCHER_VERSION)

    mark_ambiguous()
    m.match()  # up to date: ambiguous items wait for the user
    assert _item(svc, id=amelie.id).match_status == "ambiguous"
