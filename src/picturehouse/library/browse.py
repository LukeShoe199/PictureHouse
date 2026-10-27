"""Browsing the server's folders, for choosing a library folder.

Lists folders only, never files, as the computer running PictureHouse sees
them: in a container that's the container's view (/DATA, /media/<drive>),
which is exactly the path a library needs. Admin-only over HTTP (see
web/access.py): folder names are private.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from picturehouse.errors import PictureHouseError
from picturehouse.paths import long_path

MAX_ENTRIES = 2000
# Folders that never hold a user's media (Windows, macOS, Linux, NAS).
SKIP = {
    "$recycle.bin", "system volume information", "recycler", "recovery", "lost+found",
    "$windows.~bt", "$windows.~ws", "config.msi", "msocache", "perflogs", "#recycle",
    "@eadir", ".trash", ".trashes", ".spotlight-v100", ".fseventsd",
}  # fmt: skip
HOME_SHORTCUTS = ("Videos", "Movies", "Music", "Pictures", "Downloads")


@dataclass
class Place:
    name: str
    path: str
    kind: str  # "drive" | "folder"


@dataclass
class Listing:
    path: str
    parent: str | None
    dirs: list[Place] = field(default_factory=list)
    truncated: bool = False


class BrowseError(PictureHouseError):
    """A folder that can't be listed (missing, not a folder, no permission)."""


def in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("PICTUREHOUSE_MEDIA_HINT"))


def _subdirs(folder: Path) -> list[Path]:
    try:
        return sorted(
            (p for p in folder.iterdir() if p.is_dir() and not _hidden(p)),
            key=lambda p: p.name.casefold(),
        )
    except OSError:
        return []


# Windows "known folders": often moved (OneDrive, another drive), so ask Windows.
_KNOWN_FOLDERS = {
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


def _windows_known_folder(guid: str) -> Path | None:
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    folder_id = GUID()
    ctypes.memmove(folder_id.data, uuid.UUID(guid).bytes_le, 16)
    out = wintypes.LPWSTR()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


def _home_shortcuts() -> list[tuple[str, Path]]:
    home = Path.home()
    if sys.platform == "win32":
        found = []
        for name, guid in _KNOWN_FOLDERS.items():
            try:
                path = _windows_known_folder(guid)
            except Exception:  # noqa: BLE001 - fall back to the usual place
                path = None
            found.append((name, path or home / name))
        return found
    return [(name, home / name) for name in HOME_SHORTCUTS]


def roots() -> list[Place]:
    """Where to start browsing on this platform (existing places only)."""
    places: list[Place] = []

    def add(name: str, path: Path, kind: str) -> None:
        try:
            # An unformatted or unreadable drive raises here instead of
            # answering False (WinError 1005): leave it out, list the rest.
            if not path.is_dir():
                return
        except OSError:
            return
        if all(p.path != str(path) for p in places):
            places.append(Place(name, str(path), kind))

    if in_container():
        hint = os.environ.get("PICTUREHOUSE_MEDIA_HINT")
        if hint:
            add(f"Storage ({hint})", Path(hint), "drive")
        add("Storage (/DATA)", Path("/DATA"), "drive")
        for d in _subdirs(Path("/media")):
            add(f"Drive {d.name}", d, "drive")
        add("All drives (/media)", Path("/media"), "folder")
        return places

    home = Path.home()
    for name, path in _home_shortcuts():
        add(name, path, "folder")
    if sys.platform == "win32":
        drives = os.listdrives() if hasattr(os, "listdrives") else [
            f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
        ]  # fmt: skip
        for d in drives:
            add(f"Drive {d.rstrip(chr(92))}", Path(d), "drive")
        add("Home", home, "folder")
    else:
        user = home.name
        for base in (
            Path("/media") / user,
            Path("/run/media") / user,
            Path("/media"),
            Path("/mnt"),
        ):
            for d in _subdirs(base):
                if d.name != user:
                    add(f"Drive {d.name}", d, "drive")
        add("Home", home, "folder")
        add("Whole computer (/)", Path("/"), "folder")
    return places


def _hidden(p: Path) -> bool:
    name = p.name
    if name.startswith(".") or name.casefold() in SKIP:
        return True
    if sys.platform == "win32":
        try:
            attrs = os.stat(long_path(p), follow_symlinks=False).st_file_attributes
        except OSError:
            return False
        return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    return False


def list_dir(path: str) -> Listing:
    """The subfolders of ``path`` (absolute), hidden/system ones left out."""
    folder = Path(path)
    if not folder.is_absolute():
        raise BrowseError("Use a full path, starting from a drive or /.")
    try:
        entries = list(os.scandir(long_path(folder)))
    except FileNotFoundError:
        raise BrowseError(f"{path} doesn't exist (any more).") from None
    except NotADirectoryError:
        raise BrowseError(f"{path} isn't a folder.") from None
    except PermissionError:
        raise BrowseError(f"PictureHouse isn't allowed to open {path}.") from None
    except OSError as e:
        raise BrowseError(f"Can't open {path}: {e.strerror or e}") from None
    dirs = []
    for e in entries:
        try:
            if not e.is_dir():
                continue
        except OSError:
            continue
        child = folder / e.name
        if not _hidden(child):
            dirs.append(Place(e.name, str(child), "folder"))
    dirs.sort(key=lambda p: p.name.casefold())
    parent = folder.parent
    return Listing(
        path=str(folder),
        parent=None if parent == folder else str(parent),
        dirs=dirs[:MAX_ENTRIES],
        truncated=len(dirs) > MAX_ENTRIES,
    )
