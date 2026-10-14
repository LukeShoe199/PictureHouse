"""Library scanning: walk roots, probe new/changed files, record everything.

Robustness rules:

* every file is processed in isolation; a failure is stored on the file
  (reason, time, retry count) and the scan carries on;
* a root that can't be reached marks its items unavailable — nothing is
  deleted — and they come back on the next scan that can see it;
* incremental: a file whose size and mtime are unchanged isn't re-probed
  (failed files are retried up to ``MAX_RETRIES`` times, then left alone
  until they change).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select, update

from picturehouse.db import Database
from picturehouse.db.models import LibraryRootRow, MediaItem, MediaStream, utcnow
from picturehouse.library.naming import derive, is_ignored_dir, is_media_file, sort_title
from picturehouse.library.photos import PhotoError, PhotoInfo, album_of, is_photo, read_photo
from picturehouse.library.probe import ProbeError, ProbeResult, probe_file
from picturehouse.log import log_context, new_job_id
from picturehouse.paths import long_path

log = logging.getLogger(__name__)

MAX_RETRIES = 3
PROBE_WORKERS = 4


@dataclass
class FoundFile:
    rel_path: str
    size: int
    mtime_ns: int


@dataclass
class ScanResult:
    library: str
    available: bool = True
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    missing: int = 0
    walk_errors: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def touched(self) -> int:
        return self.new + self.changed + self.missing


@dataclass
class ScanProgress:
    """Live state for the console's progress bar. Read without locking; values
    are only ever replaced, never mutated in place."""

    running: bool = False
    library: str | None = None
    done: int = 0
    total: int = 0
    current: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    results: list[ScanResult] = field(default_factory=list)


def walk_root(root: Path, errors: list[str], photos: bool = False) -> Iterator[FoundFile]:
    """Yield media files under ``root`` (POSIX relative paths). Unreadable
    folders are noted in ``errors`` and skipped. ``photos``: pictures count as
    media too (only in photo libraries: elsewhere a .jpg is artwork)."""
    stack = [(long_path(root), "")]
    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except OSError as e:
            errors.append(f"{rel_dir or '.'}: {e.strerror or e}")
            continue
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=True):
                    if not is_ignored_dir(entry.name):
                        stack.append((Path(entry.path), rel))
                elif entry.is_file(follow_symlinks=True) and (
                    is_media_file(entry.name) or (photos and is_photo(entry.name))
                ):
                    st = entry.stat(follow_symlinks=True)
                    yield FoundFile(rel, st.st_size, st.st_mtime_ns)
            except OSError as e:
                errors.append(f"{rel}: {e.strerror or e}")


def _apply_photo(item: MediaItem, info: PhotoInfo | None) -> None:
    """A picture in a photo library (``info`` None: it couldn't be read)."""
    name = PurePosixPath(item.rel_path).stem
    item.kind, item.media_type = "photo", "image"
    item.title, item.sort_title = name, sort_title(name)
    item.parsed_title, item.parsed_year, item.parsed_show = name, None, None
    item.album = album_of(item.rel_path)
    item.match_status = "skipped"  # nothing to look up online
    taken = info.taken_at if info else None
    item.recorded_at = taken or datetime.fromtimestamp(item.mtime_ns / 1e9, UTC)
    item.year = item.recorded_at.year
    item.width, item.height = (info.width, info.height) if info else (None, None)
    item.container = info.format.lower() if info else None
    for f in ("show", "season", "episode", "episode_end", "artist", "album_artist", "track_no",
              "disc_no", "duration", "bitrate", "vcodec", "vprofile", "pix_fmt", "color_transfer",
              "fps", "acodec", "channels"):  # fmt: skip
        setattr(item, f, None)
    item.has_embedded_art = False
    item.streams = []


def _apply_probe(item: MediaItem, probe: ProbeResult | None, root_kind: str) -> None:
    n = derive(item.rel_path, root_kind, probe)
    item.kind = n.kind
    item.parsed_title, item.parsed_year, item.parsed_show = n.title, n.year, n.show
    # Once matched (nfo / TMDb / manual) the display fields belong to the
    # metadata layer; a rescan only refreshes what the path says.
    if (item.metadata_source or "file") == "file":
        locked = set(json.loads(item.locked or "[]"))
        if "title" not in locked:
            item.title, item.sort_title = n.title, sort_title(n.title)
        if "year" not in locked:
            item.year = n.year
        item.show = n.show
    item.season, item.episode, item.episode_end = n.season, n.episode, n.episode_end
    item.artist, item.album_artist, item.album = n.artist, n.album_artist, n.album
    item.track_no, item.disc_no, item.recorded_at = n.track_no, n.disc_no, n.recorded_at
    if probe is None:
        return
    v, a = probe.video, probe.audio
    item.media_type = probe.media_type
    item.container = probe.container
    item.duration = probe.duration
    item.bitrate = probe.bitrate
    item.vcodec = v.codec if v else None
    item.vprofile = v.profile if v else None
    item.width = v.width if v else None
    item.height = v.height if v else None
    item.pix_fmt = v.pix_fmt if v else None
    item.color_transfer = v.color_transfer if v else None
    item.fps = v.fps if v else None
    item.acodec = a.codec if a else None
    item.channels = a.channels if a else None
    item.has_embedded_art = probe.has_attached_pic
    item.streams = [
        MediaStream(
            index=s.index, type=s.type, codec=s.codec, profile=s.profile, language=s.language,
            title=s.title, channels=s.channels, width=s.width, height=s.height,
            pix_fmt=s.pix_fmt, is_default=s.is_default, is_forced=s.is_forced,
            attached_pic=s.attached_pic,
        )
        for s in probe.streams
    ]  # fmt: skip


class Scanner:
    def __init__(
        self,
        db: Database,
        ffprobe: Path,
        *,
        probe_timeout: float = 30.0,
        workers: int = PROBE_WORKERS,
    ) -> None:
        self.db = db
        self.ffprobe = ffprobe
        self.probe_timeout = probe_timeout
        self.workers = workers
        self.progress = ScanProgress()
        self._lock = threading.Lock()
        # Called after a scan that found new/changed/missing files (DLNA update id, matching).
        self.on_change: list[Callable[[], None]] = []

    @property
    def running(self) -> bool:
        return self.progress.running

    def scan(self, names: list[str] | None = None, *, full: bool = False) -> list[ScanResult]:
        """Scan the named roots (default: all configured). Blocking; one scan at
        a time — a second call while one is running returns ``[]``."""
        if not self._claim():
            return []
        return self._run_claimed(names, full)

    def start_in_background(self, names: list[str] | None = None, *, full: bool = False) -> bool:
        """Start a scan on its own thread; False if one is already running.

        The scan is marked running *before* this returns: anyone polling
        ``progress`` right after (the console, tests) must not see the previous
        scan's finished results and think this one is already done."""
        if not self._claim():
            return False
        threading.Thread(target=self._run_claimed, args=(names, full),
                         name="scan", daemon=True).start()  # fmt: skip
        return True

    def _claim(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.progress = ScanProgress(running=True, started_at=time.time())
        return True

    def _run_claimed(self, names: list[str] | None, full: bool) -> list[ScanResult]:
        try:
            with self.db.session() as s:
                q = select(LibraryRootRow).where(LibraryRootRow.configured)
                roots = [r for r in s.scalars(q) if names is None or r.name in names]
            results = []
            with log_context(job_id=new_job_id("scan")):
                for root in roots:
                    results.append(self._scan_root(root, full=full))
                    self.progress.results = list(results)
            if any(r.touched for r in results):
                for cb in self.on_change:
                    try:
                        cb()
                    except Exception:
                        log.exception("scan listener failed")
            return results
        finally:
            self.progress.running = False
            self.progress.current = None
            self.progress.finished_at = time.time()
            self._lock.release()

    def _scan_root(self, root: LibraryRootRow, *, full: bool) -> ScanResult:
        t0 = time.monotonic()
        res = ScanResult(library=root.name)
        self.progress.library, self.progress.done, self.progress.total = root.name, 0, 0
        root_path = Path(root.path)
        with log_context(library=root.name):
            log.info("scan started", extra={"path": root.path, "full": full})
            if not root_path.is_dir():
                return self._mark_unavailable(root, res, t0)

            photos = root.kind == "photos"
            found = {f.rel_path: f for f in walk_root(root_path, res.walk_errors, photos)}
            for err in res.walk_errors:
                log.warning("could not read %s", err)

            with self.db.session() as s:
                existing = {
                    i.rel_path: i
                    for i in s.scalars(select(MediaItem).where(MediaItem.root_id == root.id))
                }
                todo: list[tuple[FoundFile, bool]] = []  # (file, is_new)
                for rel, f in found.items():
                    item = existing.get(rel)
                    if item is None:
                        todo.append((f, True))
                    elif (
                        full
                        or item.size != f.size
                        or item.mtime_ns != f.mtime_ns
                        or not item.probe_ok
                        and item.retry_count < MAX_RETRIES
                    ):
                        todo.append((f, False))
                    else:
                        res.unchanged += 1
                        item.available = True
                missing = [i for rel, i in existing.items() if rel not in found and i.available]
                for i in missing:
                    i.available = False
                res.missing = len(missing)
                lr = s.get(LibraryRootRow, root.id)
                lr.available, lr.unavailable_since = True, None

            self.progress.total = len(todo)
            with ThreadPoolExecutor(self.workers, thread_name_prefix="probe") as pool:
                futures = [
                    (f, is_new, pool.submit(self._probe, root_path / f.rel_path))
                    for f, is_new in todo
                ]
                for f, is_new, fut in futures:
                    self.progress.current = f.rel_path
                    probe, error = fut.result()
                    self._store(root, f, probe, error)
                    if error:
                        res.failed += 1
                    elif is_new:
                        res.new += 1
                    else:
                        res.changed += 1
                    self.progress.done += 1

            with self.db.session() as s:
                s.get(LibraryRootRow, root.id).last_scan_at = utcnow()
            res.seconds = round(time.monotonic() - t0, 2)
            log.info(
                "scan finished: %d new, %d changed, %d unchanged, %d failed, %d missing",
                res.new, res.changed, res.unchanged, res.failed, res.missing,
                extra={"seconds": res.seconds},
            )  # fmt: skip
            return res

    def _mark_unavailable(self, root: LibraryRootRow, res: ScanResult, t0: float) -> ScanResult:
        res.available = False
        with self.db.session() as s:
            lr = s.get(LibraryRootRow, root.id)
            if lr.available:
                lr.available, lr.unavailable_since = False, utcnow()
            r = s.execute(
                update(MediaItem)
                .where(MediaItem.root_id == root.id, MediaItem.available)
                .values(available=False)
            )
            res.missing = r.rowcount or 0
        log.warning("library folder not reachable; items marked unavailable", extra={
            "path": root.path, "items": res.missing})  # fmt: skip
        res.seconds = round(time.monotonic() - t0, 2)
        return res

    def _probe(self, path: Path) -> tuple[ProbeResult | PhotoInfo | None, str | None]:
        try:
            if is_photo(path.name):  # only walked in photo libraries
                return read_photo(path), None
            return probe_file(self.ffprobe, path, timeout=self.probe_timeout), None
        except (ProbeError, PhotoError) as e:
            return None, str(e)
        except Exception as e:  # never let one file take the scan down
            log.exception("unexpected error probing %s", path)
            return None, f"unexpected error: {e}"

    def _store(
        self, root: LibraryRootRow, f: FoundFile, probe: ProbeResult | PhotoInfo | None,
        error: str | None,
    ) -> None:  # fmt: skip
        try:
            with self.db.session() as s:
                item = s.scalar(
                    select(MediaItem).where(
                        MediaItem.root_id == root.id, MediaItem.rel_path == f.rel_path
                    )
                )
                if item is None:
                    item = MediaItem(root_id=root.id, rel_path=f.rel_path, retry_count=0)
                    s.add(item)
                changed = item.size != f.size or item.mtime_ns != f.mtime_ns
                item.size, item.mtime_ns, item.available = f.size, f.mtime_ns, True
                if error:
                    item.probe_ok = False
                    item.probe_error = error
                    item.probe_failed_at = utcnow()
                    item.retry_count = 1 if changed else (item.retry_count or 0) + 1
                    item.media_type = "unknown"
                    if root.kind == "photos" and is_photo(f.rel_path):
                        _apply_photo(item, None)
                        item.media_type = "unknown"
                    else:
                        _apply_probe(item, None, root.kind)
                    log.warning("probe failed: %s", error, extra={"file": f.rel_path})
                else:
                    item.probe_ok, item.probe_error = True, None
                    item.probe_failed_at, item.retry_count = None, 0
                    if isinstance(probe, PhotoInfo):
                        _apply_photo(item, probe)
                    else:
                        _apply_probe(item, probe, root.kind)
        except Exception:
            log.exception("could not store scan result", extra={"file": f.rel_path})
