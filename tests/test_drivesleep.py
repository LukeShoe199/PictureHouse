"""Idle USB drive sleep (picturehouse/drivesleep.py): spotting idle drives
from the kernel's counters, and the ATA command bytes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from picturehouse import drivesleep
from picturehouse.drivesleep import Disk, Sleeper

DISKSTATS = (
    "   8       0 sda 9000 10 72000 100 500 20 4000 50 0 120 150 0 0 0 0\n"
    "   8      16 sdb 280964 0 2247712 9000 258 0 2064 30 0 5000 9030 0 0 0 0\n"
    "   8      17 sdb1 12 0 96 1 0 0 0 0 0 1 1 0 0 0 0\n"
)


def test_counters_from_diskstats():
    a = drivesleep.activity(DISKSTATS)
    assert a["sdb"] == (280964, 258, 0) and a["sda"] == (9000, 500, 0)


def test_command_bytes_match_what_smartctl_sends():
    # Seen on the wire from `smartctl -n standby` (CHECK POWER MODE, 0xE5).
    assert drivesleep.ata16_cdb(0xE5).hex(" ") == "85 06 2c 00 00 00 00 00 00 00 00 00 00 00 e5 00"
    assert drivesleep.ata16_cdb(0xE0)[14] == 0xE0  # STANDBY IMMEDIATE


def test_power_mode_from_returned_registers():
    desc = bytes([0x72, 0x01, 0x00, 0x1D, 0, 0, 0, 0x0E, 0x09, 0x0C, 0, 0, 0, 0x00] + [0] * 8)
    assert drivesleep.power_from_sense(desc) == "standby"
    awake = bytearray(desc)
    awake[13] = 0xFF
    assert drivesleep.power_from_sense(bytes(awake)) == "active"
    fixed = bytes([0x70, 0, 0x01, 0, 0x50, 0, 0x00] + [0] * 11)
    assert drivesleep.power_from_sense(fixed) == "standby"
    assert drivesleep.power_from_sense(b"") == "unknown"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux sysfs layout (symlinks)")
def test_disks_from_sysfs(tmp_path):
    block = tmp_path / "block"
    block.mkdir()
    usb_dev = tmp_path / "devices" / "pci0000:00" / "usb2" / "2-1" / "block" / "sdb"
    sata_dev = tmp_path / "devices" / "pci0000:00" / "ata1" / "block" / "sda"
    for d, model, rot in ((usb_dev, "ST3000DM008", "1"), (sata_dev, "Samsung SSD", "0")):
        (d / "device").mkdir(parents=True)
        (d / "queue").mkdir()
        (d / "device" / "model").write_text(model + "  \n")
        (d / "queue" / "rotational").write_text(rot)
        try:
            os.symlink(d, block / d.name, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not allowed here")
    found = {d.name: d for d in drivesleep.disks(block)}
    assert found["sdb"] == Disk("sdb", "ST3000DM008", usb=True, rotational=True)
    assert found["sda"].usb is False and found["sda"].rotational is False


class Box:
    """A fake machine: a clock, counters, drives, and what was sent."""

    def __init__(self):
        self.t = 0.0
        self.stats = {"sdb": (100, 5, 0), "sda": (50, 50, 0), "sdc": (1, 1, 0)}
        self.drives = [Disk("sdb", "USB HDD", True, True), Disk("sda", "Internal", False, True),
                       Disk("sdc", "USB SSD", True, False)]  # fmt: skip
        self.sent: list[Path] = []
        self.fail = False

    def send(self, dev: Path):
        self.sent.append(dev)
        if self.fail:
            raise OSError("not supported")

    def sleeper(self, **kw) -> Sleeper:
        return Sleeper(after=3600, now=lambda: self.t, send=self.send,
                       list_disks=lambda: self.drives, stats=lambda: dict(self.stats), **kw)  # fmt: skip


def test_idle_usb_hard_drive_sleeps_once_after_the_wait():
    box = Box()
    s = box.sleeper()
    s.tick()
    box.t = 3599
    assert s.tick() == []  # not yet
    box.t = 3600
    assert s.tick() == ["sdb"]  # internal disk and USB SSD left alone
    box.t = 9000
    assert s.tick() == [] and len(box.sent) == 1  # once per idle spell


def test_any_read_or_write_restarts_the_wait():
    box = Box()
    s = box.sleeper()
    s.tick()
    box.t = 3000
    box.stats["sdb"] = (101, 5, 0)  # someone played something
    s.tick()
    box.t = 6000
    assert s.tick() == []
    box.t = 6600
    assert s.tick() == ["sdb"]
    box.stats["sdb"] = (102, 5, 0)  # woke up for a film
    box.t = 7000
    s.tick()
    box.t = 10600
    assert s.tick() == ["sdb"] and len(box.sent) == 2  # sleeps again after the next spell


def test_busy_drive_is_not_put_to_sleep():
    box = Box()
    s = box.sleeper()
    box.stats["sdb"] = (100, 5, 1)  # a long read still in flight
    s.tick()
    box.t = 7200
    assert s.tick() == []


def test_failure_is_logged_not_retried_every_tick(caplog):
    box = Box()
    box.fail = True
    s = box.sleeper()
    s.tick()
    box.t = 3600
    assert s.tick() == [] and "couldn't put drive sdb to sleep" in caplog.text
    box.t = 3700
    s.tick()
    assert len(box.sent) == 1


def test_all_disks_and_unplugged_drives():
    box = Box()
    s = box.sleeper(all_disks=True)
    s.tick()
    box.t = 3600
    assert sorted(s.tick()) == ["sda", "sdb"]  # still never the SSD
    box.drives = box.drives[1:]  # sdb unplugged
    s.tick()
    assert "sdb" not in s.seen
