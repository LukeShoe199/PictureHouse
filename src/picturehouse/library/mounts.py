"""What kind of storage a folder is on, without touching that storage.

Two questions the background jobs ask so idle disks can sleep (a NAS spins
its drives down after a quiet spell, and any read, even a directory listing
the cache no longer holds, spins them up again):

* Is the folder on a network share? The folder watcher's change events come
  from the local kernel, so changes made by another machine on a share are
  never reported; only those folders need the periodic rescan.
* Did any drive come or go? On Linux the mount table says so from memory
  (``/proc/self/mountinfo``), so the watcher needn't look at the drives.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

MOUNTINFO = Path("/proc/self/mountinfo")

# Filesystem types whose files can change without the local kernel knowing.
NETWORK_FS = {
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "ncpfs", "afs", "9p", "ceph", "glusterfs",
    "fuse.sshfs", "fuse.rclone", "fuse.s3fs", "fuse.glusterfs", "davfs", "fuse.davfs2",
}  # fmt: skip


def _unescape(field: str) -> str:
    """mountinfo escapes spaces and the like as octal (``\\040``)."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and i + 3 < len(field) and field[i + 1 : i + 4].isdigit():
            out.append(chr(int(field[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """(mount point, filesystem type) for each line of a mountinfo file."""
    mounts = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        parts, rest = left.split(), right.split()
        if not sep or len(parts) < 5 or not rest:
            continue
        mounts.append((_unescape(parts[4]), rest[0]))
    return mounts


def mount_table() -> str | None:
    """The mount table as text (Linux; read from memory), or None elsewhere."""
    try:
        return MOUNTINFO.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def fs_type(path: Path, table: str | None = None) -> str | None:
    """Filesystem type of the mount holding ``path`` (Linux), or None."""
    table = mount_table() if table is None else table
    if table is None:
        return None
    p = path.as_posix().rstrip("/") or "/"
    best, best_type = "", None
    for point, kind in parse_mountinfo(table):
        point = point.rstrip("/") or "/"
        under = p == point or p.startswith(point + "/") or point == "/"
        if under and len(point) >= len(best):
            best, best_type = point, kind
    return best_type


def _windows_remote(path: Path) -> bool:
    s = str(path)
    if s.startswith(("\\\\", "//")):
        return True  # a UNC path: \\server\share
    drive = path.drive
    if not drive:
        return False
    try:
        kind = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return kind == 4  # DRIVE_REMOTE: a mapped network drive


def is_network(path: Path, table: str | None = None) -> bool:
    """True when the folder is on a network share (or we can't tell on Linux
    for lack of a mount table, where it's safest to keep rescanning)."""
    if sys.platform == "win32":
        return _windows_remote(path)
    if os.name != "posix":
        return True
    kind = fs_type(path, table)
    if kind is None:
        return sys.platform.startswith("linux")  # no mount table: assume the worst
    return kind in NETWORK_FS
