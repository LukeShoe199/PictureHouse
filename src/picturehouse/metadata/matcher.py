"""Matching library items to metadata.

Precedence, highest first:

1. **Manual** — fields the user edited are listed in ``locked`` and never
   overwritten; a manual match ("match" in ``locked``) is never re-searched.
2. **.nfo** — local files the user (or another tool) wrote on purpose. An id
   in the .nfo picks the TMDb record; the .nfo's own fields then win over it.
3. **TMDb** — searched by the parsed title/year; only a clear winner is
   applied automatically. Close calls are stored as ``ambiguous`` with their
   candidates for the user to pick (``picturehouse fix`` / the console).
   **OMDb** is the fallback: used when no TMDb key is set, or when TMDb has
   no candidates at all for a title. Its daily quota running out pauses OMDb
   lookups for the rest of the run (items stay pending, nothing is lost).
4. **File name** — what the scanner parsed; used when nothing else applies.

Statuses: pending (not tried yet) · matched · ambiguous · unmatched · skipped
(kinds that aren't matched online: home videos, music).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from picturehouse.db import Database
from picturehouse.db.models import MediaItem, ShowMeta, utcnow
from picturehouse.library.naming import derive, sort_title
from picturehouse.metadata import nfo as nfo_mod
from picturehouse.metadata.omdb import OMDb, OMDbError, OMDbLimit, na, split_names, year_of
from picturehouse.metadata.tmdb import TMDb, TMDbError, image_url

log = logging.getLogger(__name__)

AUTO_MATCH = 0.85  # score needed to match without asking
MARGIN = 0.08  # ...and this far ahead of the runner-up
CLEAR_WIN = 0.78  # a lower score still matches when nothing else comes close...
CLEAR_MARGIN = 0.2  # ...this far behind it
# Same-titled films and no year in the file name (Dune 1984 vs 2021): the one
# with this many times the TMDb votes of every close rival wins, if it has at
# least POPULAR_MIN votes.
POPULAR_RATIO = 4.0
POPULAR_MIN = 200
# Bump when matching improves: ambiguous/unmatched items are then retried once.
MATCHER_VERSION = 2
TMDB_VARIANTS = 4  # searches per title at most
OMDB_VARIANTS = 2  # OMDb's free quota is 1,000 a day
CANDIDATE_FLOOR = 0.45  # below this a result isn't worth showing as a candidate
EDITABLE = ("title", "year", "overview", "genres", "certification")


# --------------------------------------------------------------------------- scoring


_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
          "ix": "9", "x": "10"}  # fmt: skip


def _norm(s: str) -> str:
    """Comparable form: "The King's Speech" ~ "Kings Speech", "Part II" ~ "2"."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "and")
    s = re.sub(r"['’`]", "", s)  # King's -> kings
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    words = [_ROMAN.get(w, w) for w in words if w not in ("the", "part", "episode")]
    if words and words[0] in ("a", "an"):
        words = words[1:]
    return " ".join(words)


def title_similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    # "Rogue One" vs "Rogue One: A Star Wars Story": the main title matches.
    for full, other in ((b, na), (a, nb)):
        if ":" in full and _norm(full.split(":", 1)[0]) == other:
            ratio = max(ratio, 0.92)
    return ratio


_FRANCHISE = re.compile(r"^(marvel'?s?|disney'?s?|pixar'?s?|dc'?s?)\s+", re.IGNORECASE)


def query_variants(query: str) -> list[tuple[str, str]]:
    """Other ways to search for a file-name title, best first, as (query,
    context). "Marvels Iron Man 3" -> "Iron Man 3"; "Star Wars - Solo" ->
    "Solo" (context "Star Wars") and "Star Wars: Solo"; "Green Mile The" ->
    "The Green Mile"; "Rush Hour 1" -> "Rush Hour". ``context`` holds words
    the variant dropped: see :func:`score`."""
    out: list[tuple[str, str]] = []

    def add(q: str, context: str = "") -> None:
        q = re.sub(r"\s+", " ", q).strip(" -:")
        if q and q.casefold() not in (o.casefold() for o, _ in out):
            out.append((q, context))

    add(query)
    base = _FRANCHISE.sub("", query)
    if m := re.match(r"^(.*?),?\s+(the|a|an)$", base, re.IGNORECASE):
        base = f"{m.group(2)} {m.group(1)}"
    add(base)
    if " - " in base:
        left, right = base.split(" - ", 1)
        left = re.sub(r"\s+\d+$", "", left)  # "Planet Of The Apes 1 - Dawn Of..."
        add(right, context=left)
        add(f"{left}: {right}")
    add(re.sub(r"\s+1$", "", base))  # "Die Hard 1" -> "Die Hard"
    return out


def score(query: str, year: int | None, cand_titles: list[str], cand_year: int | None,
          rank: int, context: str = "") -> float:  # fmt: skip
    """``context``: words a query variant dropped ("Star Wars" from "Star Wars
    - Solo"). A candidate without them is only a partial match: "Solo" (some
    other film) mustn't beat "Solo: A Star Wars Story"."""
    t = max(title_similarity(query, c) for c in cand_titles if c) if any(cand_titles) else 0.0
    if context:
        have = set(_norm(" ".join(c for c in cand_titles if c)).split())
        if not set(_norm(context).split()) <= have:
            t *= 0.9
    if year and cand_year:
        y = 1.0 if year == cand_year else 0.6 if abs(year - cand_year) == 1 else 0.0
    else:
        y = 0.5  # unknown: neither helps nor hurts much
    # TMDb orders by relevance+popularity; a small nudge for the top results.
    return round(0.72 * t + 0.25 * y + 0.03 * max(0, 3 - rank) / 3, 4)


