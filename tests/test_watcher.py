from __future__ import annotations

import shutil
import time

import pytest
from sqlalchemy import select

from picturehouse.config import parse_config
from picturehouse.db.models import MediaItem
from picturehouse.library import watcher as watcher_mod
from picturehouse.library.watcher import FolderWatcher, _relevant
from picturehouse.paths import DataLayout
from picturehouse.services import open_services


def test_filter():
    import watchfiles

    add = watchfiles.Change.added
    assert _relevant(add, "D:/m/Films/A (2001).mkv")
    assert _relevant(add, "D:/m/Films/Some Folder")  # folder rename/delete
    assert not _relevant(add, "D:/m/Films/notes.txt")
    assert not _relevant(add, "D:/m/Films/sample/A sample.mkv")
    assert not _relevant(add, "D:/m/Films/.hidden/A.mkv")
    assert not _relevant(add, "D:/m/Films/Thumbs.db")


def _wait_for(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.25)
    return False


@pytest.fixture
def watched(media_library, tmp_path, monkeypatch):
    # Shorter quiet period so the test doesn't wait 5 s per change.
    monkeypatch.setattr(watcher_mod, "SETTLE_MS", 400)
    lib = tmp_path / "Films"
    lib.mkdir()
    toml = f"[[libraries]]\nname='Films'\npath='{lib}'\nkind='films'\n"
    svc = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    svc.scanner.scan()
    svc.watcher = FolderWatcher(svc.db, svc.scanner)
    svc.watcher.start()
    assert _wait_for(lambda: svc.watcher.watching)
    time.sleep(0.5)  # let the OS watch settle
    yield svc, lib
    svc.close()


def _titles(svc, available=True):
    with svc.db.session() as s:
        q = select(MediaItem.title).where(MediaItem.available == available)
        return sorted(s.scalars(q))


def test_add_rename_delete_are_picked_up(watched, media_library):
    svc, lib = watched
    src = media_library / "Films" / "Amélie (2001).mp4"
    shutil.copy2(src, lib / "Amélie (2001).mp4")
    assert _wait_for(lambda: _titles(svc) == ["Amélie"]), _titles(svc)

    (lib / "Amélie (2001).mp4").rename(lib / "Le Fabuleux Destin (2001).mp4")
    assert _wait_for(lambda: _titles(svc) == ["Le Fabuleux Destin"]), _titles(svc)

    (lib / "Le Fabuleux Destin (2001).mp4").unlink()
    assert _wait_for(lambda: _titles(svc) == []), _titles(svc)
    assert "Le Fabuleux Destin" in _titles(svc, available=False)  # kept, not deleted


def test_new_folder_with_film(watched, media_library):
    svc, lib = watched
    shutil.copytree(
        media_library / "Films" / "The Grand Test (2019)", lib / "The Grand Test (2019)"
    )
    assert _wait_for(lambda: _titles(svc) == ["The Grand Test"]), _titles(svc)


def test_reload_picks_up_new_root(watched, media_library, tmp_path):
    svc, _ = watched
    from picturehouse.library.roots import add_root

    tv = tmp_path / "TV"
    tv.mkdir()
    add_root(svc.config_file, "TV", tv, "tv")
    svc.reload_settings()
    assert _wait_for(lambda: "TV" in svc.watcher.watching)
    time.sleep(0.5)
    shutil.copytree(media_library / "TV" / "Test Show", tv / "Test Show")
    assert _wait_for(lambda: "Pilot" in _titles(svc)), _titles(svc)


