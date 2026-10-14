"""Folder watching: rescan a library shortly after files in it change.

Change events are only a trigger. The incremental scan is what decides what
actually changed, so a missed or duplicated event can never corrupt the
library. The periodic rescan in ``serve`` remains the backstop for shares
that don't deliver events.

Large copies produce a stream of events for minutes; we wait for a quiet
period (``SETTLE_MS``) before scanning, and a file still being copied when
we do scan simply fails its probe and is picked up again when it changes.
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
from picturehouse.library.naming import is_ignored_dir, is_media_file
from picturehouse.library.scanner import Scanner
from picturehouse.log import log_context, new_job_id

log = logging.getLogger(__name__)

SETTLE_MS = 5_000  # quiet time before a batch of changes is acted on
MAX_BATCH_MS = 60_000  # act at least this often during a long burst
RECHECK_MS = 60_000  # how often to re-check which roots exist (offline ones may return)


def _relevant(change: watchfiles.Change, path: str) -> bool:
    p = PurePath(path)
    if any(is_ignored_dir(part) for part in p.parts[:-1]):
        return False
    # Media files, plus suffix-less paths: a deleted or renamed folder.
    return is_media_file(p.name) or (not p.suffix and not p.name.startswith("."))


class FolderWatcher:
    def __init__(self, db: Database, scanner: Scanner) -> None:
        self.db = db
        self.scanner = scanner
        self._stop = threading.Event()
        self._reload = threading.Event()
        self._thread: threading.Thread | None = None
        self.watching: dict[str, Path] = {}

    # ------------------------------------------------------------- control

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._reload.set()
        if self._thread:
            self._thread.join(timeout=10)

    def reload(self) -> None:
        """Library folders changed: rebuild the watch list."""
        self._reload.set()

    # ------------------------------------------------------------- loop

    def _roots(self) -> dict[str, Path]:
        with self.db.session() as s:
            rows = list(s.scalars(select(LibraryRootRow).where(LibraryRootRow.configured)))
        out = {}
        for row in rows:
            p = Path(row.path)
            try:
                if p.is_dir():
                    out[row.name] = p
            except OSError:
                pass
        return out

    def _run(self) -> None:
        while not self._stop.is_set():
            self._reload.clear()
            roots = self._roots()
            self.watching = roots
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
                names = self._roots_for(changes, roots)
                if names:
                    self._scan(names, len(changes))
            if time.monotonic() - last_check > RECHECK_MS / 1000:
                last_check = time.monotonic()
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
