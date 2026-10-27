"""Changes made in the media folders show up: a replaced poster.jpg, and an
edited .nfo after Refresh metadata (bugs: both used to stay as they were)."""

from __future__ import annotations

import io
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from conftest_services import LOCAL, make_services
from picturehouse.db.models import MediaItem
from picturehouse.metadata.matcher import Matcher
from picturehouse.web.app import create_app

FILM = "The Grand Test (2019)"
NFO = """<?xml version="1.0" encoding="UTF-8"?>
<movie><title>The Grand Test</title><year>2019</year><plot>{plot}</plot></movie>
"""


@pytest.fixture
def lib(media_library, tmp_path):
    """One film in its own folder, with a poster.jpg and an .nfo that has no
    online id (so it's matched from the .nfo alone)."""
    root = tmp_path / "lib"
    shutil.copytree(media_library / "Films" / FILM, root / "Films" / FILM)
    (root / "Films" / FILM / "movie.nfo").write_text(
        NFO.format(plot="First plot."), encoding="utf-8"
    )
    svc = make_services(root, tmp_path / "data")
    svc.scanner.scan()
    yield svc, root / "Films" / FILM
    svc.close()


def jpeg(colour: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 60), colour).save(buf, "JPEG")
    return buf.getvalue()


def film_id(svc) -> int:
    with svc.db.session() as s:
        return s.scalars(select(MediaItem.id).where(MediaItem.kind == "film")).one()


def test_a_replaced_poster_shows_without_waiting_for_a_cache(lib):
    svc, folder = lib
    (folder / "poster.jpg").write_bytes(jpeg("red"))
    with TestClient(create_app(svc), **LOCAL) as c:
        url = c.get(f"/api/items/{film_id(svc)}").json()["art"]["poster"]
        first = c.get(url)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-cache"
        etag = first.headers["etag"]
        assert c.get(url, headers={"If-None-Match": etag}).status_code == 304  # unchanged: cheap
        (folder / "poster.jpg").write_bytes(jpeg("blue") + b"\0")  # replaced in the folder
        assert c.get(f"/api/items/{film_id(svc)}").json()["art"]["poster"] == url  # same address...
        again = c.get(url, headers={"If-None-Match": etag})
        assert again.status_code == 200 and again.content != first.content  # ...new picture
        assert again.headers["etag"] != etag


def test_refresh_rereads_an_edited_nfo(lib):
    svc, folder = lib
    m = Matcher(svc.db, None)
    m.match()
    with svc.db.session() as s:
        film = s.get(MediaItem, film_id(svc))
        assert film.overview == "First plot." and film.tmdb_id is None
    (folder / "movie.nfo").write_text(NFO.format(plot="Edited plot."), encoding="utf-8")
    m.refresh()
    with svc.db.session() as s:
        assert s.get(MediaItem, film_id(svc)).overview == "Edited plot."
