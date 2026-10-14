"""Helpers to stand up real services over the fixture library."""

from __future__ import annotations

from pathlib import Path

from picturehouse.config import parse_config
from picturehouse.paths import DataLayout
from picturehouse.services import Services, open_services

LIBS = [("Films", "Films", "films"), ("TV", "TV", "tv"), ("Music", "Music", "music"),
        ("Home", "Home Videos", "home_videos")]  # fmt: skip


def make_services(media_library: Path, data_dir: Path, *, extra_toml: str = "") -> Services:
    data_dir.mkdir(parents=True, exist_ok=True)
    toml = (
        "".join(
            f"[[libraries]]\nname = '{n}'\npath = '{media_library / d}'\nkind = '{k}'\n\n"
            for n, d, k in LIBS
        )
        + extra_toml
    )
    cfg = data_dir / "config.toml"
    cfg.write_text(toml, encoding="utf-8")
    return open_services(parse_config(toml), DataLayout(data_dir), cfg)


# TestClient settings that make requests look like they come from the PC
# running PictureHouse (loopback address + loopback Host), i.e. an admin.
LOCAL = {"client": ("127.0.0.1", 50000), "base_url": "http://127.0.0.1:9595"}
# ...and from another device on the LAN.
LAN = {"client": ("192.168.1.21", 50000), "base_url": "http://192.168.1.2:9595"}
