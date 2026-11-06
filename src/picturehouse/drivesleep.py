"""Put idle USB hard drives to sleep (Linux; for NAS boxes such as ZimaOS).

Why this exists: a NAS's own disk-sleep setting usually works through
``hdparm``, which many USB enclosures don't pass on (``HDIO_DRIVE_CMD:
Invalid argument``). The drive never sleeps, and the NAS's health checks
(``smartctl -n standby`` every few seconds) then keep the enclosure's own
idle timer from running out too. The ATA command itself does get through
when sent as SCSI "ATA pass-through" (what smartctl uses), so that's what
this sends.

How: ``/proc/diskstats`` says, from memory, whether a drive has read or
written anything; a drive that hasn't for ``after`` seconds is sent ATA
STANDBY IMMEDIATE, once. Health checks don't count as activity (they aren't
reads or writes), and once the drive reports standby the NAS's checks leave
it be. The next read wakes it by itself (a spin-up of 10-20 s).

Only rotating disks are touched, and by default only USB ones (internal
disks are the NAS's own job).
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SYS_BLOCK = Path("/sys/block")
DISKSTATS = Path("/proc/diskstats")
DEV = Path("/dev")
CHECK_EVERY = 30.0  # seconds between looks at the counters

ATA_STANDBY_IMMEDIATE = 0xE0
ATA_CHECK_POWER_MODE = 0xE5
_SG_IO = 0x2285


class DriveSleepError(OSError):
    pass


@dataclass(frozen=True)
class Disk:
    name: str  # sdb
    model: str
    usb: bool
    rotational: bool

    @property
    def dev(self) -> Path:
        return DEV / self.name


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def disks(sys_block: Path = SYS_BLOCK) -> list[Disk]:
    """Whole SCSI/SATA/USB disks (sd*), as the kernel lists them."""
    out = []
    for d in sorted(sys_block.glob("sd*")):
        if not (d / "device").exists():
            continue

        try:
            usb = "/usb" in os.path.realpath(d)
        except OSError:
            usb = False
        out.append(
            Disk(d.name, _read(d / "device/model"), usb, _read(d / "queue/rotational") == "1")
        )
    return out


def activity(text: str) -> dict[str, tuple[int, int, int]]:
    """name -> (reads completed, writes completed, I/O in flight)."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 12 and f[3].isdigit():
            out[f[2]] = (int(f[3]), int(f[7]), int(f[11]))
    return out


def read_activity() -> dict[str, tuple[int, int, int]]:
    try:
        return activity(DISKSTATS.read_text(encoding="utf-8"))
    except OSError:
        return {}


# ---------------------------------------------------------------- ATA pass-through


class _SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int), ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte), ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort), ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p), ("cmdp", ctypes.c_void_p), ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint), ("flags", ctypes.c_uint), ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p), ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte), ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte), ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort), ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint), ("info", ctypes.c_uint),
    ]  # fmt: skip


def ata16_cdb(command: int) -> bytes:
    """SCSI ATA PASS-THROUGH (16) for a non-data ATA command, asking for the
    ATA registers back (CK_COND), as smartctl sends it."""
    return bytes([0x85, 0x06, 0x2C] + [0] * 11 + [command, 0])


def power_from_sense(sense: bytes) -> str:
    """CHECK POWER MODE's answer from the returned registers:
    ``standby``, ``active`` or ``unknown``."""
    count = None
    if len(sense) >= 14 and sense[0] & 0x7F == 0x72 and sense[8] == 0x09:
        count = sense[8 + 5]  # descriptor format: ATA status return, count(7:0)
    elif len(sense) >= 7 and sense[0] & 0x7F == 0x70:
        count = sense[6]  # fixed format: information field carries count
    if count is None:
        return "unknown"
    return "standby" if count == 0x00 else "active"


