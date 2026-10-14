from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from picturehouse.paths import (
    ENV_DATA_DIR,
    DataLayout,
    default_data_dir,
    long_path,
    relative_to_root,
    resolve_data_dir,
    resolve_in_root,
)


def test_cli_value_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "env"))
    assert resolve_data_dir(tmp_path / "cli") == (tmp_path / "cli").absolute()


def test_env_beats_default(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "env"))
    assert resolve_data_dir() == (tmp_path / "env").absolute()


def test_default_used_when_nothing_set(monkeypatch):
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    assert resolve_data_dir() == default_data_dir().absolute()
    assert default_data_dir().name in ("PictureHouse", "picturehouse")


def test_layout_is_all_under_root(tmp_path):
    layout = DataLayout(tmp_path / "d")
    layout.ensure()
    for p in [layout.config_file, layout.db_file, *layout.writable_dirs()]:
        assert p == layout.root or layout.root in p.parents
    assert all(d.is_dir() for d in layout.writable_dirs())


@pytest.mark.parametrize(
    ("root", "item", "expected"),
    [
        (
            PureWindowsPath(r"D:\Media\Films"),
            PureWindowsPath(r"D:\Media\Films\Alien (1979)\a.mkv"),
            "Alien (1979)/a.mkv",
        ),
        # Windows comparisons are case-insensitive, like the filesystem.
        (PureWindowsPath(r"d:\media\films"), PureWindowsPath(r"D:\Media\Films\x.mkv"), "x.mkv"),
        # UNC share roots
        (
            PureWindowsPath(r"\\nas\media\TV"),
            PureWindowsPath(r"\\nas\media\TV\Show\Season 01\Show - S01E01.mkv"),
            "Show/Season 01/Show - S01E01.mkv",
        ),
        (
            PureWindowsPath(r"D:\Media\Films"),
            PureWindowsPath(r"D:\Media\Films\東京物語\🎬.mkv"),
            "東京物語/🎬.mkv",
        ),
        (PurePosixPath("/srv/media/films"), PurePosixPath("/srv/media/films/a/b.mkv"), "a/b.mkv"),
    ],
)
def test_relative_to_root(root, item, expected):
    assert relative_to_root(root, item) == expected


@pytest.mark.parametrize(
    ("root", "item"),
    [
        (PureWindowsPath(r"D:\Media\Films"), PureWindowsPath(r"D:\Media\TV\x.mkv")),
        (PureWindowsPath(r"D:\Media\Films"), PureWindowsPath(r"D:\Media\Films")),
        (PureWindowsPath(r"\\nas\media"), PureWindowsPath(r"\\other\media\x.mkv")),
    ],
)
def test_relative_to_root_rejects_outside(root, item):
    with pytest.raises(ValueError):
        relative_to_root(root, item)


def test_windows_item_resolves_on_remapped_linux_root():
    """The Windows -> Linux move: same stored path, new root."""
    rel = relative_to_root(
        PureWindowsPath(r"\\nas\media\TV"), PureWindowsPath(r"\\nas\media\TV\Show\S01\e1.mkv")
    )
    assert PurePosixPath("/mnt/media/tv").joinpath(*rel.split("/")) == PurePosixPath(
        "/mnt/media/tv/Show/S01/e1.mkv"
    )
    assert resolve_in_root(Path("/mnt/media/tv"), rel) == Path("/mnt/media/tv/Show/S01/e1.mkv")


@pytest.mark.parametrize(
    "bad", ["", "../etc/passwd", "a/../../b", "/abs/path", "C:/x.mkv", "a\\b.mkv", "./a.mkv"]
)
def test_resolve_in_root_rejects_escapes(bad):
    with pytest.raises(ValueError):
        resolve_in_root(Path("/root"), bad)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows long-path prefix")
def test_long_path_prefixes_on_windows():
    long = Path("D:/" + "x" * 250 + "/f.mkv")
    assert str(long_path(long)).startswith("\\\\?\\D:")
    assert long_path(Path("D:/short.mkv")) == Path("D:/short.mkv")
    unc = Path("\\\\nas\\media\\" + "y" * 250)
    assert str(long_path(unc)).startswith("\\\\?\\UNC\\nas\\media")


def test_long_path_round_trip_io(tmp_path):
    """Create, write and read a file whose full path exceeds 260 characters."""
    deep = tmp_path.joinpath(*["d" * 60] * 4, "f" * 40 + ".mkv")
    assert len(str(deep)) > 260
    long_path(deep.parent).mkdir(parents=True)
    long_path(deep).write_bytes(b"data")
    assert long_path(deep).read_bytes() == b"data"


def test_legacy_data_dir_is_adopted_once(monkeypatch, tmp_path):
    """The pre-rename default folder (mediasrv) moves to the new name, once."""
    from picturehouse.paths import adopt_legacy_data_dir, legacy_data_dir

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    old = legacy_data_dir()
    old.mkdir(parents=True)
    (old / "library.db").write_bytes(b"db")
    assert adopt_legacy_data_dir() == default_data_dir()
    assert (default_data_dir() / "library.db").read_bytes() == b"db"
    assert not old.exists()
    old.mkdir()
    assert adopt_legacy_data_dir() is None  # new one exists: never overwritten
