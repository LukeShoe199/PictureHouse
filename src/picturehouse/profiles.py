"""Profiles: who's watching, and what a child may see.

* Every device (by network address) uses one profile; devices never set
  use profile 1, the house default. The choice lives on the server, not in a
  cookie, so clearing a browser's data doesn't get a child out of their
  profile, and devices without cookies (the LG TV app) work the same way.
* A **child** profile sees only *kids libraries* (``kids = true`` on a
  ``[[libraries]]`` entry) plus titles approved one by one wherever they live
  (:func:`approve_item`, :func:`approve_show`; a series is approved whole,
  including episodes added later). Nothing else. That's enforced in the queries every list, search,
  item page, stream and picture goes through (``queries.in_scope``), so a
  child device can't reach anything else by address either. DLNA is not
  filtered: TVs' own media players can't pick a profile.
* Leaving a child profile for an adult one needs the **exit code** (one for
  the house, separate from the admin PIN). A device can only be put into a
  child profile once an exit code exists, so it can always be got out.
* Watch progress is kept per profile.
* Any profile can have libraries **hidden** from it (e.g. the kids' folders on
  the grown-ups' profile). That's tidiness, not a lock: an adult can unhide.

The web app sets :data:`SCOPE` for each request (``web/app.py``); code
running outside a request (DLNA, the CLI) sees everything as profile 1.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select

from picturehouse.db import Database
from picturehouse.db.models import (
    DeviceProfile,
    HiddenLibrary,
    KidsShow,
    LibraryRootRow,
    MediaItem,
    PlaybackState,
    Profile,
    utcnow,
)
from picturehouse.errors import PictureHouseError
from picturehouse.web.access import FAILURE_WINDOW, MAX_FAILURES, hash_pin, valid_pin, verify_pin

MAIN = 1  # the house default profile
CACHE_SECONDS = 5.0  # changes from the CLI reach a running server this quickly


@dataclass(frozen=True)
class Scope:
    profile_id: int = MAIN
    kids_only: bool = False
    hidden: frozenset[int] = frozenset()  # library_root ids this profile hides


EVERYTHING = Scope()  # outside a web request: DLNA, the CLI
SCOPE: ContextVar[Scope] = ContextVar("picturehouse_scope", default=EVERYTHING)


class ProfileError(PictureHouseError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ProfileInfo:
    id: int
    name: str
    child: bool

    def json(self) -> dict:
        return {"id": self.id, "name": self.name, "child": self.child}


@dataclass
class ProfileStore:
    db: Database
    data_dir: Path
    _devices: dict[str, int] | None = None  # address -> profile id (cache)
    _profiles: dict[int, ProfileInfo] | None = None
    _hidden: dict[int, frozenset[int]] | None = None  # profile id -> root ids
    _loaded_at: float = 0.0
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------ reading

    def profiles(self) -> list[ProfileInfo]:
        return sorted(self._load()[1].values(), key=lambda p: p.id)

    def get(self, profile_id: int) -> ProfileInfo:
        p = self._load()[1].get(profile_id)
        if p is None:
            raise ProfileError("no such profile", 404)
        return p

    def find(self, name_or_id: str) -> ProfileInfo:
        """By name (any case) or id, for the CLI."""
        for p in self.profiles():
            if p.name.casefold() == name_or_id.casefold() or str(p.id) == name_or_id:
                return p
        raise ProfileError(f"no profile called {name_or_id!r}", 404)

    def for_device(self, address: str) -> ProfileInfo:
        devices, profiles, _ = self._load()
        return profiles.get(devices.get(address, MAIN)) or profiles[MAIN]

    def scope_for(self, address: str) -> Scope:
        p = self.for_device(address)
        return Scope(profile_id=p.id, kids_only=p.child,
                     hidden=self._load()[2].get(p.id, frozenset()))  # fmt: skip

    def devices(self) -> dict[str, int]:
        return dict(self._load()[0])

    def _load(
        self,
    ) -> tuple[dict[str, int], dict[int, ProfileInfo], dict[int, frozenset[int]]]:
        with self._lock:
            stale = time.monotonic() - self._loaded_at > CACHE_SECONDS
            if self._devices is None or self._profiles is None or self._hidden is None or stale:
                self._loaded_at = time.monotonic()
                with self.db.session() as s:
                    self._profiles = {p.id: ProfileInfo(p.id, p.name, p.child)
                                      for p in s.scalars(select(Profile))}  # fmt: skip
                    self._devices = {
                        d.address: d.profile_id for d in s.scalars(select(DeviceProfile))
                    }
                    hidden: dict[int, set[int]] = {}
                    for h in s.scalars(select(HiddenLibrary)):
                        hidden.setdefault(h.profile_id, set()).add(h.root_id)
                    self._hidden = {k: frozenset(v) for k, v in hidden.items()}
            return self._devices, self._profiles, self._hidden

    def _changed(self) -> None:
        with self._lock:
            self._devices = self._profiles = self._hidden = None

    # ------------------------------------------------------------ managing

    def add(self, name: str, child: bool = False) -> ProfileInfo:
        name = _clean_name(name)
        self._unique(name)
        with self.db.session() as s:
            p = Profile(name=name, child=child)
            s.add(p)
            s.flush()
            info = ProfileInfo(p.id, p.name, p.child)
        self._changed()
        return info

    def update(self, profile_id: int, *, name: str | None = None,
               child: bool | None = None) -> ProfileInfo:  # fmt: skip
        self.get(profile_id)
        if profile_id == MAIN and child:
            raise ProfileError("the main profile can't be a child profile")
        if name is not None:
            name = _clean_name(name)
            self._unique(name, profile_id)
        with self.db.session() as s:
            p = s.get(Profile, profile_id)
            if name is not None:
                p.name = name
            if child is not None:
                if child and not self.code_set:
                    raise ProfileError("set an exit code first, so a child profile can be left")
                p.child = child
            info = ProfileInfo(p.id, p.name, p.child)
        self._changed()
        return info

    def remove(self, profile_id: int) -> None:
        """Its devices go back to the main profile; its watch progress goes."""
        self.get(profile_id)
        if profile_id == MAIN:
            raise ProfileError("the main profile can't be removed (rename it instead)")
        with self.db.session() as s:
            s.execute(delete(DeviceProfile).where(DeviceProfile.profile_id == profile_id))
            s.execute(delete(PlaybackState).where(PlaybackState.profile_id == profile_id))
            s.execute(delete(HiddenLibrary).where(HiddenLibrary.profile_id == profile_id))
            s.execute(delete(Profile).where(Profile.id == profile_id))
        self._changed()

    def _unique(self, name: str, except_id: int | None = None) -> None:
        for p in self.profiles():
            if p.name.casefold() == name.casefold() and p.id != except_id:
                raise ProfileError(f"there's already a profile called {p.name!r}", 409)

    # ------------------------------------------------------------ hidden libraries

    def hidden_from(self) -> dict[str, list[int]]:
        """Library name -> ids of the profiles it's hidden from."""
        with self.db.session() as s:
            rows = s.execute(select(LibraryRootRow.name, HiddenLibrary.profile_id)
                             .join(LibraryRootRow, LibraryRootRow.id == HiddenLibrary.root_id))  # fmt: skip
            out: dict[str, list[int]] = {}
            for name, pid in rows:
                out.setdefault(name, []).append(pid)
        return {k: sorted(v) for k, v in out.items()}

    def set_hidden(self, library: str, profile_ids: list[int]) -> list[int]:
        """Hide a library from exactly these profiles (the rest see it)."""
        wanted = {self.get(pid).id for pid in profile_ids}
        with self.db.session() as s:
            root = s.scalar(select(LibraryRootRow).where(
                func.lower(LibraryRootRow.name) == library.casefold(),
                LibraryRootRow.configured))  # fmt: skip
            if root is None:
                raise ProfileError(f"no library named {library!r}", 404)
            s.execute(delete(HiddenLibrary).where(HiddenLibrary.root_id == root.id))
            s.add_all(HiddenLibrary(profile_id=pid, root_id=root.id) for pid in sorted(wanted))
        self._changed()
        return sorted(wanted)

    # ------------------------------------------------------------ devices

    def switch(self, address: str, profile_id: int, code: str | None = None) -> ProfileInfo:
        """A device changes its own profile. Leaving a child profile for an
        adult one needs the exit code (rate-limited per device)."""
        target, current = self.get(profile_id), self.for_device(address)
        if current.child and not target.child:
            wait = self.locked_out(address)
            if wait:
                raise ProfileError(f"too many wrong codes; try again in {wait // 60 + 1} min", 429)
            if not self.check_code(code or ""):
                self.record_failure(address)
                raise ProfileError("wrong exit code", 403)
            self.clear_failures(address)
        if target.child and not self.code_set:
            raise ProfileError("set an exit code first, so the child profile can be left")
        self.assign(address, target.id)
        return target

    def assign(self, address: str, profile_id: int) -> None:
        """Set a device's profile with no checks (admin, CLI)."""
        target = self.get(profile_id)
        if target.child and not self.code_set:
            raise ProfileError("set an exit code first, so the child profile can be left")
        with self.db.session() as s:
            row = s.get(DeviceProfile, address)
            if profile_id == MAIN:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(DeviceProfile(address=address, profile_id=profile_id))
            else:
                row.profile_id, row.updated_at = profile_id, utcnow()
        self._changed()

    def progress_count(self) -> dict[int, int]:
        with self.db.session() as s:
            rows = s.execute(select(PlaybackState.profile_id, func.count())
                             .group_by(PlaybackState.profile_id))  # fmt: skip
            return dict(rows.all())

    # ------------------------------------------------------------ exit code

    @property
    def code_file(self) -> Path:
        return self.data_dir / "exit-code"

    @property
    def code_set(self) -> bool:
        try:
            return bool(self.code_file.read_text(encoding="ascii").strip())
        except OSError:
            return False

    def set_code(self, code: str | None) -> None:
        if code is None:
            if any(p.child for p in self.profiles()):
                raise ProfileError("there are child profiles: they need an exit code")
            self.code_file.unlink(missing_ok=True)
            return
        if not valid_pin(code):
            raise ProfileError("the exit code must be 4 to 12 digits")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.code_file.write_text(hash_pin(code), encoding="ascii")

    def check_code(self, code: str) -> bool:
        try:
            stored = self.code_file.read_text(encoding="ascii").strip()
        except OSError:
            return False
        return verify_pin(code, stored)

    def locked_out(self, client: str) -> int:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW]
            self._failures[client] = recent
            return 0 if len(recent) < MAX_FAILURES else int(FAILURE_WINDOW - (now - recent[0])) + 1

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(time.monotonic())

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 40 or any(ord(c) < 32 for c in name):
        raise ProfileError("a profile name is 1 to 40 characters")
    return name


