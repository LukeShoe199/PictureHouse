"""Opt-in local copies of metadata next to the media (metadata/localsave.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from conftest_services import make_services
from fake_tmdb import FakeTMDb
from picturehouse.db.models import MediaItem, ShowMeta
from picturehouse.metadata import api_client, nfo
from picturehouse.metadata.localsave import MARKER, show_folder
from picturehouse.metadata.tmdb import TMDb
from picturehouse.paths import long_path
from picturehouse.streaming import artwork

JPEG = b"\xff\xd8\xff\xe0local-copy-test"


@pytest.fixture
def library(media_library, tmp_path, monkeypatch):
    """A private copy of the fixture media: these tests write into it."""
    dst = tmp_path / "media"
    shutil.copytree(long_path(media_library.absolute()), long_path(dst.absolute()))
    monkeypatch.setattr(api_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(artwork.httpx, "get", lambda url, **kw: httpx.Response(
        200, content=JPEG + url.encode(), headers={"content-type": "image/jpeg"},
        request=httpx.Request("GET", url)))  # fmt: skip
    return dst


def _matched(library: Path, data: Path, tmdb: bool = True):
    svc = make_services(library, data)
    svc.scanner.scan()
    if tmdb:
        svc.matcher.tmdb = TMDb("k", data / "tmdb", transport=FakeTMDb().transport())
    svc.matcher.match()
    return svc


def _film(svc, title: str) -> MediaItem | None:
    with svc.db.session() as s:
        return s.scalar(select(MediaItem).where(MediaItem.kind == "film",
                                                MediaItem.title == title))  # fmt: skip


def test_writes_kodi_files_next_to_films_and_series(library, tmp_path):
    svc = _matched(library, tmp_path / "d1")
    try:
        sm = svc.saver.sync()
        assert sm.written and not sm.failed
        films = library / "Films"
        film_nfo = nfo.parse_nfo((films / "Amélie (2001).nfo").read_text(encoding="utf-8"))
        assert film_nfo.kind == "movie" and film_nfo.ours
        assert film_nfo.title == "Amélie" and film_nfo.tmdb_id == 194
        assert film_nfo.overview.startswith("A shy waitress") and film_nfo.cast
        assert (films / "Amélie (2001)-poster.jpg").read_bytes().startswith(JPEG)
        # Series: the show folder (not "Season 1"), plus each episode.
        another = library / "TV" / "Another Show (2015)"
        assert nfo.parse_nfo((another / "tvshow.nfo").read_text(encoding="utf-8")).ours
        assert (another / "poster.jpg").exists()
        assert not (another / "Season 1" / "tvshow.nfo").exists()
        ep = another / "Season 1" / "another.show.1x03.nfo"
        assert nfo.parse_nfo(ep.read_text(encoding="utf-8")).kind == "episodedetails"
        assert (another / "Season 1" / "another.show.1x03-thumb.jpg").exists()
        assert not (library / "TV" / "Test Show" / "Specials" / "tvshow.nfo").exists()
        # Nothing to do the second time.
        again = svc.saver.sync()
        assert again.written == 0 and again.unchanged == sm.written
    finally:
        svc.close()


def test_never_replaces_files_it_did_not_write(library, tmp_path):
    films = library / "Films"
    mine = films / "Amélie (2001).nfo"
    mine.write_text("<movie><title>Amélie</title><plot>My own notes</plot></movie>",
                    encoding="utf-8")  # fmt: skip
    user_poster = films / "Amélie (2001)-poster.jpg"
    user_poster.write_bytes(b"users own poster")
    show_nfo = library / "TV" / "Test Show" / "tvshow.nfo"
    user_show_nfo = show_nfo.read_bytes()
    svc = _matched(library, tmp_path / "d1")
    try:
        sm = svc.saver.sync()
        assert sm.skipped_foreign >= 2  # Amélie's .nfo, Test Show's tvshow.nfo
        assert "My own notes" in mine.read_text(encoding="utf-8")
        assert user_poster.read_bytes() == b"users own poster"
        assert show_nfo.read_bytes() == user_show_nfo
        # The Grand Test has its own movie.nfo and poster.jpg: ours would be read
        # first and hide them, so nothing is written for it.
        grand = films / "The Grand Test (2019)"
        assert not (grand / "The Grand Test (2019).nfo").exists()
        assert not (grand / "The Grand Test (2019)-poster.jpg").exists()
        # One of ours that the user edited (marker line removed) is theirs now.
        ours = library / "TV" / "Another Show (2015)" / "tvshow.nfo"
        assert ours.exists()
        ours.write_text(ours.read_text(encoding="utf-8").replace(MARKER, ""), encoding="utf-8")
        before = ours.read_text(encoding="utf-8")
        with svc.db.session() as s:
            s.get(ShowMeta, "Another Show").overview = "changed"
        svc.saver.sync()
        assert ours.read_text(encoding="utf-8") == before
    finally:
        svc.close()


def test_copies_bring_metadata_back_without_internet(library, tmp_path):
    svc = _matched(library, tmp_path / "d1")
    svc.saver.sync()
    svc.close()
    # A fresh install (new data dir), no API keys at all.
    fresh = _matched(library, tmp_path / "d2", tmdb=False)
    try:
        amelie = _film(fresh, "Amélie")
        assert amelie is not None and amelie.match_status == "matched"
        assert amelie.overview.startswith("A shy waitress")
        with fresh.db.session() as s:
            meta = s.get(ShowMeta, "Another Show")
            assert meta.match_status == "matched" and meta.overview
        # Artwork comes from the saved poster, with no download.
        art = artwork.find_artwork(amelie, library / "Films", "poster", fresh.ffmpeg.path,
                                   tmp_path / "d2" / "art")  # fmt: skip
        assert art is not None and art.name == "Amélie (2001)-poster.jpg"
    finally:
        fresh.close()


def test_saved_edits_survive_but_online_data_still_refreshes(library, tmp_path):
    svc = _matched(library, tmp_path / "d1")
    amelie = _film(svc, "Amélie")
    new_title = "Le Fabuleux Destin d'Amélie Poulain"
    svc.matcher.edit(amelie.id, None, {"title": new_title})
    svc.saver.sync()
    svc.close()
    nfo_path = library / "Films" / "Amélie (2001).nfo"
    text = nfo_path.read_text(encoding="utf-8")
    assert 'locked="title"' in text
    # Pretend the saved plot is stale: online data must win over our copy...
    nfo_path.write_text(text.replace("A shy waitress", "STALE PLOT"), encoding="utf-8")
    fresh = _matched(library, tmp_path / "d2")
    try:
        a = _film(fresh, new_title)
        assert a is not None, "the user's edited title came back"
        assert "title" in a.locked
        assert a.overview.startswith("A shy waitress")  # ...but not over the edit
        assert a.metadata_source == "tmdb"  # our copy isn't "a user's .nfo"
    finally:
        fresh.close()


def test_unwritable_folder_is_reported_not_raised(library, tmp_path, monkeypatch):
    svc = _matched(library, tmp_path / "d1")
    try:

        def deny(self, *a, **k):
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(Path, "write_bytes", deny)
        sm = svc.saver.sync()
        assert sm.failed and sm.errors and sm.written == 0
    finally:
        svc.close()


def test_show_folder(tmp_path):
    assert show_folder(tmp_path, "Show/Season 1/e.mkv") == tmp_path / "Show"
    assert show_folder(tmp_path, "Show/Specials/e.mkv") == tmp_path / "Show"
    assert show_folder(tmp_path, "Show/e.mkv") == tmp_path / "Show"
    assert show_folder(tmp_path, "e.mkv") is None
