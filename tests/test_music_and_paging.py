"""Music by folder, paged lists, visualiser levels, sized artwork (TV performance)."""

from __future__ import annotations

import base64
import io
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest_services import LOCAL, make_services
from picturehouse.library.photos import is_photo
from picturehouse.web.app import create_app


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    svc = make_services(media_library, tmp_path_factory.mktemp("music") / "data")
    svc.scanner.scan()
    with TestClient(create_app(svc), **LOCAL) as c:
        c.svc = svc
        yield c
    svc.close()


def test_artists_are_top_level_folders(client):
    r = client.get("/api/music/artists").json()
    names = [a["name"] for a in r["artists"]]
    assert names == sorted(names, key=str.casefold) and r["total"] == len(names)
    assert {"Test Artist", "Björk-ish Ensemble", "Various"} <= set(names)
    test = next(a for a in r["artists"] if a["name"] == "Test Artist")
    assert test["albums"] == 1 and test["tracks"] == 2


def test_artist_page_lists_album_folders_and_loose_tracks(client):
    arts = {a["name"]: a for a in client.get("/api/music/artists").json()["artists"]}
    test = client.get(f"/api/music/artists/{arts['Test Artist']['key']}").json()
    (album,) = test["albums"]
    assert album["title"] == "First Album" and album["year"] == 2020  # "(2020)" from the folder
    assert album["folder"] == "First Album (2020)" and test["tracks"] == []
    various = client.get(f"/api/music/artists/{arts['Various']['key']}").json()
    assert various["albums"] == [] and len(various["tracks"]) == 1  # loose in the artist folder
    assert client.get("/api/music/artists/bm9wZQ").status_code == 404


def test_album_folder_tracks_in_order(client):
    albums = client.get("/api/music/albums").json()["albums"]
    first = next(a for a in albums if a["title"] == "First Album")
    al = client.get(f"/api/music/albums/{first['key']}").json()
    assert [t["title"] for t in al["track_list"]] == ["Opening", "Second Song"]
    assert al["artist"] == "Test Artist" and al["art"]["poster"]


def test_lists_page_with_offset_limit_total(client):
    songs = client.get("/api/music/songs").json()
    assert songs["total"] == len(songs["items"]) >= 4
    page = client.get("/api/music/songs", params={"offset": 1, "limit": 2}).json()
    assert page["total"] == songs["total"] and [t["id"] for t in page["items"]] == [
        t["id"] for t in songs["items"][1:3]]  # fmt: skip
    films = client.get("/api/films", params={"sort": "az", "limit": 3}).json()
    assert len(films["items"]) == 3 and films["total"] > 3
    rest = client.get("/api/films", params={"sort": "az", "offset": 3}).json()
    assert len(rest["items"]) == films["total"] - 3
    shows = client.get("/api/shows", params={"limit": 1}).json()
    assert len(shows["shows"]) == 1 and shows["total"] >= 2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_visualiser_levels_for_tracks(client):
    track = client.get("/api/music/songs").json()["items"][0]
    r = client.get(f"/api/items/{track['id']}/levels")
    assert r.status_code == 200, r.text
    d = r.json()
    data = base64.b64decode(d["data"])
    assert d["fps"] == 20 and d["bands"] == 13 and len(data) % 13 == 0
    assert len(data) // 13 >= int(track["duration"] * 20) - 2  # a frame every 50 ms
    film = client.get("/api/films").json()["items"][0]
    assert client.get(f"/api/items/{film['id']}/levels").status_code == 404


def test_artwork_resized_for_grids(client):
    albums = client.get("/api/music/albums").json()["albums"]
    first = next(a for a in albums if a["title"] == "First Album")  # has a 300 px cover.jpg
    full = Image.open(io.BytesIO(client.get(first["art"]["poster"]).content))
    small = client.get(first["art"]["poster"], params={"w": 160})
    assert small.status_code == 200
    assert Image.open(io.BytesIO(small.content)).width == 160 < full.width
    big = client.get(first["art"]["poster"], params={"w": 900})  # never enlarged
    assert Image.open(io.BytesIO(big.content)).width == full.width


def test_raw_files_are_never_photos():
    for name in ("IMG_1.CR2", "a.nef", "b.ARW", "c.dng", "d.raf", "e.orf", "f.rw2", "g.raw"):
        assert not is_photo(name), name
    assert is_photo("IMG_1.JPG") and is_photo("x.heic")