def _float(v) -> float | None:
    try:
        return float(na(v)) if na(v) is not None else None
    except ValueError:
        return None


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


@dataclass
class Candidate:
    tmdb_id: int | None
    title: str
    year: int | None
    score: float
    overview: str = ""
    poster: str | None = None
    imdb_id: str | None = None
    provider: str = "tmdb"  # tmdb | omdb: which id to send back when picking
    votes: int | None = None  # TMDb vote_count: popularity, for same-title tie-breaks
    exact: bool = False  # the title equals the query (after normalising)

    def json(self) -> dict:
        return self.__dict__.copy()


def _exact(query: str, *titles: str | None) -> bool:
    q = _norm(query)
    return bool(q) and any(t and _norm(t) == q for t in titles)


def rank_movies(
    results: list[dict], query: str, year: int | None, context: str = ""
) -> list[Candidate]:
    out = [
        Candidate(r["id"], r.get("title") or r.get("original_title") or "?",
                  _year(r.get("release_date")),
                  score(query, year, [r.get("title", ""), r.get("original_title", "")],
                        _year(r.get("release_date")), i, context),
                  (r.get("overview") or "")[:300], image_url(r.get("poster_path"), "w185"),
                  votes=r.get("vote_count"),
                  exact=_exact(query, r.get("title"), r.get("original_title")))
        for i, r in enumerate(results[:10])
    ]  # fmt: skip
    return sorted(out, key=lambda c: -c.score)


def rank_shows(
    results: list[dict], query: str, year: int | None, context: str = ""
) -> list[Candidate]:
    out = [
        Candidate(r["id"], r.get("name") or r.get("original_name") or "?",
                  _year(r.get("first_air_date")),
                  score(query, year, [r.get("name", ""), r.get("original_name", "")],
                        _year(r.get("first_air_date")), i, context),
                  (r.get("overview") or "")[:300], image_url(r.get("poster_path"), "w185"),
                  votes=r.get("vote_count"),
                  exact=_exact(query, r.get("name"), r.get("original_name")))
        for i, r in enumerate(results[:10])
    ]  # fmt: skip
    return sorted(out, key=lambda c: -c.score)


def rank_omdb(
    results: list[dict], query: str, year: int | None, context: str = ""
) -> list[Candidate]:
    out = [
        Candidate(None, r.get("Title") or "?", year_of(r.get("Year")),
                  score(query, year, [r.get("Title", "")], year_of(r.get("Year")), i, context),
                  "", na(r.get("Poster")), imdb_id=r.get("imdbID"), provider="omdb")
        for i, r in enumerate(results[:10]) if r.get("imdbID")
    ]  # fmt: skip
    return sorted(out, key=lambda c: -c.score)


def merge_ranked(*lists: list[Candidate]) -> list[Candidate]:
    """Candidates from several searches: each record once, at its best score."""
    best: dict[tuple, Candidate] = {}
    for c in (c for lst in lists for c in lst):
        key = (c.provider, c.tmdb_id, c.imdb_id)
        if key not in best or c.score > best[key].score:
            if key in best:
                c.exact = c.exact or best[key].exact
            best[key] = c
    return sorted(best.values(), key=lambda c: -c.score)


def decide(cands: list[Candidate], year: int | None = None) -> tuple[str, Candidate | None]:
    """matched (with the winner) | ambiguous | unmatched.

    ``year``: the year from the file name, if any. Without one, a much more
    popular film wins over same-titled rivals (remakes, shorts, obscure
    namesakes)."""
    if not cands:
        return "unmatched", None
    best = cands[0]
    runner = cands[1].score if len(cands) > 1 else 0.0
    if best.score >= AUTO_MATCH and best.score - runner >= MARGIN:
        return "matched", best
    if best.score >= CLEAR_WIN and best.score - runner >= CLEAR_MARGIN:
        return "matched", best
    if year is None and best.score >= CLEAR_WIN:
        # Rivals that are about as good a title match: is one far more popular?
        # An exact title only competes with other exact titles ("Dune" 1984 vs
        # 2021), not with sequels ("Dune: Part Two").
        close = [c for c in cands if best.score - c.score < MARGIN
                 and (c.exact or not best.exact)]  # fmt: skip
        top = max(close, key=lambda c: c.votes or 0)
        rest = max((c.votes or 0 for c in close if c is not top), default=0)
        if (top.votes or 0) >= POPULAR_MIN and (top.votes or 0) >= POPULAR_RATIO * max(rest, 1):
            return "matched", top
    if best.score >= CANDIDATE_FLOOR:
        return "ambiguous", None
    return "unmatched", None


# --------------------------------------------------------------------------- applying


def _locked(obj) -> set[str]:
    try:
        return set(json.loads(obj.locked or "[]"))
    except ValueError:
        return set()


