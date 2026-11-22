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
                                                          "child": True, "avatar": None}  # fmt: skip
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
    toons = tmp_path / "Cartoons"
    toons.mkdir()
    r = cli("library", "add", "Cartoons", str(toons), "--kind", "tv", "--kids")
    assert r.exit_code == 0 and "children's tv" in r.output
    r = cli("profile", "add", "Sam", "--child")
    assert r.exit_code != 0 and "exit code first" in r.output
    assert cli("profile", "exit-code", input=f"{CODE}\n{CODE}\n").exit_code == 0
    assert cli("profile", "add", "Sam", "--child").exit_code == 0
    assert cli("profile", "assign", "192.168.1.50", "sam").exit_code == 0
    out = cli("profile", "list").output
    assert "Sam" in out and "child" in out and "192.168.1.50" in out
    assert "kids libraries: Kids, Cartoons" in out and "exit code: set" in out
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


# ---------------------------------------------------------------- approvals
# A child sees the kids libraries plus titles approved one by one, so a film
# both grown-ups and children watch needn't be copied into the kids folder.


def test_an_approved_film_reaches_a_child_and_nothing_else_does(house):
    pc, kid = house["pc"], device(house["app"], KID)
    films = pc.get("/api/films").json()["items"]
    film, other = films[0]["id"], films[1]["id"]
    assert pc.get(f"/api/items/{film}").json()["kids_ok"] is False
    r = pc.put(f"/api/items/{film}/kids", json={"approved": True})
    try:
        assert r.status_code == 200 and r.json()["kids_ok"] is True
        assert r.json()["kids_library"] is False
        assert ids(kid.get("/api/films").json()["items"]) == {film}
        assert kid.get(f"/api/items/{film}").status_code == 200
        assert kid.get(f"/api/items/{film}/play").status_code == 200
        assert kid.get(f"/api/items/{other}").status_code == 404
        assert kid.get("/api/status").json()["counts"]["films"] == 1
        assert kid.get(f"/lite/item/{film}").status_code == 200
    finally:
        pc.put(f"/api/items/{film}/kids", json={"approved": False})
    assert kid.get("/api/films").json()["total"] == 0
    assert kid.get(f"/api/items/{film}").status_code == 404


def test_an_approved_series_reaches_a_child_with_every_episode(house):
    pc, kid = house["pc"], device(house["app"], KID)
    assert pc.put("/api/libraries/TV", json={"kids": False}).status_code == 200
    try:
        assert kid.get("/api/shows").json()["shows"] == []
        show = pc.get("/api/shows").json()["shows"][0]
        detail = pc.get(f"/api/shows/{show['key']}").json()
        assert detail["kids_ok"] is False and detail["kids_library"] is False
        ep = detail["season_list"][0]["episodes"][0]["id"]
        # An episode alone can't be approved: the series is, whole.
        assert pc.put(f"/api/items/{ep}/kids", json={"approved": True}).status_code == 400
        r = pc.put(f"/api/shows/{show['key']}/kids", json={"approved": True})
        assert r.status_code == 200 and r.json()["kids_ok"] is True
        assert [s["title"] for s in kid.get("/api/shows").json()["shows"]] == [show["title"]]
        mine = kid.get(f"/api/shows/{show['key']}").json()
        assert mine["episodes"] == show["episodes"] and mine["kids_ok"] is True
        assert kid.get(f"/api/items/{ep}").status_code == 200
        assert kid.get("/api/films").json()["total"] == 0
        pc.put(f"/api/shows/{show['key']}/kids", json={"approved": False})
        assert kid.get("/api/shows").json()["shows"] == []
        assert kid.get(f"/api/items/{ep}").status_code == 404
    finally:
        pc.put("/api/libraries/TV", json={"kids": True})


def test_a_series_approval_covers_episodes_found_later(tmp_path, media_library):
    from picturehouse.db.models import MediaItem
    from picturehouse.profiles import Scope, approve_show
    from picturehouse.web import queries as Q

    svc = make_services(media_library, tmp_path / "data")
    try:
        svc.scanner.scan()
        with svc.db.session() as s:
            ep = s.scalars(Q.visible().where(MediaItem.kind == "episode")).first()
            parsed, show, root_id = ep.parsed_show, ep.show, ep.root_id
        approve_show(svc.db, [parsed])
        with svc.db.session() as s:  # a new episode of that series turns up
            s.add(MediaItem(root_id=root_id, rel_path="new/S09E01.mkv", size=1, mtime_ns=1,
                            media_type="video", kind="episode", title="New", show=show,
                            parsed_show=parsed, season=9, episode=1, probe_ok=True))  # fmt: skip
        token = SCOPE.set(Scope(profile_id=2, kids_only=True))
        try:
            with svc.db.session() as s:
                titles = set(s.scalars(Q.visible().with_only_columns(MediaItem.title)))
        finally:
            SCOPE.reset(token)
        assert "New" in titles
    finally:
        svc.close()