# ---------------------------------------------------------------- approvals
# House-wide, like kids libraries: an approved title is visible to every
# child profile. Read by queries.in_scope on every request, so no cache.


def approve_item(db: Database, item_id: int, approved: bool = True) -> None:
    """A film (or any single item) outside the kids libraries. Episodes are
    approved through their series: :func:`approve_show`."""
    with db.session() as s:
        item = s.get(MediaItem, item_id)
        if item is None:
            raise ProfileError("no such item", 404)
        if item.kind == "episode":
            raise ProfileError("episodes are approved with their whole series")
        item.kids_ok = approved


def approve_show(db: Database, parsed_shows: list[str], approved: bool = True) -> None:
    """A whole series, by the folder names its episodes were found under, so
    episodes added later are approved too."""
    with db.session() as s:
        s.execute(delete(KidsShow).where(KidsShow.parsed_show.in_(parsed_shows)))
        if approved:
            s.add_all(KidsShow(parsed_show=p) for p in parsed_shows)


def approved_shows(db: Database) -> set[str]:
    with db.session() as s:
        return set(s.scalars(select(KidsShow.parsed_show)))


def approved_titles(db: Database) -> list[tuple[str, str, int | None]]:
    """(kind, title, item id or None) of everything approved, for listing."""
    with db.session() as s:
        films = s.execute(select(MediaItem.kind, MediaItem.title, MediaItem.year, MediaItem.id)
                          .where(MediaItem.kids_ok).order_by(MediaItem.sort_title))  # fmt: skip
        out = [(k, f"{t} ({y})" if y else t, i) for k, t, y, i in films]
        shows = s.execute(select(func.coalesce(MediaItem.show, KidsShow.parsed_show))
                          .select_from(KidsShow)
                          .outerjoin(MediaItem, MediaItem.parsed_show == KidsShow.parsed_show)
                          .distinct())  # fmt: skip
        out += sorted(("show", name, None) for (name,) in shows)
    return out


