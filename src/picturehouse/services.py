"""Wiring: one place that turns config + data dir into ready-to-use services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from picturehouse import ffmpeg
from picturehouse.config import Settings, load_config
from picturehouse.db import Database
from picturehouse.library.audiocheck import AudioChecker
from picturehouse.library.roots import sync_roots
from picturehouse.library.scanner import Scanner
from picturehouse.library.watcher import FolderWatcher
from picturehouse.metadata.localsave import LocalSaver
from picturehouse.metadata.matcher import Matcher
from picturehouse.metadata.omdb import OMDb
from picturehouse.metadata.tmdb import TMDb
from picturehouse.paths import DataLayout


@dataclass
class Services:
    settings: Settings
    layout: DataLayout
    config_file: Path
    db: Database
    ffmpeg: ffmpeg.ToolInfo
    ffprobe: ffmpeg.ToolInfo
    scanner: Scanner
    matcher: Matcher
    audio: AudioChecker
    watcher: FolderWatcher | None = None
    saver: LocalSaver | None = None
    # Background saves need a long-running process (`serve`); one-shot CLI
    # commands save before they exit instead.
    background_saves: bool = False
    # Why PICTUREHOUSE_ADMIN_PIN gave no PIN ("blank" / "invalid"), for the console.
    pin_problem: str | None = None

    def metadata_changed(self) -> None:
        """After matches/fixes/edits: refresh the local copies, if switched on."""
        if self.saver is not None and self.background_saves and self.settings.metadata.save_local:
            self.saver.sync_soon()

    def reload_settings(self) -> Settings:
        self.settings = load_config(self.config_file)
        sync_roots(self.db, self.settings)
        self.matcher.tmdb = make_tmdb(self.settings, self.layout)
        self.matcher.omdb = make_omdb(self.settings, self.layout)
        if self.watcher:
            self.watcher.reload()
        return self.settings

    def close(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.db.dispose()


def open_services(settings: Settings, layout: DataLayout, config_file: Path) -> Services:
    """Locate FFmpeg, open + migrate the database, sync library roots."""
    ff = ffmpeg.identify("ffmpeg", settings.ffmpeg.ffmpeg_path)
    fp = ffmpeg.identify("ffprobe", settings.ffmpeg.ffprobe_path)
    layout.ensure()
    db = Database(layout.db_file, layout.backup_dir)
    db.migrate()
    sync_roots(db, settings)
    scanner = Scanner(db, fp.path, probe_timeout=settings.ffmpeg.probe_timeout_s)
    matcher = Matcher(
        db,
        make_tmdb(settings, layout),
        make_omdb(settings, layout),
        state_file=layout.cache_dir / "matcher-version",
    )
    audio = AudioChecker(db, ff.path)
    svc = Services(settings, layout, config_file, db, ff, fp, scanner, matcher, audio)
    svc.saver = LocalSaver(db, layout.artwork_dir, layout.root / "local-metadata.json")
    matcher.on_change.append(svc.metadata_changed)
    return svc


def make_omdb(settings: Settings, layout: DataLayout) -> OMDb | None:
    key = settings.metadata.omdb_api_key
    return OMDb(key, layout.cache_dir / "omdb") if key else None


def make_tmdb(settings: Settings, layout: DataLayout) -> TMDb | None:
    m = settings.metadata
    if not m.tmdb_api_key:
        return None
    return TMDb(m.tmdb_api_key, layout.cache_dir / "tmdb", language=m.language, region=m.region)
