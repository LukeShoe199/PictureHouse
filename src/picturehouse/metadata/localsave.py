"""Save matched metadata next to the media (opt-in: ``[metadata] save_local``).

The one exception to "media folders are never written to", and only when the
user turns it on. What is written uses the Kodi/Jellyfin names PictureHouse
already reads first, so a reinstall, a lost database or no internet still
gives every film and series its titles, plots and artwork:

* film:    ``<name>.nfo``, ``<name>-poster.jpg``, ``<name>-fanart.jpg``
* series:  ``tvshow.nfo``, ``poster.jpg``, ``fanart.jpg`` in the show folder
* episode: ``<name>.nfo``, ``<name>-thumb.jpg``

Never someone else's file: an .nfo is only replaced while it still carries
our marker comment, an image only while it is byte-for-byte what we wrote
(hashes kept in ``local-metadata.json`` in the data dir). Unchanged files are
not rewritten. A folder we can't write to (read-only share) is skipped and
logged, never an error.

Our .nfo files hold the TMDb/IMDb ids, the details, and which fields the
user edited (``<picturehouse locked="..."/>``). When TMDb is reachable it
still wins for everything but those edits (see ``nfo.NfoData.ours``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

from sqlalchemy import select

from picturehouse.db import Database
from picturehouse.db.models import LibraryRootRow, MediaItem, ShowMeta
from picturehouse.library.naming import _SPECIALS_DIR, _is_season_dir
from picturehouse.paths import long_path, resolve_in_root
from picturehouse.streaming.artwork import (
    BACKDROP_NAMES,
    POSTER_NAMES,
    _first_existing,
    _remote,
    _sidecar,
)

log = logging.getLogger(__name__)

MARKER = "<!-- Written by PictureHouse. It updates this file while this line is here; delete the line to keep your own version. -->"  # noqa: E501


@dataclass
class SaveSummary:
    written: int = 0
    unchanged: int = 0
    skipped_foreign: int = 0  # a file that isn't ours is in the way
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _people(raw: str | None) -> tuple[list[str], list[str]]:
    try:
        p = json.loads(raw or "{}")
    except ValueError:
        return [], []
    return list(p.get("director") or []), list(p.get("cast") or [])


def _locked(obj) -> list[str]:
    try:
        return [f for f in json.loads(obj.locked or "[]") if f != "match"]
    except ValueError:
        return []


def nfo_xml(kind: str, obj, *, season: int | None = None, episode: int | None = None) -> str:
    """Kodi-style XML for a MediaItem (movie / episodedetails) or ShowMeta (tvshow)."""

    def el(tag: str, value) -> str:
        return f"  <{tag}>{escape(str(value))}</{tag}>\n" if value not in (None, "") else ""

    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n', MARKER, "\n",
           f"<{kind}>\n", el("title", obj.title)]  # fmt: skip
    if kind == "episodedetails":
        out += [el("season", season), el("episode", episode), el("plot", obj.overview)]
    else:
        out += [el("year", obj.year), el("plot", obj.overview)]
        for g in (obj.genres or "").split(","):
            out.append(el("genre", g.strip()))
        if obj.certification:
            out.append(el("mpaa", obj.certification))
        if obj.rating is not None:
            out.append(f'  <ratings>\n    <rating name="default" default="true">\n'
                       f"      <value>{obj.rating:g}</value>\n    </rating>\n  </ratings>\n")  # fmt: skip
        directors, cast = _people(obj.people)
        out += [el("director", d) for d in directors]
        out += [f"  <actor>\n    <name>{escape(a)}</name>\n  </actor>\n" for a in cast]
        if obj.tmdb_id:
            out.append(f'  <uniqueid type="tmdb" default="true">{obj.tmdb_id}</uniqueid>\n')
        if obj.imdb_id:
            out.append(f'  <uniqueid type="imdb">{escape(obj.imdb_id)}</uniqueid>\n')
    if locked := _locked(obj):
        out.append(f'  <picturehouse locked="{escape(",".join(locked))}"/>\n')
    out.append(f"</{kind}>\n")
    return "".join(out)


def show_folder(root: Path, rel_path: str) -> Path | None:
    """The series' own folder: above "Season N" / "Specials", else the
    episode's folder. None for episodes loose in the library root."""
    rel = PurePosixPath(rel_path)
    parts = rel.parts[:-1]
    if parts and (_is_season_dir(parts[-1]) is not None or _SPECIALS_DIR.match(parts[-1])):
        parts = parts[:-1]
    return root.joinpath(*parts) if parts else None


def _foreign_nfo(path: Path) -> bool:
    try:
        raw = long_path(path.absolute()).read_bytes()
    except OSError:
        return False
    return MARKER.encode() not in raw


def _sidecar_safe(item: MediaItem, root: Path, which: str) -> Path | None:
    try:
        return _sidecar(item, root, which)
    except (OSError, ValueError):
        return None


def _users_art(found: Path | None, target: Path) -> bool:
    """An image the reader would use that isn't the file we'd write (whose
    ownership _write checks itself): the user's poster.jpg / folder.jpg."""
    return found is not None and found.name.lower() != target.name.lower()