@dataclass(frozen=True)
class Title:
    """A film/item or a whole series, as the CLI finds them by name."""

    kind: str  # "show" or the item's kind (film, home_video...)
    label: str
    item_id: int | None = None
    parsed_shows: tuple[str, ...] = ()
    kids_library: bool = False
    approved: bool = False


def find_titles(db: Database, name: str) -> list[Title]:
    """Films/items and series whose title is ``name`` (any case; a film may
    also be given as "Title (Year)")."""
    want = " ".join(name.split()).casefold()
    out: list[Title] = []
    with db.session() as s:
        items = s.scalars(select(MediaItem).where(MediaItem.kind != "episode",
                                                  func.lower(MediaItem.title).contains(
                                                      want.split(" (")[0])))  # fmt: skip
        for i in items:
            label = f"{i.title} ({i.year})" if i.year else i.title
            if want in (i.title.casefold(), label.casefold()):
                out.append(Title(i.kind, label, item_id=i.id, kids_library=i.root.kids,
                                 approved=i.kids_ok))  # fmt: skip
        eps = s.scalars(select(MediaItem).where(MediaItem.kind == "episode",
                                                func.lower(MediaItem.show) == want))  # fmt: skip
        shows: dict[str, list[MediaItem]] = {}
        for e in eps:
            shows.setdefault(e.show, []).append(e)
        approved = set(s.scalars(select(KidsShow.parsed_show)))
        for show, episodes in shows.items():
            parsed = tuple(sorted({e.parsed_show for e in episodes if e.parsed_show}))
            out.append(Title("show", show, parsed_shows=parsed,
                             kids_library=all(e.root.kids for e in episodes),
                             approved=bool(parsed) and set(parsed) <= approved))  # fmt: skip
    return out
