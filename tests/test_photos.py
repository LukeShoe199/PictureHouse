"""Photo libraries: scanning, albums, renditions, API and CLI."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

import fixture_library
from conftest_services import LOCAL, make_services
from picturehouse.db.models import MediaItem
from picturehouse.web.app import create_app


@pytest.fixture(scope="module")
def svc(media_library, tmp_path_factory):
    photos = media_library / "Photos"
    s = make_services(media_library, tmp_path_factory.mktemp("photos") / "data",
                      extra_toml=f"[[libraries]]\nname = 'Photos'\npath = '{photos}'\n"
                                 "kind = 'photos'\n")  # fmt: skip
    s.scanner.scan()
    yield s
    s.close()


@pytest.fixture(scope="module")
def client(svc):
    with TestClient(create_app(svc), **LOCAL) as c:
        yield c


def _photo(svc, name: str) -> MediaItem:
    with svc.db.session() as s:
        return s.scalar(select(MediaItem).where(MediaItem.rel_path.endswith(name)))


def _expected(media_library):
    return {e.path.split("/", 1)[1]: e for e in fixture_library.load_manifest(media_library)
            if e.category == "photos" and not e.skipped}  # fmt: skip


def test_scan_matches_the_manifest(svc, media_library):
    for rel, e in _expected(media_library).items():
        item = _photo(svc, rel)
        assert item is not None, rel
        if e.kind == "broken":
            assert not item.probe_ok and item.kind == "photo" and item.probe_error
            continue
        if e.expect.get("kind") == "home_video":
            assert item.kind == "home_video" and item.media_type == "video"
            continue
        assert item.kind == "photo" and item.media_type == "image" and item.probe_ok
        assert (item.width, item.height) == (e.expect["width"], e.expect["height"]), rel
        if "taken_at" in e.expect:
            assert item.recorded_at.strftime("%Y-%m-%dT%H:%M:%S") == e.expect["taken_at"]
        else:
            assert item.recorded_at is not None  # file time
        if "album" in e.expect:
            assert item.album == e.expect["album"]
        assert item.match_status == "skipped"


def test_artwork_beside_films_is_not_a_photo(svc):
    with svc.db.session() as s:
        assert not s.scalar(select(MediaItem).where(MediaItem.rel_path.endswith("poster.jpg")))


def test_albums_api(client):
    albums = client.get("/api/photos").json()["albums"]
    by = {a["path"]: a for a in albums}
    assert by["2024 Cornwall"]["photos"] == 2 and by["Screenshots"]["photos"] == 1
    assert albums[0]["path"] == "2024 Cornwall" or albums[0]["to"] >= albums[1]["to"]
    cornwall = client.get(f"/api/photos/albums/{by['2024 Cornwall']['key']}").json()
    names = [p["title"] for p in cornwall["photos"]]
    assert names == ["IMG_1001", "IMG_1002"]  # in the order they were taken
    assert client.get("/api/photos/albums/bm9wZQ").status_code == 404
    assert client.get("/api/status").json()["counts"]["photos"] == 3


def _img(resp) -> Image.Image:
    assert resp.status_code == 200, resp.text
    return Image.open(io.BytesIO(resp.content))


def test_renditions_are_upright_jpegs(client, svc):
    side = _photo(svc, "IMG_1001.JPG")
    thumb = _img(client.get(f"/api/items/{side.id}/photo?size=thumb"))
    assert thumb.format == "JPEG" and thumb.size == (360, 480)  # rotated upright
    screen = _img(client.get(f"/api/items/{side.id}/photo?size=screen"))
    assert screen.size == (480, 640)  # never enlarged
    heic = _photo(svc, "IMG_1002.HEIC")
    if heic is not None:
        assert _img(client.get(f"/api/items/{heic.id}/photo?size=original")).format == "JPEG"
    png = _photo(svc, "logo.png")
    r = client.get(f"/api/items/{png.id}/photo?size=original")
    assert _img(r).format == "PNG"  # browsers show PNGs as they are
    assert "immutable" in r.headers["cache-control"]


def test_photo_items_carry_photo_art(client, svc):
    side = _photo(svc, "IMG_1001.JPG")
    d = client.get(f"/api/items/{side.id}").json()
    assert d["kind"] == "photo" and "/photo?size=thumb" in d["art"]["thumb"]
    film = client.get("/api/films").json()["items"][0]
    assert client.get(f"/api/items/{film['id']}/photo").status_code == 404


def test_broken_picture_is_a_problem_not_a_crash(client, svc):
    broken = _photo(svc, "broken.jpg")
    assert any(f["id"] == broken.id for f in client.get("/api/problems").json()["files"])


def test_cli_lists_albums(svc, media_library, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from picturehouse.cli import app

    cfg = svc.config_file
    r = CliRunner().invoke(
        app, ["--config", str(cfg), "--data-dir", str(svc.layout.root), "photos"]
    )
    assert r.exit_code == 0, r.output
    assert "2024 Cornwall" in r.output and "Screenshots" in r.output
    r = CliRunner().invoke(app, ["--config", str(cfg), "--data-dir", str(svc.layout.root),
                                 "photos", "2024 Cornwall"])  # fmt: skip
    assert r.exit_code == 0 and "IMG_1001.JPG" in r.output and "480x640" in r.output


def test_tv_rendition_and_all_photos(client, svc):
    side = _photo(svc, "IMG_1001.JPG")
    tv = _img(client.get(f"/api/items/{side.id}/photo?size=tv"))
    assert tv.size == (480, 640)  # small original: never enlarged
    d = client.get(f"/api/items/{side.id}").json()
    assert "size=thumb" in d["art"]["thumb"]
    allp = client.get("/api/photos/all").json()
    assert allp["fields"] == ["id", "v", "title", "taken"] and len(allp["rows"]) == 3
    ids = [r[0] for r in allp["rows"]]
    assert ids.index(side.id) < len(ids)  # ordered by date taken
    first = allp["rows"][0]
    assert client.get(f"/api/items/{first[0]}/photo?size=tv&v={first[1]}").status_code == 200
