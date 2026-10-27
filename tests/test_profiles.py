"""Profiles (picturehouse.profiles): child profiles see kids libraries only,
leaving one needs the exit code, and progress is kept per profile."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest_services import LOCAL, make_services
from picturehouse.cli import app as cli_app
from picturehouse.library.roots import set_root_kids
from picturehouse.profiles import EVERYTHING, SCOPE, ProfileError, ProfileStore
from picturehouse.web.app import create_app

KID = "192.168.1.50"  # the child's tablet
CODE = "4321"


def device(app, address: str) -> TestClient:
    return TestClient(app, client=(address, 50000), base_url="http://192.168.1.2:9595")


@pytest.fixture(scope="module")
def house(media_library, tmp_path_factory):
    """The fixture library with TV as the only kids library, an exit code, a
    child profile "Sam", and the child's tablet in it."""
    svc = make_services(media_library, tmp_path_factory.mktemp("profiles") / "data")
    svc.scanner.scan()
    set_root_kids(svc.config_file, "TV", True)
    svc.reload_settings()
    app = create_app(svc)
    store: ProfileStore = app.state.profiles
    store.set_code(CODE)
    sam = store.add("Sam", child=True)
    store.assign(KID, sam.id)
    with TestClient(app, **LOCAL) as pc:
        yield {"svc": svc, "app": app, "store": store, "sam": sam, "pc": pc}
    svc.close()


def ids(items):
    return {i["id"] for i in items}


def test_a_child_device_sees_only_kids_libraries(house):
    kid = device(house["app"], KID)
    assert kid.get("/api/profiles").json()["current"] == {"id": house["sam"].id, "name": "Sam",
                                                          "child": True}  # fmt: skip
    assert kid.get("/api/films").json()["total"] == 0
    assert kid.get("/api/home-videos").json()["total"] == 0
    assert kid.get("/api/music/artists").json()["total"] == 0
    assert kid.get("/api/albums").json()["albums"] == []
    shows = [s["title"] for s in kid.get("/api/shows").json()["shows"]]
    assert shows == [s["title"] for s in house["pc"].get("/api/shows").json()["shows"]]  # TV: all
    counts = kid.get("/api/status").json()["counts"]
    assert counts["films"] == 0 and counts["episodes"] == 6
    found = kid.get("/api/search", params={"q": "a"}).json()
    assert found["films"] == found["tracks"] == found["albums"] == []


def test_a_child_device_cant_reach_other_things_by_address(house):
    film = house["pc"].get("/api/films").json()["items"][0]["id"]
    kid = device(house["app"], KID)
    for path in (f"/api/items/{film}", f"/api/items/{film}/play", f"/api/items/{film}/file",
                 f"/api/items/{film}/stream.mp4", f"/api/items/{film}/hls.m3u8",
                 f"/api/items/{film}/art/poster", f"/lite/item/{film}"):  # fmt: skip
        assert kid.get(path).status_code == 404, path
    assert kid.get("/lite/films").text.count('class="c ') == 0  # Lite is filtered too


def test_music_cache_doesnt_leak_between_profiles(house):
    pc, kid = house["pc"], device(house["app"], KID)
    assert pc.get("/api/music/artists").json()["total"] > 0  # fills the adult cache first
    assert kid.get("/api/music/artists").json()["total"] == 0
    assert pc.get("/api/music/artists").json()["total"] > 0


def test_leaving_a_child_profile_needs_the_exit_code(house):
    store, app = house["store"], house["app"]
    addr = "192.168.1.51"
    store.assign(addr, house["sam"].id)
    kid = device(app, addr)
    assert kid.put("/api/profiles/current", json={"id": 1}).status_code == 403
    assert kid.put("/api/profiles/current", json={"id": 1, "code": "0000"}).status_code == 403
    r = kid.put("/api/profiles/current", json={"id": 1, "code": CODE})
    assert r.status_code == 200 and r.json()["current"]["name"] == "Main"
    # ...and any device may go *into* a child profile without one
    assert kid.put("/api/profiles/current", json={"id": house["sam"].id}).json()["current"]["child"]


def test_wrong_codes_are_rate_limited(house):
    store, addr = house["store"], "192.168.1.52"
    store.assign(addr, house["sam"].id)
    kid = device(house["app"], addr)
    for _ in range(5):
        assert kid.put("/api/profiles/current", json={"id": 1, "code": "1111"}).status_code == 403
    r = kid.put("/api/profiles/current", json={"id": 1, "code": CODE})
    assert r.status_code == 429  # even the right code waits now
    assert store.for_device(addr).child


def test_the_choice_is_kept_by_the_server_not_the_browser(house):
    """A fresh browser (cleared data, no cookies) on the child's tablet is
    still in the child profile."""
    with device(house["app"], KID) as fresh:
        fresh.cookies.clear()
        assert fresh.get("/api/profiles").json()["current"]["child"] is True