def _set(obj, locked: set[str], **values) -> None:
    for k, v in values.items():
        if k in locked or v is None or v == "" or v == []:
            continue
        if k == "title":
            obj.title = v
            if hasattr(obj, "sort_title"):
                obj.sort_title = sort_title(v)
        else:
            setattr(obj, k, v)


def _set_images(obj, locked: set[str], poster: str | None, backdrop: str | None) -> None:
    """Artwork always follows the record just matched, even when it has none:
    re-matching to a film without a poster must not keep the old film's one."""
    for k, v in (("poster_url", poster), ("backdrop_url", backdrop)):
        if k not in locked:
            setattr(obj, k, v or None)


def _people(directors: list[str], cast: list[str]) -> str | None:
    if not directors and not cast:
        return None
    return json.dumps({"director": directors, "cast": cast}, ensure_ascii=False)


def _apply_movie(item: MediaItem, d: dict, tmdb: TMDb) -> None:
    lk = _locked(item)
    crew = (d.get("credits") or {}).get("crew", [])
    cast = (d.get("credits") or {}).get("cast", [])
    _set(item, lk, title=d.get("title"), year=_year(d.get("release_date")),
         overview=d.get("overview"), genres=", ".join(g["name"] for g in d.get("genres", [])),
         certification=tmdb.certification(d, "movie"), rating=d.get("vote_average") or None,
         imdb_id=d.get("imdb_id"), tmdb_id=d.get("id"),
         people=_people([c["name"] for c in crew if c.get("job") == "Director"],
                        [c["name"] for c in cast[:12]]))  # fmt: skip
    _set_images(
        item,
        lk,
        image_url(d.get("poster_path"), "w500"),
        image_url(d.get("backdrop_path"), "w1280"),
    )


def _users_nfo(d: nfo_mod.NfoData | None) -> bool:
    """An .nfo the user (or another tool) wrote, not PictureHouse's saved copy."""
    return d is not None and not d.ours


def _apply_nfo(obj, d: nfo_mod.NfoData) -> None:
    restore: set[str] = set()
    if d.ours and d.locked:
        # A saved copy remembers what the user edited: bring those edits back
        # and keep protecting them (database lost, or another PC). Fields the
        # database already has locked are newer and win.
        restore = set(d.locked) - _locked(obj)
        obj.locked = json.dumps(sorted(_locked(obj) | set(d.locked)))
    _set(obj, _locked(obj) - restore, title=d.title, year=d.year, overview=d.overview,
         genres=", ".join(d.genres), certification=d.certification, rating=d.rating,
         imdb_id=d.imdb_id, tmdb_id=d.tmdb_id, people=_people(d.directors, d.cast))  # fmt: skip


def _apply_show(meta: ShowMeta, d: dict, tmdb: TMDb) -> None:
    cast = (d.get("credits") or {}).get("cast", [])
    lk = _locked(meta)
    _set(meta, lk, title=d.get("name"), year=_year(d.get("first_air_date")),
         overview=d.get("overview"), genres=", ".join(g["name"] for g in d.get("genres", [])),
         certification=tmdb.certification(d, "tv"), rating=d.get("vote_average") or None,
         tmdb_id=d.get("id"),
         people=_people([c["name"] for c in d.get("created_by", [])],
                        [c["name"] for c in cast[:12]]))  # fmt: skip
    _set_images(
        meta,
        lk,
        image_url(d.get("poster_path"), "w500"),
        image_url(d.get("backdrop_path"), "w1280"),
    )


def _apply_omdb(obj, d: dict) -> None:
    """Movie or series record from OMDb onto an item or ShowMeta."""
    is_series = (d.get("Type") or "").lower() == "series"
    try:
        rating = float(na(d.get("imdbRating")) or "")
    except ValueError:
        rating = None
    # For series OMDb lists creators under Writer; Director is usually N/A.
    makers = split_names(d.get("Writer") if is_series else d.get("Director"))
    lk = _locked(obj)
    _set(obj, lk, title=na(d.get("Title")), year=year_of(d.get("Year")),
         overview=na(d.get("Plot")), genres=na(d.get("Genre")),
         certification=na(d.get("Rated")), rating=rating, imdb_id=na(d.get("imdbID")),
         people=_people(makers, split_names(d.get("Actors"))))  # fmt: skip
    _set_images(obj, lk, na(d.get("Poster")), None)  # OMDb has no backdrops


def _mark(obj, status: str, source: str | None = None, confidence: float | None = None,
          candidates: list[Candidate] | None = None) -> None:  # fmt: skip
    obj.match_status = status
    if source:
        obj.metadata_source = source
    obj.match_confidence = confidence
    obj.match_candidates = (json.dumps([c.json() for c in candidates[:6]], ensure_ascii=False)
                            if candidates else None)  # fmt: skip
    if status == "matched":
        obj.matched_at = utcnow()


# --------------------------------------------------------------------------- matcher


@dataclass
class MatchSummary:
    films: dict[str, int] = field(default_factory=dict)
    shows: dict[str, int] = field(default_factory=dict)
    episodes: int = 0
    errors: list[str] = field(default_factory=list)
    online: bool = False

    def count(self, bucket: dict[str, int], status: str) -> None:
        bucket[status] = bucket.get(status, 0) + 1


