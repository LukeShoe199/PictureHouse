"""Configuration: a TOML file validated into typed settings.

Unknown keys are rejected (``extra="forbid"``) so a typo in the file is an
error at startup rather than a setting that silently does nothing.
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from picturehouse.errors import ConfigError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LibraryKind = Literal["films", "tv", "music", "home_videos", "photos", "mixed"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _empty_to_none(value: object) -> object:
    # TOML has no null; the template uses "" to mean "not set".
    return None if value == "" else value


class ServerConfig(_Section):
    friendly_name: str = Field("PictureHouse", min_length=1, max_length=64)
    host: str = "0.0.0.0"
    http_port: int = Field(9595, ge=1, le=65535)
    # HTTPS for phones: needed for casting and installing the app (0 = off).
    https_port: int = Field(9596, ge=0, le=65535)


class MetadataConfig(_Section):
    # Free key from https://www.themoviedb.org/settings/api (v3 key or v4 read token).
    tmdb_api_key: str | None = None
    # Fallback: free key from https://www.omdbapi.com/apikey.aspx (~1,000 requests/day).
    omdb_api_key: str | None = None
    language: str = Field("en-GB", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    region: str = Field("GB", pattern=r"^[A-Z]{2}$")  # for age certificates
    match_on_scan: bool = True  # match new items in the background after scans
    # Write .nfo files and artwork next to the media after matching (opt-in:
    # the only time PictureHouse writes to media folders). See localsave.py.
    save_local: bool = False

    _blank = field_validator("tmdb_api_key", "omdb_api_key", mode="before")(_empty_to_none)


class FFmpegConfig(_Section):
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    probe_timeout_s: float = Field(30.0, gt=0, le=600)
    # Video conversions at once (each uses several CPU cores); 0 = no limit.
    max_transcodes: int = Field(2, ge=0, le=16)

    _blank = field_validator("ffmpeg_path", "ffprobe_path", mode="before")(_empty_to_none)


class DLNAConfig(_Section):
    enabled: bool = True
    # Videos above this bitrate are converted for TVs instead of sent as-is,
    # for TVs on Wi-Fi that can't keep up with big files. 0 = always send
    # originals when the TV can play them.
    max_bitrate_mbps: float = Field(0, ge=0, le=200)


class ScanConfig(_Section):
    on_startup: bool = True  # incremental scan when the server starts
    interval_minutes: int = Field(360, ge=0)  # periodic incremental rescan; 0 = off
    watch: bool = True  # rescan a library shortly after files in it change
    audio_check: bool = True  # flag films/episodes whose audio looks broken


class UIConfig(_Section):
    # Theme every device uses unless it picks its own (themes/, docs/themes.md).
    theme: str = Field("picturehouse", pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")


class LoggingConfig(_Section):
    level: LogLevel = "INFO"
    console_level: LogLevel = "INFO"
    max_bytes: int = Field(10_000_000, ge=10_000)
    backup_count: int = Field(5, ge=0, le=100)

    @field_validator("level", "console_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v


# Any printable name except path separators: it appears in URLs and the UI.
RootName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f/\\]*[^\s\x00-\x1f\x7f/\\]$")
]


class LibraryRoot(_Section):
    name: RootName
    path: Path
    kind: LibraryKind = "mixed"

    @field_validator("path")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError(f"library path must be absolute, got {str(v)!r}")
        return v


class Settings(_Section):
    server: ServerConfig = ServerConfig()
    ffmpeg: FFmpegConfig = FFmpegConfig()
    logging: LoggingConfig = LoggingConfig()
    dlna: DLNAConfig = DLNAConfig()
    metadata: MetadataConfig = MetadataConfig()
    scan: ScanConfig = ScanConfig()
    ui: UIConfig = UIConfig()
    libraries: list[LibraryRoot] = []

    @field_validator("libraries")
    @classmethod
    def _unique_names(cls, roots: list[LibraryRoot]) -> list[LibraryRoot]:
        seen: set[str] = set()
        for r in roots:
            key = r.name.casefold()
            if key in seen:
                raise ValueError(f"duplicate library name {r.name!r} (names are case-insensitive)")
            seen.add(key)
        return roots

    def library(self, name: str) -> LibraryRoot | None:
        key = name.casefold()
        return next((r for r in self.libraries if r.name.casefold() == key), None)


def template_text() -> str:
    """The commented default config written by ``picturehouse config init``."""
    return files("picturehouse").joinpath("config_template.toml").read_text(encoding="utf-8")


def parse_config(text: str, *, source: str = "<string>") -> Settings:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(
            f"{source}: not valid TOML: {e}",
            hint="Windows paths need single quotes, e.g. path = 'D:\\Media\\Films'",
        ) from e
    try:
        return Settings.model_validate(raw)
    except ValidationError as e:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in e.errors()
        )
        raise ConfigError(f"{source}: invalid configuration:\n{problems}") from e


def load_config(path: Path) -> Settings:
    """Load settings from ``path``. A missing file means all defaults."""
    if not path.exists():
        return Settings()
    try:
        text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM from Notepad
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    return parse_config(text, source=str(path))
