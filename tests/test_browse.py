"""Browsing the server's folders for the Add folder form (and `library browse`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest_services import LAN, LOCAL, make_services
from picturehouse.cli import app as cli_app
from picturehouse.library import browse
from picturehouse.web.app import create_app


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "Media"
    for d in ("Films", "TV Shows", "music", ".hidden", "$RECYCLE.BIN",
              "System Volume Information", "lost+found", "Films/Extras"):  # fmt: skip
        (root / d).mkdir(parents=True)
    (root / "notes.txt").write_text("not a folder", encoding="utf-8")
    return root


def test_lists_folders_only_sorted_without_system_ones(tree):
    listing = browse.list_dir(str(tree))
    assert [d.name for d in listing.dirs] == ["Films", "music", "TV Shows"]
    assert listing.path == str(tree) and listing.parent == str(tree.parent)
    assert all(d.path == str(tree / d.name) for d in listing.dirs)


def test_errors_are_explained(tree):
    with pytest.raises(browse.BrowseError, match="doesn't exist"):
        browse.list_dir(str(tree / "Nope"))
    with pytest.raises(browse.BrowseError, match="isn't a folder"):
        browse.list_dir(str(tree / "notes.txt"))
    with pytest.raises(browse.BrowseError, match="full path"):
        browse.list_dir("Media/Films")


def test_a_drive_or_filesystem_root_has_no_parent(tree):
    top = tree.anchor  # "C:\\" or "/"
    assert browse.list_dir(top).parent is None


def test_roots_exist_and_container_shows_its_own_view(tmp_path, monkeypatch):
    for place in browse.roots():
        assert browse.list_dir(place.path).path  # every starting place opens
    # In a container only the container's storage is offered.
    data, media = tmp_path / "DATA", tmp_path / "media"
    (media / "WD_SSD").mkdir(parents=True)
    data.mkdir()
    monkeypatch.setenv("PICTUREHOUSE_MEDIA_HINT", str(data))
    monkeypatch.setattr(browse, "Path", _rooted(browse.Path, {"/DATA": data, "/media": media}))
    names = [p.name for p in browse.roots()]
    assert names[0].startswith("Storage") and "Drive WD_SSD" in names
    assert "Home" not in names


def _rooted(real_path, mapping):
    """Path() that maps container paths (/DATA, /media) into a temp folder."""

    def factory(*parts):
        p = real_path(*parts)
        key = str(p).replace("\\", "/")
        return mapping.get(key, p)

    return factory


def test_cli_lists_places_and_folders(tree):
    r = CliRunner().invoke(cli_app, ["library", "browse", str(tree)])
    assert r.exit_code == 0 and str(tree / "Films") in r.output and ".hidden" not in r.output
    r = CliRunner().invoke(cli_app, ["library", "browse", str(tree / "Nope")])
    assert r.exit_code != 0 and "doesn't exist" in r.output


def test_api_is_admin_only(tree, media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        with TestClient(create_app(svc), **LAN) as lan:
            assert lan.get("/api/browse").status_code == 403  # folder names are private
        with TestClient(create_app(svc), **LOCAL) as pc:
            assert pc.get("/api/browse").json()["roots"]
            d = pc.get("/api/browse", params={"path": str(tree)}).json()
            assert [x["name"] for x in d["dirs"]] == ["Films", "music", "TV Shows"]
            bad = pc.get("/api/browse", params={"path": str(tree / "Nope")})
            assert bad.status_code == 400 and "doesn't exist" in bad.json()["detail"]
    finally:
        svc.close()


def test_an_unreadable_drive_is_left_out_not_fatal(monkeypatch, tmp_path):
    """Windows: an unformatted drive (or empty card reader) raises WinError 1005
    from is_dir() instead of answering False; that broke the whole list."""
    real_is_dir = browse.Path.is_dir

    def is_dir(self, *a, **k):
        if self == tmp_path / "broken":
            raise OSError(1005, "The volume does not contain a recognized file system")
        return real_is_dir(self, *a, **k)

    monkeypatch.setattr(browse.Path, "is_dir", is_dir)
    monkeypatch.setattr(browse, "in_container", lambda: False)  # CI may run in Docker
    monkeypatch.setattr(
        browse, "_home_shortcuts", lambda: [("Broken", tmp_path / "broken"), ("Fine", tmp_path)]
    )
    names = [p.name for p in browse.roots()]
    assert "Fine" in names and "Broken" not in names