class Matcher:
    def __init__(self, db: Database, tmdb: TMDb | None, omdb: OMDb | None = None,
                 state_file: Path | None = None) -> None:  # fmt: skip
        """``state_file`` remembers MATCHER_VERSION, so items the matcher
        couldn't settle are retried once after it improves."""
        self.db = db
        self.state_file = state_file
        self.tmdb = tmdb
        self.omdb = omdb
        self._omdb_exhausted = False  # daily quota hit during this run
        self._lock = threading.Lock()
        self.running = False
        self.on_change: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- batch

    def match(self, *, force: bool = False, libraries: list[str] | None = None) -> MatchSummary:
        """Match pending items (all items with ``force``; manual matches are
        never re-searched, and locked fields are never overwritten)."""
        with self._lock:
            self.running = True
            try:
                summary = MatchSummary(online=self.online)
                self._omdb_exhausted = False
                self._skip_offline_kinds()
                retry = self.online and self._matcher_improved()
                if retry:
                    log.info("matcher improved: retrying ambiguous and unmatched items")
                for item_id in self._film_ids(force, libraries, retry):
                    try:
                        summary.count(summary.films, self.match_film(item_id))
                    except Exception as e:  # one item never stops the batch
                        log.exception("matching film %s failed", item_id)
                        summary.errors.append(f"item {item_id}: {e}")
                for show in self._show_names(force, libraries, retry):
                    try:
                        status, n = self.match_show(show, force=force)
                        summary.count(summary.shows, status)
                        summary.episodes += n
                    except Exception as e:
                        log.exception("matching show %r failed", show)
                        summary.errors.append(f"show {show}: {e}")
                if retry and not libraries:
                    self._save_matcher_version()
                if summary.films or summary.shows:
                    for cb in self.on_change:
                        cb()
                return summary
            finally:
                self.running = False

    def _skip_offline_kinds(self) -> None:
        with self.db.session() as s:
            for i in s.scalars(select(MediaItem).where(
                    MediaItem.kind.in_(("home_video", "track", "photo", "other")),
                    MediaItem.match_status == "pending")):  # fmt: skip
                i.match_status = "skipped"

    def _matcher_improved(self) -> bool:
        if self.state_file is None:
            return False
        try:
            return int(self.state_file.read_text().strip() or 0) < MATCHER_VERSION
        except (OSError, ValueError):
            return True  # never recorded: items from before versioning

    def _save_matcher_version(self) -> None:
        if self.state_file is not None:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(str(MATCHER_VERSION))

    def _film_ids(self, force: bool, libraries: list[str] | None,
                  retry: bool = False) -> list[int]:  # fmt: skip
        from picturehouse.db.models import LibraryRootRow

        q = (select(MediaItem.id, MediaItem.locked).join(LibraryRootRow)
             .where(MediaItem.kind == "film", MediaItem.probe_ok, MediaItem.available))  # fmt: skip
        if libraries:
            q = q.where(LibraryRootRow.name.in_(libraries))
        if not force:
            statuses = ("pending", "ambiguous", "unmatched") if retry else ("pending",)
            q = q.where(MediaItem.match_status.in_(statuses))
        with self.db.session() as s:
            return [i for i, _lk in s.execute(q)]

    def _show_names(self, force: bool, libraries: list[str] | None,
                    retry: bool = False) -> list[str]:  # fmt: skip
        from picturehouse.db.models import LibraryRootRow

        q = (select(MediaItem.parsed_show).join(LibraryRootRow)
             .where(MediaItem.kind == "episode", MediaItem.parsed_show.is_not(None),
                    MediaItem.available).distinct())  # fmt: skip
        if libraries:
            q = q.where(LibraryRootRow.name.in_(libraries))
        with self.db.session() as s:
            names = list(s.scalars(q))
            out = []
            for n in names:
                meta = s.get(ShowMeta, n)
                pending_eps = s.scalar(select(MediaItem.id).where(
                    MediaItem.parsed_show == n, MediaItem.match_status == "pending").limit(1))  # fmt: skip
                unsettled = retry and meta is not None and meta.match_status in (
                    "ambiguous", "unmatched") and "match" not in _locked(meta)  # fmt: skip
                if meta is None or force or meta.match_status == "pending" or pending_eps                         or unsettled:  # fmt: skip
                    out.append(n)
            return out

    @staticmethod
    def _reparse(item: MediaItem) -> None:
        """Re-read the title from the file name with the current parser, so
        parser fixes reach files scanned before them (scans skip unchanged
        files)."""
        n = derive(item.rel_path, item.root.kind, None)
        if n.kind != "film" or (n.title, n.year) == (item.parsed_title, item.parsed_year):
            return
        item.parsed_title, item.parsed_year = n.title, n.year
        if (item.metadata_source or "file") == "file":
            locked = _locked(item)
            if "title" not in locked:
                item.title, item.sort_title = n.title, sort_title(n.title)
            if "year" not in locked:
                item.year = n.year

    @staticmethod
    def _ranked(search: Callable, rank: Callable, query: str, year: int | None,
                max_variants: int = TMDB_VARIANTS) -> list[Candidate]:  # fmt: skip
        """Search the file-name title, then its variants (see query_variants)
        until one gives a clear match; every result is kept as a candidate."""
        lists: list[list[Candidate]] = []
        cands: list[Candidate] = []
        for q, context in query_variants(query)[:max_variants]:
            results = search(q, year)
            if not results and year:
                results = search(q, None)  # the file's year may be wrong
            lists.append(rank(results, q, year, context))
            cands = merge_ranked(*lists)
            if decide(cands, year)[0] == "matched":
                break
        return cands

    # ---------------------------------------------------------------- films

    def match_film(self, item_id: int, *, tmdb_id: int | None = None, manual: bool = False,
                   imdb_id: str | None = None) -> str:  # fmt: skip
        """Match one film. ``tmdb_id`` / ``imdb_id`` force a specific record (a user fix;
        an IMDb id is looked up on OMDb)."""
        if manual and tmdb_id is not None and not self.tmdb:
            raise TMDbError("no TMDb API key configured")
        if manual and imdb_id is not None and not self.omdb:
            raise OMDbError("no OMDb API key configured")
        with self.db.session() as s:
            item = s.get(MediaItem, item_id)
            root = Path(item.root.path)
            if "match" not in _locked(item):
                self._reparse(item)
            nfo = nfo_mod.find_film_nfo(root, item.rel_path)
            query = item.parsed_title or item.title
            year = item.parsed_year
            if tmdb_id is None and "match" in _locked(item) and item.tmdb_id:
                tmdb_id, manual = item.tmdb_id, True  # user's choice: refresh, never re-search
            elif (tmdb_id is None and imdb_id is None and "match" in _locked(item)
                  and item.imdb_id and not item.tmdb_id):  # fmt: skip
                imdb_id, manual = item.imdb_id, True  # a manual OMDb pick
            if imdb_id is not None:
                return self._film_via_omdb(item, query, year, nfo, imdb_id, manual)

            if tmdb_id is None and nfo:
                tmdb_id = nfo.tmdb_id or self._imdb_to_tmdb(nfo.imdb_id, "movie")
            if tmdb_id is not None and self.tmdb:
                fetched = False
                try:
                    _apply_movie(item, self.tmdb.movie(tmdb_id), self.tmdb)
                    fetched = True
                except TMDbError as e:
                    log.warning("TMDb lookup of movie %s failed: %s", tmdb_id, e)
                    if not nfo:
                        if manual:
                            raise
                        return self._leave_pending(item)
                if nfo:
                    _apply_nfo(item, nfo.for_online_match() if fetched else nfo)
                source = "manual" if manual else ("nfo" if _users_nfo(nfo) else "tmdb")
                if manual:
                    item.locked = json.dumps(sorted(_locked(item) | {"match"}))
                    item.tmdb_id = tmdb_id
                _mark(item, "matched", source, 1.0)
                return "matched"
            if not self.tmdb and nfo and nfo.imdb_id and self._omdb_ok():
                return self._film_via_omdb(item, query, year, nfo, nfo.imdb_id, False)
            if nfo and nfo.title:
                _apply_nfo(item, nfo)
                _mark(item, "matched", "nfo", 1.0)
                return "matched"
            if not self.tmdb:
                if self._omdb_ok():
                    return self._film_via_omdb(item, query, year, None, None, False)
                return item.match_status  # stays pending until a key is configured

            try:
                cands = self._ranked(self.tmdb.search_movie, rank_movies, query, year)
            except TMDbError as e:
                log.warning("TMDb search for %r failed: %s", query, e)
                return self._leave_pending(item)
            status, best = decide(cands, year)
            if not cands and self._omdb_ok():
                return self._film_via_omdb(item, query, year, None, None, False)
            if best is not None:
                try:
                    _apply_movie(item, self.tmdb.movie(best.tmdb_id), self.tmdb)
                except TMDbError as e:
                    log.warning("TMDb details for %s failed: %s", best.tmdb_id, e)
                    return self._leave_pending(item)
                _mark(item, "matched", "tmdb", best.score)
            else:
                _mark(item, status, None, cands[0].score if cands else None,
                      [c for c in cands if c.score >= CANDIDATE_FLOOR] or cands[:3])  # fmt: skip
            log.info("film %r: %s", query, status,
                     extra={"item": item_id, "confidence": item.match_confidence})  # fmt: skip
            return status

    def _film_via_omdb(self, item: MediaItem, query: str, year: int | None,
                       nfo: nfo_mod.NfoData | None, imdb_id: str | None, manual: bool) -> str:  # fmt: skip
        try:
            cands = None
            if imdb_id:
                d = self.omdb.by_id(imdb_id)
                status, conf = "matched", 1.0
                source = "manual" if manual else ("nfo" if _users_nfo(nfo) else "omdb")
            else:
                cands = self._ranked(lambda q, y: self.omdb.search(q, y, "movie"), rank_omdb,
                                     query, year, OMDB_VARIANTS)  # fmt: skip
                status, best = decide(cands, year)
                d = self.omdb.by_id(best.imdb_id) if best else None
                source, conf = "omdb", (best.score if best else cands[0].score if cands else None)
        except OMDbLimit as e:
            self._omdb_exhausted = True
            log.warning("%s; remaining items wait for the next run", e)
            if manual:
                raise
            return self._leave_pending(item)
        except OMDbError as e:
            log.warning("OMDb lookup for %r failed: %s", query, e)
            if manual:
                raise
            return self._leave_pending(item)
        if d:
            _apply_omdb(item, d)
            if manual:
                item.locked = json.dumps(sorted(_locked(item) | {"match"}))
                item.imdb_id, item.tmdb_id = imdb_id, None
        if nfo:
            _apply_nfo(item, nfo.for_online_match() if d else nfo)
        if status == "matched":
            _mark(item, "matched", source, conf)
        else:
            _mark(item, status, None, conf,
                  [c for c in cands if c.score >= CANDIDATE_FLOOR] or cands[:3])  # fmt: skip
        log.info("film %r (OMDb): %s", query, status, extra={"item": item.id})
        return status

    @property
    def online(self) -> bool:
        return self.tmdb is not None or self.omdb is not None

    def _omdb_ok(self) -> bool:
        return self.omdb is not None and not self._omdb_exhausted

    @staticmethod
    def _leave_pending(item: MediaItem) -> str:
        # Network trouble: try again next time instead of recording a bad state.
        return item.match_status

    def _imdb_to_tmdb(self, imdb_id: str | None, kind: str) -> int | None:
        if not imdb_id or not self.tmdb:
            return None
        try:
            res = self.tmdb.find_imdb(imdb_id)
        except TMDbError:
            return None
        hits = res.get("movie_results" if kind == "movie" else "tv_results") or []
        return hits[0]["id"] if hits else None

    # ---------------------------------------------------------------- shows

    def match_show(self, parsed_show: str, *, tmdb_id: int | None = None,
                   manual: bool = False, force: bool = False,
                   imdb_id: str | None = None) -> tuple[str, int]:  # fmt: skip
        """Match a series and fill in its episodes. Returns (status, episodes updated)."""
        if manual and tmdb_id is not None and not self.tmdb:
            raise TMDbError("no TMDb API key configured")
        if manual and imdb_id is not None and not self.omdb:
            raise OMDbError("no OMDb API key configured")
        with self.db.session() as s:
            meta = s.get(ShowMeta, parsed_show)
            if meta is None:
                meta = ShowMeta(parsed_show=parsed_show, locked="[]", metadata_source="file",
                                match_status="pending")  # fmt: skip
                s.add(meta)
            eps = list(s.scalars(select(MediaItem).where(
                MediaItem.parsed_show == parsed_show, MediaItem.kind == "episode")))  # fmt: skip
            if not eps:
                return "unmatched", 0
            root = Path(eps[0].root.path)
            nfo = nfo_mod.find_show_nfo(root, eps[0].rel_path)
            year = min((e.parsed_year for e in eps if e.parsed_year), default=None)

            if (
                tmdb_id is None
                and meta.tmdb_id
                and ("match" in _locked(meta) or (meta.match_status == "matched" and not force))
            ):
                # Known show (new episodes, or a refresh): reuse the id, don't re-search.
                tmdb_id, manual = meta.tmdb_id, "match" in _locked(meta)
            elif (
                tmdb_id is None and imdb_id is None and meta.imdb_id and not meta.tmdb_id
                and meta.metadata_source in ("omdb", "manual") and self._omdb_ok()
                and ("match" in _locked(meta) or (meta.match_status == "matched" and not force))
            ):  # fmt: skip
                # Known OMDb show: reuse its IMDb id.
                return self._show_via_omdb(meta, eps, root, parsed_show, year, nfo,
                                           meta.imdb_id, "match" in _locked(meta))  # fmt: skip
            elif tmdb_id is None and meta.match_status == "matched" and not force:
                return "matched", self._fill_episodes(meta, eps, root)  # .nfo-only match
            if imdb_id is not None:
                return self._show_via_omdb(meta, eps, root, parsed_show, year, nfo, imdb_id, manual)
            if tmdb_id is None and nfo:
                tmdb_id = nfo.tmdb_id or self._imdb_to_tmdb(nfo.imdb_id, "tv")
            if tmdb_id is None and not self.tmdb and self._omdb_ok():
                return self._show_via_omdb(meta, eps, root, parsed_show, year, nfo,
                                           nfo.imdb_id if nfo else None, False)  # fmt: skip

            status, source, conf, cands = meta.match_status, None, None, None
            details = None
            if tmdb_id is not None and self.tmdb:
                try:
                    details = self.tmdb.tv(tmdb_id)
                    status, source, conf = (
                        "matched",
                        ("manual" if manual else "nfo" if _users_nfo(nfo) else "tmdb"),
                        1.0,
                    )
                except TMDbError as e:
                    log.warning("TMDb lookup of tv %s failed: %s", tmdb_id, e)
                    if manual:
                        raise
            elif self.tmdb:
                try:
                    cands = self._ranked(self.tmdb.search_tv, rank_shows, parsed_show, year)
                    status, best = decide(cands, year)
                    if not cands and not nfo and self._omdb_ok():
                        return self._show_via_omdb(meta, eps, root, parsed_show, year, None,
                                                   None, False)  # fmt: skip
                    if best:
                        details = self.tmdb.tv(best.tmdb_id)
                        source, conf = "tmdb", best.score
                    else:
                        conf = cands[0].score if cands else None
                        cands = [c for c in cands if c.score >= CANDIDATE_FLOOR] or cands[:3]
                except TMDbError as e:
                    log.warning("TMDb search for show %r failed: %s", parsed_show, e)
                    return meta.match_status, 0

            if details:
                _apply_show(meta, details, self.tmdb)
                if manual:
                    meta.locked = json.dumps(sorted(_locked(meta) | {"match"}))
                    meta.tmdb_id = tmdb_id
            if nfo and nfo.title:
                _apply_nfo(meta, nfo.for_online_match() if details else nfo)
                if not details:
                    status, source, conf = "matched", "nfo", 1.0
            if status != meta.match_status or details or nfo:
                _mark(meta, status, source, conf, None if status == "matched" else cands)
            if status != "matched":
                for e in eps:
                    if e.match_status == "pending":
                        e.match_status = status if self.online else "pending"
                log.info("show %r: %s", parsed_show, status)
                return status, 0

            n = self._fill_episodes(meta, eps, root)
            log.info("show %r: matched (%d episodes)", parsed_show, n,
                     extra={"tmdb_id": meta.tmdb_id})  # fmt: skip
            return "matched", n

    def _show_via_omdb(self, meta: ShowMeta, eps: list[MediaItem], root: Path, parsed_show: str,
                       year: int | None, nfo: nfo_mod.NfoData | None, imdb_id: str | None,
                       manual: bool) -> tuple[str, int]:  # fmt: skip
        try:
            cands = None
            if imdb_id:
                d = self.omdb.by_id(imdb_id)
                status, conf = "matched", 1.0
                source = "manual" if manual else ("nfo" if _users_nfo(nfo) else "omdb")
            else:
                cands = self._ranked(lambda q, y: self.omdb.search(q, y, "series"), rank_omdb,
                                     parsed_show, year, OMDB_VARIANTS)  # fmt: skip
                status, best = decide(cands, year)
                d = self.omdb.by_id(best.imdb_id) if best else None
                source, conf = "omdb", (best.score if best else cands[0].score if cands else None)
        except OMDbLimit as e:
            self._omdb_exhausted = True
            log.warning("%s; remaining items wait for the next run", e)
            if manual:
                raise
            return meta.match_status, 0
        except OMDbError as e:
            log.warning("OMDb lookup for show %r failed: %s", parsed_show, e)
            if manual:
                raise
            return meta.match_status, 0
        if d:
            _apply_omdb(meta, d)
            meta.tmdb_id = None
            if manual:
                meta.locked = json.dumps(sorted(_locked(meta) | {"match"}))
                meta.imdb_id = imdb_id
        if nfo and nfo.title:
            _apply_nfo(meta, nfo.for_online_match() if d else nfo)
        if status == "matched":
            _mark(meta, "matched", source, conf)
        else:
            _mark(meta, status, None, conf,
                  [c for c in cands if c.score >= CANDIDATE_FLOOR] or cands[:3])  # fmt: skip
            for e in eps:
                if e.match_status == "pending":
                    e.match_status = status
            log.info("show %r (OMDb): %s", parsed_show, status)
            return status, 0
        n = self._fill_episodes(meta, eps, root)
        log.info("show %r (OMDb): matched (%d episodes)", parsed_show, n)
        return "matched", n

    def _fill_episodes(self, meta: ShowMeta, eps: list[MediaItem], root: Path) -> int:
        seasons: dict[int, dict[int, dict]] = {}
        provider = "tmdb"
        if self.omdb and meta.imdb_id and not meta.tmdb_id and not self._omdb_exhausted:
            # OMDb season lists: titles, air dates and ratings (no plots or stills).
            provider = "omdb"
            for n in sorted({e.season for e in eps if e.season is not None}):
                try:
                    seasons[n] = {
                        int(ep["Episode"]): {
                            "name": na(ep.get("Title")), "air_date": na(ep.get("Released")),
                            "vote_average": _float(ep.get("imdbRating")),
                            "imdb_id": na(ep.get("imdbID")),
                        }
                        for ep in self.omdb.season(meta.imdb_id, n)
                        if str(ep.get("Episode", "")).isdigit()
                    }  # fmt: skip
                except OMDbLimit:
                    self._omdb_exhausted = True
                    break
                except OMDbError as e:
                    log.info("no OMDb data for %s season %s: %s", meta.title, n, e)
        elif self.tmdb and meta.tmdb_id:
            for n in sorted({e.season for e in eps if e.season is not None}):
                try:
                    data = self.tmdb.season(meta.tmdb_id, n)
                    seasons[n] = {ep["episode_number"]: ep for ep in data.get("episodes", [])}
                except TMDbError as e:
                    log.info("no TMDb data for %s season %s: %s", meta.title, n, e)
        updated = 0
        for e in eps:
            lk = _locked(e)
            if "show" not in lk:
                e.show = meta.title or e.parsed_show
            ep = seasons.get(e.season or -1, {}).get(e.episode or -1)
            if ep:
                title = ep.get("name")
                if e.episode_end and e.episode_end != e.episode:
                    last = seasons.get(e.season, {}).get(e.episode_end)
                    if last and last.get("name") and last["name"] != title:
                        title = f"{title} / {last['name']}"
                _set(e, lk, title=title, overview=ep.get("overview"),
                     year=_year(ep.get("air_date")), rating=ep.get("vote_average") or None,
                     poster_url=image_url(ep.get("still_path"), "w780"))  # fmt: skip
                e.tmdb_id = ep.get("id")
                if ep.get("imdb_id"):
                    e.imdb_id = ep["imdb_id"]
            ep_nfo = nfo_mod.find_episode_nfo(root, e.rel_path)
            if ep_nfo:
                _apply_nfo(e, ep_nfo.for_online_match() if ep else ep_nfo)
            if e.metadata_source != "manual":
                e.metadata_source = (
                    "nfo" if _users_nfo(ep_nfo) else (provider if ep else meta.metadata_source)
                )
            e.match_status = "matched"
            e.matched_at = utcnow()
            updated += 1
        return updated

    def refresh(self, item_ids: list[int] | None = None) -> MatchSummary:
        """Re-fetch details for matched items by their existing ids (no re-search)."""
        summary = MatchSummary(online=self.online)
        self._omdb_exhausted = False
        with self.db.session() as s:
            q = select(MediaItem.id, MediaItem.kind, MediaItem.parsed_show, MediaItem.tmdb_id,
                       MediaItem.imdb_id, MediaItem.metadata_source,
                       MediaItem.locked).where(MediaItem.match_status == "matched")  # fmt: skip
            if item_ids:
                q = q.where(MediaItem.id.in_(item_ids))
            rows = s.execute(q).all()
        shows = sorted({r.parsed_show for r in rows if r.kind == "episode" and r.parsed_show})
        with self._lock:
            for r in rows:
                if r.kind == "film" and r.tmdb_id:
                    summary.count(summary.films, self.match_film(
                        r.id, tmdb_id=r.tmdb_id, manual="match" in (r.locked or "")))  # fmt: skip
                elif r.kind == "film" and r.imdb_id and self.omdb:
                    summary.count(summary.films, self.match_film(
                        r.id, imdb_id=r.imdb_id, manual="match" in (r.locked or "")))  # fmt: skip
            for show in shows:
                status, n = self.match_show(show)
                summary.count(summary.shows, status)
                summary.episodes += n
        for cb in self.on_change:
            cb()
        return summary

    # ---------------------------------------------------------------- manual edits

    def edit(self, item_id: int | None, show: str | None, fields: dict) -> None:
        """User edits: set the fields and lock them against future matching."""
        bad = set(fields) - set(EDITABLE)
        if bad:
            raise ValueError(f"not editable: {', '.join(sorted(bad))}")
        with self.db.session() as s:
            objs = []
            if item_id is not None:
                objs.append(s.get(MediaItem, item_id))
            if show is not None:
                meta = s.get(ShowMeta, show) or ShowMeta(parsed_show=show, locked="[]",
                                                         match_status="pending")  # fmt: skip
                s.add(meta)
                objs.append(meta)
            for obj in objs:
                if obj is None:
                    raise LookupError("no such item")
                for k, v in fields.items():
                    if k == "title":
                        obj.title = v
                        if hasattr(obj, "sort_title"):
                            obj.sort_title = sort_title(v)
                    else:
                        setattr(obj, k, v if v != "" else None)
                obj.locked = json.dumps(sorted(_locked(obj) | set(fields)))
                if obj.metadata_source == "file":
                    obj.metadata_source = "manual"
            if show is not None and "title" in fields:
                for e in s.scalars(select(MediaItem).where(MediaItem.parsed_show == show)):
                    if "show" not in _locked(e):
                        e.show = fields["title"]
        for cb in self.on_change:
            cb()

    def unlock(self, item_id: int | None, show: str | None) -> None:
        """Forget manual edits and the manual match; the next match starts fresh."""
        with self.db.session() as s:
            objs = [s.get(MediaItem, item_id)] if item_id is not None else []
            if show is not None and (meta := s.get(ShowMeta, show)):
                objs.append(meta)
            for obj in objs:
                if obj is None:
                    continue
                for f in _locked(obj) - {"match"}:
                    if f == "title":
                        parsed = getattr(obj, "parsed_title", None) or getattr(
                            obj, "parsed_show", None)  # fmt: skip
                        if isinstance(obj, ShowMeta):
                            obj.title = None
                        else:
                            obj.title = parsed or obj.title
                            obj.sort_title = sort_title(obj.title)
                    elif f == "year":
                        obj.year = getattr(obj, "parsed_year", None)
                    else:
                        setattr(obj, f, None)
                obj.locked = "[]"
                obj.metadata_source = "file"
                obj.match_status = "pending"
            if show is not None:
                for e in s.scalars(select(MediaItem).where(MediaItem.parsed_show == show)):
                    if e.metadata_source != "manual":
                        e.show = e.parsed_show

    # ---------------------------------------------------------------- candidates

    def search(self, kind: str, query: str, year: int | None) -> list[Candidate]:
        if not self.tmdb and self.omdb:
            omdb_kind = "series" if kind == "show" else "movie"
            return rank_omdb(self.omdb.search(query, year, omdb_kind), query, year)
        if not self.tmdb:
            raise TMDbError("no TMDb or OMDb API key configured")
        if kind == "show":
            return rank_shows(self.tmdb.search_tv(query, year), query, year)
        return rank_movies(self.tmdb.search_movie(query, year), query, year)
