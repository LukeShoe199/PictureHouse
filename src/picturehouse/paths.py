"""Filesystem locations: the data directory and named library roots.

Two rules from the plan live here:

* **No hard-coded paths.** Config, database, cache and logs all sit under one
  data directory, chosen by ``--data-dir`` / ``PICTUREHOUSE_DATA_DIR`` / a
  per-platform default.
* **Items are stored relative to a named root.** A library root such as
  ``Films = D:\\Media\\Films`` is stored once; every item under it is stored as
  a POSIX-style relative path (``"Alien (1979)/Alien (1979).mkv"``). Moving to
  Linux means remapping the root, not rewriting every row.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

APP_NAME = "PictureHouse"  # folder name on Windows/macOS
APP_SLUG = "picturehouse"  # lower-case folder name on Linux
LEGACY_SLUG = "mediasrv"  # the working name before the rename
ENV_DATA_DIR = "PICTUREHOUSE_DATA_DIR"
ENV_CONFIG = "PICTUREHOUSE_CONFIG"

# Windows' classic MAX_PATH is 260 including the terminator; leave headroom for
# file names appended later (temp suffixes, sidecars).
_WIN_LONG_PATH_THRESHOLD = 240


def _platform_data_dir(name_win_mac: str, name_linux: str) -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / name_win_mac
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name_win_mac
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / name_linux


def default_data_dir() -> Path:
    """Per-platform default data directory (used when nothing else is set)."""
    return _platform_data_dir(APP_NAME, APP_SLUG)


def legacy_data_dir() -> Path:
    return _platform_data_dir(LEGACY_SLUG, LEGACY_SLUG)


def adopt_legacy_data_dir() -> Path | None:
    """One-time move of the pre-rename default data dir. Returns the new path if
    it moved something. Only touches the *default* locations, never a path the
    user chose explicitly."""
    new, old = default_data_dir(), legacy_data_dir()
    if new.exists() or not old.is_dir():
        return None
    try:
        old.rename(new)
    except OSError:
        return None  # e.g. in use; the old folder simply stays where it was
    return new


def resolve_data_dir(cli_value: Path | None = None) -> Path:
    """Data directory precedence: CLI option > environment > platform default."""
    if cli_value is not None:
        chosen = cli_value
    elif os.environ.get(ENV_DATA_DIR):
        chosen = Path(os.environ[ENV_DATA_DIR])
    else:
        adopt_legacy_data_dir()
        chosen = default_data_dir()
    # absolute() rather than resolve(): resolve() can rewrite mapped drives to
    # UNC paths on Windows, which would surprise users reading logs.
    return chosen.expanduser().absolute()


@dataclass(frozen=True)
class DataLayout:
    """Every location the server writes to, derived from one root."""

    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def db_file(self) -> Path:
        return self.root / "library.db"

    @property
    def backup_dir(self) -> Path:
        return self.root / "backups"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def artwork_dir(self) -> Path:
        return self.cache_dir / "artwork"

    @property
    def transcode_dir(self) -> Path:
        return self.cache_dir / "transcode"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    def writable_dirs(self) -> list[Path]:
        return [
            self.root,
            self.backup_dir,
            self.cache_dir,
            self.artwork_dir,
            self.transcode_dir,
            self.log_dir,
        ]

    def ensure(self) -> None:
        for d in self.writable_dirs():
            d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Named roots
# --------------------------------------------------------------------------


def relative_to_root(root: PurePath, item: PurePath) -> str:
    """Return ``item`` as a POSIX-style path relative to ``root``.

    Works on pure paths so Windows paths (including UNC shares) can be handled
    on any OS. Windows comparison is case-insensitive, as the filesystem is.
    Raises ``ValueError`` if ``item`` is not under ``root``.
    """
    rel = item.relative_to(root)
    if not rel.parts:
        raise ValueError(f"{item} is the root itself, not an item under it")
    return PurePosixPath(*rel.parts).as_posix()


def resolve_in_root(root: Path, rel: str) -> Path:
    """Join a stored relative path back onto a (possibly remapped) root.

    Rejects anything that could escape the root: absolute paths, drive letters
    and ``..`` components. Stored paths come from our own scanner, but this is
    also the function streaming will use with ids from the network.
    """
    if not rel:
        raise ValueError("empty relative path")
    if "\\" in rel:
        raise ValueError(f"stored paths use '/' separators: {rel!r}")
    posix = PurePosixPath(rel)
    if posix.is_absolute() or PureWindowsPath(rel).drive:
        raise ValueError(f"stored path must be relative: {rel!r}")
    # Check raw segments: PurePosixPath silently drops "." and empty ones.
    if any(seg in ("", ".", "..") for seg in rel.split("/")):
        raise ValueError(f"stored path must not contain empty, '.' or '..' segments: {rel!r}")
    return root.joinpath(*posix.parts)


def long_path(path: Path) -> Path:
    """Return a path usable for file I/O even beyond Windows' MAX_PATH.

    On Windows, absolute paths longer than the threshold get the ``\\\\?\\``
    (or ``\\\\?\\UNC\\``) prefix so they open regardless of the system's
    LongPathsEnabled setting. On other platforms the path is returned as-is.
    Use this only at the I/O boundary — never store prefixed paths.
    """
    if sys.platform != "win32":
        return path
    s = str(path)
    if s.startswith("\\\\?\\") or len(s) < _WIN_LONG_PATH_THRESHOLD or not path.is_absolute():
        return path
    if s.startswith("\\\\"):  # UNC: \\server\share\... -> \\?\UNC\server\share\...
        return Path("\\\\?\\UNC\\" + s[2:])
    return Path("\\\\?\\" + s)