class LocalSaver:
    def __init__(self, db: Database, cache_dir: Path, manifest: Path) -> None:
        self.db = db
        self.cache_dir = cache_dir  # where online artwork is already downloaded
        self.manifest_file = manifest
        self._lock = threading.Lock()
        self._pending = threading.Event()
        self.running = False

    # ---------------------------------------------------------------- manifest

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, m: dict[str, str]) -> None:
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.manifest_file)

    # ---------------------------------------------------------------- writing

    def _write(self, path: Path, data: bytes, *, is_nfo: bool, m: dict, sm: SaveSummary) -> None:
        lp = long_path(path.absolute())
        key = str(path)
        new_hash = hashlib.sha1(data).hexdigest()
        try:
            if lp.exists():
                old = lp.read_bytes()
                if old == data:
                    m[key] = new_hash
                    sm.unchanged += 1
                    return
                ours = (MARKER.encode() in old) if is_nfo else (
                    m.get(key) == hashlib.sha1(old).hexdigest())  # fmt: skip
                if not ours:
                    sm.skipped_foreign += 1
                    return
            tmp = lp.with_name(lp.name + ".picturehouse-tmp")
            tmp.write_bytes(data)
            tmp.replace(lp)
            m[key] = new_hash
            sm.written += 1
        except OSError as e:
            sm.failed += 1
            if len(sm.errors) < 20:
                sm.errors.append(f"{path}: {e.strerror or e}")

    def _image(self, url: str | None) -> bytes | None:
        try:
            p = _remote(url, self.cache_dir)
            return p.read_bytes() if p else None
        except OSError:
            return None

    def _save_film(self, item: MediaItem, root: Path, m: dict, sm: SaveSummary) -> None:
        file = resolve_in_root(root, item.rel_path)
        stem = file.with_suffix("")
        # The user's own movie.nfo / poster.jpg must stay in charge: ours would
        # be read first and hide them.
        own_folder = len(PurePosixPath(item.rel_path).parts) > 1
        movie_nfo = file.parent / "movie.nfo"
        if own_folder and _foreign_nfo(movie_nfo):
            sm.skipped_foreign += 1
        else:
            self._write(file.with_suffix(".nfo"), nfo_xml("movie", item).encode(), is_nfo=True,
                        m=m, sm=sm)  # fmt: skip
        for which, url, suffix in (("poster", item.poster_url, "-poster.jpg"),
                                   ("backdrop", item.backdrop_url, "-fanart.jpg")):  # fmt: skip
            target = stem.with_name(stem.name + suffix)
            if _users_art(_sidecar_safe(item, root, which), target):
                sm.skipped_foreign += 1
            elif img := self._image(url):
                self._write(target, img, is_nfo=False, m=m, sm=sm)

    def _save_show(self, meta: ShowMeta, eps: list[MediaItem], root: Path, m: dict,
                   sm: SaveSummary) -> None:  # fmt: skip
        folder = show_folder(root, eps[0].rel_path)
        if folder is not None:
            self._write(folder / "tvshow.nfo", nfo_xml("tvshow", meta).encode(), is_nfo=True,
                        m=m, sm=sm)  # fmt: skip
            for url, name, names in ((meta.poster_url, "poster.jpg", POSTER_NAMES),
                                     (meta.backdrop_url, "fanart.jpg", BACKDROP_NAMES)):  # fmt: skip
                if _users_art(_first_existing(folder, names), folder / name):
                    sm.skipped_foreign += 1  # e.g. the user's folder.jpg
                elif img := self._image(url):
                    self._write(folder / name, img, is_nfo=False, m=m, sm=sm)
        for e in eps:
            if e.match_status != "matched" or not e.title:
                continue
            file = resolve_in_root(root, e.rel_path)
            xml = nfo_xml("episodedetails", e, season=e.season, episode=e.episode)
            self._write(file.with_suffix(".nfo"), xml.encode(), is_nfo=True, m=m, sm=sm)
            if img := self._image(e.poster_url):
                stem = file.with_suffix("")
                self._write(stem.with_name(stem.name + "-thumb.jpg"), img, is_nfo=False, m=m,
                            sm=sm)  # fmt: skip

    def sync(self, libraries: list[str] | None = None) -> SaveSummary:
        """Write (or refresh) local files for every matched film and series."""
        sm = SaveSummary()
        with self._lock:
            self.running = True
            try:
                m = self._load()
                with self.db.session() as s:
                    base = (select(MediaItem).join(LibraryRootRow)
                            .where(MediaItem.available, LibraryRootRow.available,
                                   LibraryRootRow.configured))  # fmt: skip
                    if libraries:
                        base = base.where(LibraryRootRow.name.in_(libraries))
                    for item in s.scalars(base.where(MediaItem.kind == "film",
                                                     MediaItem.match_status == "matched")):  # fmt: skip
                        self._save_film(item, Path(item.root.path), m, sm)
                    by_show: dict[str, list[MediaItem]] = {}
                    for e in s.scalars(base.where(MediaItem.kind == "episode",
                                                  MediaItem.parsed_show.is_not(None))):  # fmt: skip
                        by_show.setdefault(e.parsed_show, []).append(e)
                    for show, eps in by_show.items():
                        meta = s.get(ShowMeta, show)
                        if meta is None or meta.match_status != "matched":
                            continue
                        # One series can span several library folders; save per folder.
                        roots: dict[int, list[MediaItem]] = {}
                        for e in eps:
                            roots.setdefault(e.root_id, []).append(e)
                        for group in roots.values():
                            self._save_show(meta, group, Path(group[0].root.path), m, sm)
                self._save(m)
            finally:
                self.running = False
        log.info("local metadata saved", extra={"written": sm.written, "unchanged": sm.unchanged,
                 "not_ours": sm.skipped_foreign, "failed": sm.failed})  # fmt: skip
        for e in sm.errors[:5]:
            log.warning("could not save local metadata: %s", e)
        return sm

    def sync_soon(self) -> None:
        """Background sync; calls while one runs are folded into one more pass."""
        if self._pending.is_set():
            return
        self._pending.set()

        def run() -> None:
            with self._lock:  # wait for a running pass
                self._pending.clear()
            try:
                self.sync()
            except Exception:
                log.exception("saving local metadata failed")

        threading.Thread(target=run, name="local-metadata", daemon=True).start()