def ata_command(dev: Path, command: int, timeout_ms: int = 20_000) -> bytes:
    """Send one non-data ATA command; returns the sense data. Needs
    CAP_SYS_RAWIO (root on the host, or a container with that capability)."""
    import fcntl  # Linux only; imported here so the module loads anywhere

    cdb = (ctypes.c_ubyte * 16)(*ata16_cdb(command))
    sense = (ctypes.c_ubyte * 32)()
    hdr = _SgIoHdr(interface_id=ord("S"), dxfer_direction=-1, cmd_len=16, mx_sb_len=32,
                   timeout=timeout_ms, cmdp=ctypes.cast(cdb, ctypes.c_void_p),
                   sbp=ctypes.cast(sense, ctypes.c_void_p))  # fmt: skip
    try:
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        raise DriveSleepError(f"can't open {dev}: {e.strerror}") from e
    try:
        fcntl.ioctl(fd, _SG_IO, hdr)
    except OSError as e:
        raise DriveSleepError(f"{dev}: {e.strerror}") from e
    finally:
        os.close(fd)
    data = bytes(sense[: hdr.sb_len_wr])
    if hdr.host_status or hdr.driver_status & 0x07:  # 0x08 = sense included: fine
        raise DriveSleepError(f"{dev}: command not delivered (host {hdr.host_status})")
    if data:
        key = (data[1] if data[0] & 0x7F == 0x72 else data[2]) & 0x0F
        if key not in (0x00, 0x01):  # 1 = "recovered": the registers we asked for
            raise DriveSleepError(f"{dev}: drive refused the command (sense {data[:4].hex()})")
    return data


def standby(dev: Path) -> None:
    ata_command(dev, ATA_STANDBY_IMMEDIATE)


def power_mode(dev: Path) -> str:
    try:
        return power_from_sense(ata_command(dev, ATA_CHECK_POWER_MODE))
    except DriveSleepError:
        return "unknown"


# ---------------------------------------------------------------- the idle timer


@dataclass
class _Seen:
    counters: tuple[int, int]
    since: float
    asleep: bool = False  # we sent standby and nothing has happened since


@dataclass
class Sleeper:
    after: float  # seconds of no reads or writes before a drive is put to sleep
    all_disks: bool = False  # also internal (non-USB) rotating disks
    now: Callable[[], float] = time.monotonic
    send: Callable[[Path], None] = standby
    list_disks: Callable[[], list[Disk]] = disks
    stats: Callable[[], dict[str, tuple[int, int, int]]] = read_activity
    seen: dict[str, _Seen] = field(default_factory=dict)

    def candidates(self) -> list[Disk]:
        return [d for d in self.list_disks() if d.rotational and (d.usb or self.all_disks)]

    def tick(self) -> list[str]:
        """One look at the counters; returns the drives put to sleep."""
        stats, t, slept = self.stats(), self.now(), []
        names = set()
        for d in self.candidates():
            names.add(d.name)
            if d.name not in stats:
                continue
            reads, writes, in_flight = stats[d.name]
            c = (reads, writes)
            s = self.seen.get(d.name)
            if s is None or c != s.counters or in_flight:
                if s is not None and s.asleep:
                    log.info("drive %s woke up (%s)", d.name, d.model)
                self.seen[d.name] = _Seen(c, t)
                continue
            if s.asleep or t - s.since < self.after:
                continue
            s.asleep = True  # once per idle spell, whether or not it works
            try:
                self.send(d.dev)
            except OSError as e:
                log.warning("couldn't put drive %s to sleep: %s", d.name, e)
                continue
            log.info("drive %s (%s) idle for %d min: sent to sleep", d.name, d.model,
                     (t - s.since) // 60)  # fmt: skip
            slept.append(d.name)
        for gone in set(self.seen) - names:  # unplugged
            del self.seen[gone]
        return slept

    def run(self, stop: threading.Event, every: float = CHECK_EVERY) -> None:
        while True:
            self.tick()
            if stop.wait(every):
                return