def test_a_child_profile_changes_nothing_even_on_this_pc(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        app = create_app(svc)
        store = app.state.profiles
        store.set_code(CODE)
        sam = store.add("Sam", child=True)
        with TestClient(app, **LOCAL) as pc:
            assert pc.get("/api/admin").json()["admin"] is True
            store.assign("127.0.0.1", sam.id)
            assert pc.get("/api/admin").json()["admin"] is False
            assert pc.get("/api/settings/metadata").status_code == 403
            assert pc.post("/api/profiles", json={"name": "X"}).status_code == 403
            assert pc.put("/api/profiles/current", json={"id": 1, "code": CODE}).status_code == 200
            assert pc.get("/api/admin").json()["admin"] is True
    finally:
        svc.close()


def test_progress_is_per_profile(house):
    app, store = house["app"], house["store"]
    dad = store.add("Dad")
    film = house["pc"].get("/api/films").json()["items"][0]["id"]
    a, b = device(app, "192.168.1.60"), device(app, "192.168.1.61")
    store.assign("192.168.1.61", dad.id)
    assert a.put(f"/api/items/{film}/progress", json={"position": 1.5}).status_code == 204
    assert a.get(f"/api/items/{film}").json()["progress"]["position"] == 1.5
    assert b.get(f"/api/items/{film}").json()["progress"] is None
    assert film not in ids(b.get("/api/continue").json()["items"])
    store.remove(dad.id)
    assert store.for_device("192.168.1.61").id == 1  # back to Main, which has a's progress


def test_rules_for_the_main_profile_and_the_exit_code(tmp_path, media_library):
    svc = make_services(media_library, tmp_path / "data")
    try:
        store = ProfileStore(svc.db, svc.layout.root)
        kid = store.add("Kid")
        with pytest.raises(ProfileError, match="exit code first"):
            store.update(kid.id, child=True)  # no exit code yet
        with pytest.raises(ProfileError, match="can't be removed"):
            store.remove(1)
        store.set_code(CODE)
        with pytest.raises(ProfileError, match="can't be a child"):
            store.update(1, child=True)
        kid = store.add("Kid2", child=True)
        with pytest.raises(ProfileError, match="need an exit code"):
            store.set_code(None)
        with pytest.raises(ProfileError, match="already a profile"):
            store.add("kid2")
        with pytest.raises(ProfileError, match="4 to 12 digits"):
            store.set_code("12ab")
        assert store.find("KID2").id == kid.id
        assert (svc.layout.root / "exit-code").read_text(encoding="ascii").startswith("pbkdf2")
    finally:
        svc.close()


def test_admin_manages_profiles_devices_and_kids_libraries(house):
    pc, svc = house["pc"], house["svc"]
    r = pc.post("/api/profiles", json={"name": "Grandma"})
    assert r.status_code == 201
    gid = r.json()["id"]
    assert pc.put(f"/api/profiles/{gid}", json={"name": "Nan"}).json()["name"] == "Nan"
    assert pc.put("/api/devices/192.168.1.70/profile", json={"profile_id": gid}).status_code == 200
    assert house["store"].for_device("192.168.1.70").name == "Nan"
    lib = pc.put("/api/libraries/Home", json={"kids": True}).json()
    assert lib["kids"] is True and "kids = true" in svc.config_file.read_text(encoding="utf-8")
    assert device(house["app"], KID).get("/api/home-videos").json()["total"] > 0
    assert pc.put("/api/libraries/Home", json={"kids": False}).json()["kids"] is False
    assert pc.delete(f"/api/profiles/{gid}").status_code == 204
    other = device(house["app"], "192.168.1.80")  # no PIN set: other devices can't manage
    assert other.post("/api/profiles", json={"name": "X"}).status_code == 403
    assert other.put("/api/profiles-exit-code", json={"code": "9999"}).status_code == 403


def test_dlna_and_the_cli_see_everything():
    assert SCOPE.get() is EVERYTHING and not EVERYTHING.kids_only


def test_cli(tmp_path):
    data, run = tmp_path / "d", CliRunner()

    def cli(*args, **kw):
        return run.invoke(cli_app, ["--data-dir", str(data), *args], **kw)

    lib = tmp_path / "Kids Films"
    lib.mkdir()
    assert cli("library", "add", "Kids", str(lib), "--kind", "films").exit_code == 0
    assert cli("library", "kids", "Kids").exit_code == 0
    r = cli("profile", "add", "Sam", "--child")
    assert r.exit_code != 0 and "exit code first" in r.output
    assert cli("profile", "exit-code", input=f"{CODE}\n{CODE}\n").exit_code == 0
    assert cli("profile", "add", "Sam", "--child").exit_code == 0
    assert cli("profile", "assign", "192.168.1.50", "sam").exit_code == 0
    out = cli("profile", "list").output
    assert "Sam" in out and "child" in out and "192.168.1.50" in out
    assert "kids libraries: Kids" in out and "exit code: set" in out
    assert cli("profile", "edit", "Sam", "--name", "Samuel").exit_code == 0
    assert cli("profile", "remove", "Samuel").exit_code == 0
    assert cli("profile", "remove", "Main").exit_code != 0


def test_lite_profile_page(house):
    store, addr = house["store"], "192.168.1.90"
    store.assign(addr, house["sam"].id)
    kid = device(house["app"], addr)
    page = kid.get("/lite/profiles").text
    assert "Sam (this device)" in page and 'name="code"' in page  # Main needs the code
    assert "/lite/profiles" in kid.get("/lite/").text  # the bar links to it
    r = kid.post("/lite/profiles", data={"id": "1", "code": "0000"}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert store.for_device(addr).child
    r = kid.post("/lite/profiles", data={"id": "1", "code": CODE}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/lite/"
    assert store.for_device(addr).id == 1