def test_drive_plugged_in_and_out_is_scanned_straight_away(media_library, tmp_path, monkeypatch):
    """A library on a USB drive: plugged in after the server started, it
    becomes available (and its films appear) without waiting for the periodic
    scan or "Check now"; unplugged, it's marked offline (nothing deleted)."""
    monkeypatch.setattr(watcher_mod, "SETTLE_MS", 400)
    monkeypatch.setattr(watcher_mod, "RECHECK_MS", 500)
    home = tmp_path / "Home"
    home.mkdir()
    usb = tmp_path / "USB" / "Films"  # not plugged in yet
    toml = (
        f"[[libraries]]\nname='Home'\npath='{home}'\nkind='films'\n"
        f"[[libraries]]\nname='USB'\npath='{usb}'\nkind='films'\n"
    )
    svc = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    try:
        svc.scanner.scan()
        svc.watcher = FolderWatcher(svc.db, svc.scanner)
        svc.watcher.start()
        assert _wait_for(lambda: "Home" in svc.watcher.watching)

        def usb_available():
            from picturehouse.db.models import LibraryRootRow

            with svc.db.session() as s:
                return s.scalar(
                    select(LibraryRootRow.available).where(LibraryRootRow.name == "USB")
                )

        assert usb_available() is False
        usb.mkdir(parents=True)  # plug it in
        shutil.copy2(media_library / "Films" / "Amélie (2001).mp4", usb / "Amélie (2001).mp4")
        assert _wait_for(lambda: usb_available() and _titles(svc) == ["Amélie"], 20), _titles(svc)

        shutil.rmtree(tmp_path / "USB")  # unplug it
        assert _wait_for(lambda: usb_available() is False, 20)
        assert _titles(svc) == [] and "Amélie" in _titles(svc, available=False)
    finally:
        svc.close()


# ---------------------------------------------------------------- idle drives


MOUNTINFO = (
    "22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw\n"
    "40 22 8:17 / /media/My\\040Drive rw,relatime shared:9 - ntfs3 /dev/sdb1 rw\n"
    "41 22 0:50 / /mnt/nas rw,relatime shared:10 - cifs //nas/films rw\n"
    "42 41 0:51 / /mnt/nas/usb rw,relatime shared:11 - exfat /dev/sdc1 rw\n"
)


def test_mount_table_says_what_storage_a_folder_is_on(monkeypatch):
    from pathlib import PurePosixPath

    from picturehouse.library import mounts

    assert mounts.fs_type(PurePosixPath("/media/My Drive/Films"), MOUNTINFO) == "ntfs3"
    assert mounts.fs_type(PurePosixPath("/media/My Drives"), MOUNTINFO) == "ext4"  # not a prefix
    assert mounts.fs_type(PurePosixPath("/mnt/nas/Films"), MOUNTINFO) == "cifs"
    assert mounts.fs_type(PurePosixPath("/mnt/nas/usb/x"), MOUNTINFO) == "exfat"  # deepest wins
    monkeypatch.setattr(mounts.sys, "platform", "linux")
    monkeypatch.setattr(mounts.os, "name", "posix")
    assert not mounts.is_network(PurePosixPath("/media/My Drive/Films"), MOUNTINFO)
    assert mounts.is_network(PurePosixPath("/mnt/nas/Films"), MOUNTINFO)


def test_watched_local_libraries_need_no_periodic_rescan(watched):
    """Rescanning a watched local disk finds nothing new and keeps a NAS's
    drives from ever sleeping, so serve's periodic rescan skips it."""
    svc, _ = watched
    assert svc.watcher.covers("Films")
    assert not svc.watcher.covers("Elsewhere")
    svc.watcher.stop()
    assert not svc.watcher.covers("Films")  # watcher gone: rescan again


def test_idle_watcher_leaves_the_drives_alone(media_library, tmp_path, monkeypatch):
    """While nothing changes and no drive comes or goes, the watched library
    folders aren't looked at (that would wake a sleeping disk). A missing
    library folder is still looked for, and turns up when created."""
    from picturehouse.library import mounts

    monkeypatch.setattr(watcher_mod, "SETTLE_MS", 300)
    monkeypatch.setattr(watcher_mod, "RECHECK_MS", 300)
    monkeypatch.setattr(mounts, "mount_table", lambda: "the same mounts")
    looked: list = []
    real = watcher_mod._is_dir
    monkeypatch.setattr(watcher_mod, "_is_dir", lambda p: looked.append(p) or real(p))
    films, later = tmp_path / "Films", tmp_path / "Later"
    films.mkdir()
    toml = (
        f"[[libraries]]\nname='Films'\npath='{films}'\nkind='films'\n"
        f"[[libraries]]\nname='Later'\npath='{later}'\nkind='films'\n"
    )
    svc = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    try:
        svc.watcher = FolderWatcher(svc.db, svc.scanner)
        svc.watcher.start()
        assert _wait_for(lambda: "Films" in svc.watcher.watching)
        time.sleep(0.3)
        looked.clear()
        time.sleep(2.0)  # several re-check periods with nothing happening
        assert films not in looked and later in looked
        later.mkdir()
        assert _wait_for(lambda: "Later" in svc.watcher.watching, 10)
    finally:
        svc.close()
