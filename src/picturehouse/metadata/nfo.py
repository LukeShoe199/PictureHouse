"""Kodi-style .nfo files: <movie>, <tvshow>, <episodedetails>.

Local metadata wins over online lookups (the user or another tool wrote it on
purpose). The exception is an .nfo PictureHouse wrote itself (``ours``, see
metadata/localsave.py): that is a saved copy, so online data still wins except
for the fields the user edited. An .nfo may also be a bare URL ("https://www.themoviedb.org/movie/603"
or an IMDb link) — we take the id from it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from picturehouse.paths import long_path

_TMDB_URL = re.compile(r"themoviedb\.org/(movie|tv)/(\d+)")
_IMDB_ID = re.compile(r"\b(tt\d{7,9})\b")


@dataclass
class NfoData:
    kind: str  # movie | tvshow | episodedetails | url
    title: str | None = None
    year: int | None = None
    overview: str | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    genres: list[str] = field(default_factory=list)
    certification: str | None = None
    rating: float | None = None
    directors: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    season: int | None = None
    episode: int | None = None
    ours: bool = False  # written by PictureHouse (localsave), not by the user
    locked: list[str] = field(default_factory=list)  # fields the user had edited

    def for_online_match(self) -> NfoData:
        """What to lay over fresh online data: everything from a user's .nfo,
        only the user's own edits from one PictureHouse saved."""
        if not self.ours:
            return self
        keep = NfoData(kind=self.kind, tmdb_id=self.tmdb_id, imdb_id=self.imdb_id, ours=True,
                       locked=self.locked, season=self.season, episode=self.episode)  # fmt: skip
        for f in self.locked:
            if f == "genres":
                keep.genres = self.genres
            elif hasattr(keep, f):
                setattr(keep, f, getattr(self, f))
        return keep


def _text(root: ET.Element, tag: str) -> str | None:
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def _int(v: str | None) -> int | None:
    m = re.match(r"\s*(\d+)", v or "")
    return int(m.group(1)) if m else None


def parse_nfo(text: str) -> NfoData | None:
    text = text.lstrip("﻿")
    try:
        root = ET.fromstring(text[text.find("<") :]) if "<" in text else None
    except ET.ParseError:
        root = None
    if root is None or root.tag not in ("movie", "tvshow", "episodedetails"):
        # Not XML (or junk after it): look for a URL / id instead.
        tmdb = _TMDB_URL.search(text)
        imdb = _IMDB_ID.search(text)
        if not tmdb and not imdb:
            return None
        return NfoData(kind="url", tmdb_id=int(tmdb.group(2)) if tmdb else None,
                       imdb_id=imdb.group(1) if imdb else None)  # fmt: skip

    d = NfoData(kind=root.tag, title=_text(root, "title"), overview=_text(root, "plot"))
    d.year = _int(_text(root, "year")) or _int((_text(root, "premiered") or "")[:4])
    for uid in root.findall("uniqueid"):
        kind, val = (uid.get("type") or "").lower(), (uid.text or "").strip()
        if kind == "tmdb" and val.isdigit():
            d.tmdb_id = int(val)
        elif kind == "imdb" and val:
            d.imdb_id = val
    d.tmdb_id = d.tmdb_id or _int(_text(root, "tmdbid"))
    d.imdb_id = d.imdb_id or _text(root, "imdbid") or (
        _text(root, "id") if (_text(root, "id") or "").startswith("tt") else None)  # fmt: skip
    d.genres = [g.text.strip() for g in root.findall("genre") if g.text and g.text.strip()]
    mpaa = _text(root, "mpaa")
    d.certification = mpaa.split(":")[-1].strip() if mpaa else None
    rating = root.find("ratings/rating/value")
    rating_text = rating.text if rating is not None else _text(root, "rating")
    try:
        d.rating = float(rating_text) if rating_text else None
    except ValueError:
        d.rating = None
    d.directors = [x.text.strip() for x in root.findall("director") if x.text]
    d.cast = [n.text.strip() for n in root.findall("actor/name") if n.text][:15]
    d.season = _int(_text(root, "season"))
    d.episode = _int(_text(root, "episode"))
    if (ph := root.find("picturehouse")) is not None or "Written by PictureHouse" in text:
        d.ours = True
        if ph is not None:
            d.locked = [f for f in (ph.get("locked") or "").split(",") if f]
    return d


def _read(path: Path) -> NfoData | None:
    try:
        raw = long_path(path.absolute()).read_bytes()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return parse_nfo(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return None


def find_film_nfo(root: Path, rel_path: str) -> NfoData | None:
    """<stem>.nfo next to the file, else movie.nfo in the film's own folder."""
    rel = PurePosixPath(rel_path)
    file = root.joinpath(*rel.parts)
    for candidate in (file.with_suffix(".nfo"),
                      file.parent / "movie.nfo" if len(rel.parts) > 1 else None):  # fmt: skip
        if candidate is not None and (d := _read(candidate)) and d.kind in ("movie", "url"):
            return d
    return None


def find_show_nfo(root: Path, rel_path: str) -> NfoData | None:
    """tvshow.nfo in the show folder or any folder between it and the file."""
    rel = PurePosixPath(rel_path)
    folder = root.joinpath(*rel.parts[:-1])
    while True:
        if (d := _read(folder / "tvshow.nfo")) and d.kind in ("tvshow", "url"):
            return d
        if folder == root or root not in folder.parents:
            return None
        folder = folder.parent


def find_episode_nfo(root: Path, rel_path: str) -> NfoData | None:
    file = root.joinpath(*PurePosixPath(rel_path).parts)
    d = _read(file.with_suffix(".nfo"))
    return d if d and d.kind == "episodedetails" else None