def test_only_an_adult_admin_can_approve(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        app = create_app(svc)
        store = app.state.profiles
        store.set_code(CODE)
        store.assign("127.0.0.1", store.add("Sam", child=True).id)
        with TestClient(app, **LOCAL) as pc:  # a child profile, even on the PC itself
            assert pc.put("/api/items/1/kids", json={"approved": True}).status_code == 403
            assert pc.put("/api/shows/eA/kids", json={"approved": True}).status_code == 403
        other = device(app, "192.168.1.81")  # an adult device without the admin PIN
        assert other.put("/api/items/1/kids", json={"approved": True}).status_code == 403
    finally:
        svc.close()


def test_a_library_can_be_hidden_from_some_profiles(house):
    """The kids' folders needn't clutter the grown-ups' profile, while the
    child still sees them."""
    pc, kid = house["pc"], device(house["app"], KID)
    show = pc.get("/api/shows").json()["shows"][0]
    ep = pc.get(f"/api/shows/{show['key']}").json()["season_list"][0]["episodes"][0]["id"]
    r = pc.put("/api/libraries/TV", json={"hidden_from": [1]})
    try:
        assert r.status_code == 200 and r.json()["hidden_from"] == [1] and r.json()["kids"] is True
        assert pc.get("/api/shows").json()["shows"] == []
        assert pc.get("/api/status").json()["counts"]["episodes"] == 0
        assert pc.get(f"/api/items/{ep}").status_code == 404
        assert pc.get("/api/films").json()["total"] > 0  # only that library
        assert kid.get("/api/status").json()["counts"]["episodes"] == 6  # the child still sees it
        lib = next(x for x in pc.get("/api/libraries").json()["libraries"] if x["name"] == "TV")
        assert lib["hidden_from"] == [1]
        assert pc.put("/api/libraries/TV", json={"hidden_from": [99]}).status_code == 404
    finally:
        assert pc.put("/api/libraries/TV", json={"hidden_from": []}).json()["hidden_from"] == []
    assert pc.get("/api/status").json()["counts"]["episodes"] == 6


def test_cli_approvals_and_hiding(tmp_path, media_library):
    from sqlalchemy import select

    from picturehouse.db.models import MediaItem

    data, run = tmp_path / "data", CliRunner()
    svc = make_services(media_library, data)
    svc.scanner.scan()
    with svc.db.session() as s:
        film = s.scalars(select(MediaItem).where(MediaItem.kind == "film")).first().title
        show = s.scalars(select(MediaItem).where(MediaItem.kind == "episode")).first().show
    svc.close()

    def cli(*args, **kw):
        return run.invoke(cli_app, ["--data-dir", str(data), *args], **kw)

    r = cli("kids", "allow", film)
    assert r.exit_code == 0 and "child profiles can see the film" in r.output, r.output
    assert cli("kids", "allow", show).exit_code == 0
    out = cli("kids", "list").output
    assert film in out and show in out and "series" in out
    assert cli("kids", "allow", "No Such Title").exit_code != 0
    assert cli("kids", "remove", film).exit_code == 0
    assert film not in cli("kids", "list").output
    assert cli("profile", "exit-code", input=f"{CODE}\n{CODE}\n").exit_code == 0
    assert cli("profile", "add", "Sam", "--child").exit_code == 0
    r = cli("library", "hide", "TV", "Main")
    assert r.exit_code == 0 and "hidden from Main" in r.output, r.output
    assert "TV: hidden from Main" in cli("profile", "list").output
    assert "shown to every profile" in cli("library", "hide", "TV").output
    assert cli("library", "hide", "Nope", "Main").exit_code != 0


def test_a_childrens_folder_is_added_as_one(house, tmp_path):
    pc, svc = house["pc"], house["svc"]
    folder = tmp_path / "Kids Films"
    folder.mkdir()
    r = pc.post("/api/libraries", json={"name": "Kids Films", "path": str(folder),
                                        "kind": "films", "kids": True})  # fmt: skip
    try:
        assert r.status_code == 201 and r.json()["kids"] is True and r.json()["kind"] == "films"
        assert "kids = true" in svc.config_file.read_text(encoding="utf-8")
    finally:
        assert pc.delete("/api/libraries/Kids Films").status_code == 204


def test_an_approved_film_shows_even_if_its_folder_was_hidden_from_the_child(house):
    """Bug: hiding the grown-ups' Films folder from the child (redundant, and
    greyed out in the console) also blocked the films approved from it."""
    pc, kid, sam = house["pc"], device(house["app"], KID), house["sam"].id
    film = pc.get("/api/films").json()["items"][0]["id"]
    assert pc.put("/api/libraries/Films", json={"hidden_from": [sam]}).status_code == 200
    pc.put(f"/api/items/{film}/kids", json={"approved": True})
    try:
        assert ids(kid.get("/api/films").json()["items"]) == {film}
        assert kid.get(f"/api/items/{film}").status_code == 200
        # Hiding a children's folder from the child still works.
        assert pc.put("/api/libraries/TV", json={"hidden_from": [sam]}).status_code == 200
        assert kid.get("/api/status").json()["counts"]["episodes"] == 0
    finally:
        pc.put(f"/api/items/{film}/kids", json={"approved": False})
        pc.put("/api/libraries/Films", json={"hidden_from": []})
        pc.put("/api/libraries/TV", json={"hidden_from": []})


# ---------------------------------------------------------------- pictures


def _photo(w: int, h: int, fmt: str = "JPEG", exif_rotate: bool = False) -> bytes:
    import io

    from PIL import Image

    im = Image.new("RGB", (w, h), (200, 30, 30))
    im.paste((30, 30, 200), (0, 0, w // 2, h))  # left half blue, right half red
    buf = io.BytesIO()
    if exif_rotate:  # a phone photo held sideways: stored landscape, shown portrait
        exif = Image.Exif()
        exif[0x0112] = 6
        im.save(buf, fmt, exif=exif)
    else:
        im.save(buf, fmt)
    return buf.getvalue()


def test_a_profile_picture_is_squared_upright_and_shown_to_every_device(house, tmp_path):
    import io

    from PIL import Image

    pc, store = house["pc"], house["store"]
    sam = house["sam"].id
    r = pc.put(f"/api/profiles/{sam}/avatar", content=_photo(1200, 800, exif_rotate=True),
               headers={"Content-Type": "image/jpeg"})  # fmt: skip
    try:
        assert r.status_code == 200, r.text
        url = r.json()["avatar"]
        assert url.startswith(f"/api/profiles/{sam}/avatar?v=")
        listed = {p["id"]: p for p in pc.get("/api/profiles").json()["profiles"]}
        assert listed[sam]["avatar"] == url and listed[1]["avatar"] is None
        kid = device(house["app"], KID)  # the child's own device can see it (the picker)
        got = kid.get(url)
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(got.content)) as im:
            assert im.size == (512, 512)
            assert not im.getexif()  # nothing of the original kept (e.g. GPS)
            # Turned upright: the blue half (left when stored) is now on top.
            top, bottom = im.getpixel((256, 60)), im.getpixel((256, 450))
            assert top[2] > top[0] and bottom[0] > bottom[2]
        assert (store.data_dir / "avatars" / f"{sam}.jpg").is_file()
        # Only an admin changes pictures; a child device can't.
        assert kid.put(f"/api/profiles/{sam}/avatar", content=_photo(10, 10)).status_code == 403
        bad = pc.put(f"/api/profiles/{sam}/avatar", content=b"not a picture")
        assert bad.status_code == 400 and "isn't a picture" in bad.json()["detail"]
        png = pc.put(f"/api/profiles/{sam}/avatar", content=_photo(64, 300, "PNG"))
        assert png.status_code == 200 and png.json()["avatar"] != url  # new version, new URL
    finally:
        r = pc.delete(f"/api/profiles/{sam}/avatar")
    assert r.status_code == 200 and r.json()["avatar"] is None
    assert pc.get(f"/api/profiles/{sam}/avatar").status_code == 404


def test_a_removed_profile_takes_its_picture_with_it(house):
    pc, store = house["pc"], house["store"]
    pid = pc.post("/api/profiles", json={"name": "Visitor"}).json()["id"]
    assert pc.put(f"/api/profiles/{pid}/avatar", content=_photo(50, 50)).status_code == 200
    assert store.avatar_file(pid).is_file()
    assert pc.delete(f"/api/profiles/{pid}").status_code == 204
    assert not store.avatar_file(pid).exists()


def test_cli_profile_picture(tmp_path):
    data, run = tmp_path / "d", CliRunner()

    def cli(*args, **kw):
        return run.invoke(cli_app, ["--data-dir", str(data), *args], **kw)

    photo = tmp_path / "me.jpg"
    photo.write_bytes(_photo(300, 200))
    r = cli("profile", "picture", "Main", str(photo))
    assert r.exit_code == 0 and "picture set" in r.output, r.output
    assert (data / "avatars" / "1.jpg").is_file()
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"nope")
    assert cli("profile", "picture", "Main", str(junk)).exit_code != 0
    assert cli("profile", "picture", "Main", "--remove").exit_code == 0
    assert not (data / "avatars" / "1.jpg").exists()
