"""Folder watching: rescan a library shortly after files in it change.

Change events are only a trigger. The incremental scan is what decides what
actually changed, so a missed or duplicated event can never corrupt the
library. The periodic rescan in ``serve`` remains the backstop for shares
that don't deliver events.

Large copies produce a stream of events for minutes; we wait for a quiet
period (``SETTLE_MS``) before scanning, and a file still being copied when
we do scan simply fails its probe and is picked up again when it changes.

Idle drives must be able to sleep: nothing here reads the library folders
while nothing changes. Change events cost no disk access, and on Linux a
drive coming or going is noticed in the mount table (memory) rather than by
looking at the folders; only then, or after a change event, are they checked.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path, PurePath

import watchfiles
from sqlalchemy import select

from picturehouse.db import Database
from picturehouse.db.models import LibraryRootRow
from picturehouse.library import mounts
from picturehouse.library.naming import is_ignored_dir, is_media_file
from picturehouse.library.scanner import Scanner
from picturehouse.log import log_context, new_job_id

log = logging.getLogger(__name__)

SETTLE_MS = 5_000  # quiet time before a batch of changes is acted on
MAX_BATCH_MS = 60_000  # act at least this often during a long burst
RECHECK_MS = 20_000  # how often to look for drives that came or went (USB drives)


def _relevant(change: watchfiles.Change, path: str) -> bool:
    p = PurePath(path)
    if any(is_ignored_dir(part) for part in p.parts[:-1]):
        return False
    # Media files, plus suffix-less paths: a deleted or renamed folder.
    return is_media_file(p.name) or (not p.suffix and not p.name.startswith("."))


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


class FolderWatcher:
    def __init__(self, db: Database, scanner: Scanner) -> None:
        self.db = db
        self.scanner = scanner
        self._stop = threading.Event()
        self._reload = threading.Event()
        self._thread: threading.Thread | None = None
        self.watching: dict[str, Path] = {}
        # Watched folders on local disks: change events cover them fully, so
        # they need no periodic rescan (see covers()).
        self.local: set[str] = set()

    # ------------------------------------------------------------- control

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._reload.set()
        if self._thread:
            self._thread.join(timeout=10)

    def covers(self, name: str) -> bool:
        """True when this library's changes are reliably seen as they happen
        (a local disk being watched), so a periodic rescan would only wake
        the drive for nothing. Network shares: changes made by other
        machines raise no events here, so they still need rescanning."""
        return name in self.local and self._thread is not None and self._thread.is_alive()

    def reload(self) -> None:
        """Library folders changed: rebuild the watch list."""
        self._reload.set()

    # ------------------------------------------------------------- loop

    def _configured(self) -> dict[str, Path]:
        with self.db.session() as s:
            rows = list(s.scalars(select(LibraryRootRow).where(LibraryRootRow.configured)))
        return {row.name: Path(row.path) for row in rows}

    def _roots(self) -> dict[str, Path]:
        return {n: p for n, p in self._configured().items() if _is_dir(p)}

    def _run(self) -> None:
        previous: set[str] | None = None
        while not self._stop.is_set():
            self._reload.clear()
            roots = self._roots()
            self.watching = roots
            table = mounts.mount_table()
            self.local = {n for n, p in roots.items() if not mounts.is_network(p, table)}
            # A drive plugged in or out (or a share back online): scan those
            # folders now, so they show as available/offline straight away
            # rather than at the next periodic scan.
            if previous is not None:
                came, went = set(roots) - previous, previous - set(roots)
                if came or went:
                    log.info("library folders appeared: %s; disappeared: %s",
                             ", ".join(sorted(came)) or "none",
                             ", ".join(sorted(went)) or "none")  # fmt: skip
                    self._scan(sorted(came | went), 0)
            previous = set(roots)
            if not roots:
                self._reload.wait(RECHECK_MS / 1000)
                continue
            log.info("watching %d library folders", len(roots), extra={"roots": list(roots)})
            try:
                self._watch(roots)
            except Exception:
                # e.g. a share vanished mid-watch. Back off, then rebuild.
                log.exception("folder watcher failed; retrying in 30 s")
                self._stop.wait(30)

    def _watch(self, roots: dict[str, Path]) -> None:
        last_check = time.monotonic()
        table = mounts.mount_table()  # None off Linux: look at the folders instead
        changed = False  # since the last look at the folders
        for changes in watchfiles.watch(
            *roots.values(),
            watch_filter=_relevant,
            debounce=MAX_BATCH_MS,
            step=SETTLE_MS,
            stop_event=self._reload,
            rust_timeout=RECHECK_MS,
            yield_on_timeout=True,
            raise_interrupt=False,
            ignore_permission_denied=True,
        ):
            if self._stop.is_set():
                return
            if changes:
                changed = True
                names = self._roots_for(changes, roots)
                if names:
                    self._scan(names, len(changes))
            if time.monotonic() - last_check > RECHECK_MS / 1000:
                last_check = time.monotonic()
                if table is not None:
                    # Don't look at the watched folders unless a drive came or
                    # went, or something changed (a folder deleted outright
                    # raises an event): looking would wake sleeping drives.
                    # Missing folders are still looked for (one created later).
                    now = mounts.mount_table()
                    if now == table and not changed:
                        missing = [p for n, p in self._configured().items() if n not in roots]
                        if not any(_is_dir(p) for p in missing):
                            continue
                    table, changed = now, False
                if set(self._roots()) != set(roots):  # a root went offline or came back
                    log.info("library folder availability changed; rebuilding watch list")
                    return

    @staticmethod
    def _roots_for(
        changes: set[tuple[watchfiles.Change, str]], roots: dict[str, Path]
    ) -> list[str]:
        hit = set()
        for _change, path in changes:
            p = Path(path)
            for name, root in roots.items():
                if p == root or root in p.parents:
                    hit.add(name)
        return sorted(hit)

    def _scan(self, names: list[str], n_changes: int) -> None:
        with log_context(job_id=new_job_id("watch")):
            log.info("%d changes in %s; scanning", n_changes, ", ".join(names))
            # Another scan may be running; wait for it rather than drop the event.
            while not self._stop.is_set():
                if self.scanner.scan(names):
                    return
                if not self.scanner.running:
                    return  # scan ran but had nothing configured
                self._stop.wait(2)
