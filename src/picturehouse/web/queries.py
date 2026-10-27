"""Read-side queries and JSON shaping shared by the web API and DLNA."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, object_session

from picturehouse.db.models import (
    LibraryRootRow,
    MediaItem,
    PlaybackState,
    ShowMeta,
    is_hdr,
)
from picturehouse.library.naming import split_year
from picturehouse.profiles import SCOPE

VIDEO_KINDS = ("film", "episode", "home_video")


def encode_key(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_key(key: str) -> list[str]:
    try:
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        return raw.decode("utf-8").split("\x1f")
    except (ValueError, UnicodeDecodeError):
        return []


def in_scope():
    """Libraries this request may see: configured ones, and for a child
    profile only kids libraries (see picturehouse.profiles). Every query that
    lists or finds items joins LibraryRootRow and uses this."""
    if SCOPE.get().kids_only:
        return and_(LibraryRootRow.configured, LibraryRootRow.kids)
    return LibraryRootRow.configured


def visible() -> Select:
    """Items a client may browse: probed fine, file present, library in scope."""
    return (
        select(MediaItem)
        .join(LibraryRootRow)
        .where(MediaItem.available, MediaItem.probe_ok, in_scope())
    )


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit offset. SQLite hands back naive datetimes even
    for timezone-aware columns; everything we store is UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def quality(i: MediaItem) -> str | None:
    if i.media_type != "video":
        return None
    w, h = i.width or 0, i.height or 0
    if w >= 3200 or h >= 1800:
        return "4K"
    if w >= 1800 or h >= 1000:
        return "1080p"
    if w >= 1200 or h >= 700:
        return "720p"
    return "SD"


def art_version(item: MediaItem, meta: ShowMeta | None = None) -> str:
    """Changes whenever the artwork an item would be served with may change:
    a new match (other TMDb/OMDb image URLs) or a changed file. Added to art
    URLs as ``?v=``, so browsers and TVs fetch afresh instead of reusing a
    cached image, or the "no image" they remembered from before the match."""
    if meta is None and item.kind == "episode" and item.parsed_show:
        s = object_session(item)
        meta = s.get(ShowMeta, item.parsed_show) if s is not None else None
    parts = [item.mtime_ns, item.poster_url, item.backdrop_url,
             meta.poster_url if meta else None, meta.backdrop_url if meta else None]  # fmt: skip
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:10]


def art_url(item: MediaItem, which: str, meta: ShowMeta | None = None) -> str:
    return f"/api/items/{item.id}/art/{which}?v={art_version(item, meta)}"


def photo_url(item: MediaItem, size: str) -> str:
    return f"/api/items/{item.id}/photo?size={size}&v={item.mtime_ns}"


def art_urls(item: MediaItem) -> dict[str, str]:
    if item.kind == "photo":  # the picture itself is the artwork
        return {"poster": photo_url(item, "thumb"), "thumb": photo_url(item, "thumb"),
                "backdrop": photo_url(item, "screen")}  # fmt: skip
    v = art_version(item)
    base = f"/api/items/{item.id}/art"
    return {w: f"{base}/{w}?v={v}" for w in ("poster", "thumb", "backdrop")}


def progress_map(s: Session, ids: Iterable[int]) -> dict[int, PlaybackState]:
    ids = list(ids)
    if not ids:
        return {}
    rows = s.scalars(select(PlaybackState).where(
        PlaybackState.profile_id == SCOPE.get().profile_id, PlaybackState.item_id.in_(ids)))  # fmt: skip
    return {r.item_id: r for r in rows}


def progress_of(s: Session, item_id: int) -> PlaybackState | None:
    """This profile's progress on one item."""
    return s.get(PlaybackState, (SCOPE.get().profile_id, item_id))


def metadata_json(obj) -> dict:
    """Where an item's (or show's) metadata came from, for the console."""
    try:
        cands = json.loads(obj.match_candidates) if obj.match_candidates else []
    except ValueError:
        cands = []
    try:
        locked = json.loads(obj.locked or "[]")
    except ValueError:
        locked = []
    try:
        people = json.loads(obj.people) if obj.people else {}
    except ValueError:
        people = {}
    return {
        "source": obj.metadata_source,
        "status": obj.match_status,
        "confidence": obj.match_confidence,
        "tmdb_id": obj.tmdb_id,
        "imdb_id": obj.imdb_id,
        "locked": locked,
        "candidates": cands,
        "people": people,
        "matched_at": iso(obj.matched_at),
    }


def item_json(i: MediaItem, prog: PlaybackState | None = None) -> dict:
    return {
        "id": i.id,
        "kind": i.kind,
        "title": i.title,
        "year": i.year,
        "show": i.show,
        "season": i.season,
        "episode": i.episode,
        "episode_end": i.episode_end,
        "show_key": encode_key(i.show) if i.kind == "episode" and i.show else None,
        # Same key as /api/albums uses, so a client can queue the rest of the album.
        "album_key": encode_key(i.album_artist or i.artist or "", i.album or "")
        if i.kind == "track"
        else None,
        "artist": i.album_artist or i.artist if i.kind == "track" else i.artist,
        "album": i.album,
        "track_no": i.track_no,
        "duration": i.duration,
        "added_at": iso(i.added_at),
        "available": i.available,
        "quality": quality(i),
        "hdr": is_hdr(i),
        "overview": i.overview,
        "genres": i.genres,
        "certification": i.certification,
        "rating": i.rating,
        "art": art_urls(i),
        "progress": {"position": prog.position, "completed": prog.completed} if prog else None,
    }


def items_json(s: Session, items: list[MediaItem]) -> list[dict]:
    pm = progress_map(s, (i.id for i in items))
    return [item_json(i, pm.get(i.id)) for i in items]


def sort_items(q: Select, sort: str) -> Select:
    if sort == "az":
        return q.order_by(MediaItem.sort_title, MediaItem.year)
    if sort == "year":
        return q.order_by(MediaItem.year.desc().nulls_last(), MediaItem.sort_title)
    return q.order_by(MediaItem.added_at.desc(), MediaItem.id.desc())


# ---------------------------------------------------------------- shows


def shows(s: Session, sort: str = "added", where=None) -> list[dict]:
    q = (
        select(
            MediaItem.show,
            func.min(MediaItem.year),
            func.count(MediaItem.id),
            func.count(func.distinct(MediaItem.season)),
            func.max(MediaItem.added_at),
            func.min(MediaItem.id),
        )
        .join(LibraryRootRow)
        .where(
            MediaItem.available,
            MediaItem.probe_ok,
            in_scope(),
            MediaItem.kind == "episode",
            MediaItem.show.is_not(None),
        )  # fmt: skip
        .group_by(MediaItem.show)
    )
    if where is not None:
        q = q.where(where)
    out = []
    for show, year, n_eps, n_seasons, added, first_id in s.execute(q):
        first = s.get(MediaItem, first_id)
        meta = s.get(ShowMeta, first.parsed_show) if first and first.parsed_show else None
        out.append({
            "key": encode_key(show), "title": show,
            "year": (meta.year if meta and meta.year else year), "seasons": n_seasons,
            "episodes": n_eps, "added_at": iso(added),
            "overview": meta.overview if meta else None,
            "genres": meta.genres if meta else None,
            "certification": meta.certification if meta else None,
            "rating": meta.rating if meta else None,
            "metadata": metadata_json(meta) if meta else None,
            "art": {"poster": art_url(first, "poster", meta),
                    "backdrop": art_url(first, "backdrop", meta)} if first else {},
            "_first_id": first_id,
            "_parsed_show": first.parsed_show if first else show,
        })  # fmt: skip
    _sort_groups(out, sort)
    return out


def show_episodes(s: Session, show: str) -> list[MediaItem]:
    q = visible().where(MediaItem.kind == "episode", MediaItem.show == show)
    q = q.order_by(MediaItem.season.nulls_last(), MediaItem.episode.nulls_last(), MediaItem.title)
    return list(s.scalars(q))


# --------------------------------------------------------------- albums


def _album_artist():
    return func.coalesce(MediaItem.album_artist, MediaItem.artist, "")


def albums(s: Session, sort: str = "added", where=None) -> list[dict]:
    artist = _album_artist()
    q = (
        select(
            artist,
            func.coalesce(MediaItem.album, ""),
            func.max(MediaItem.year),
            func.count(MediaItem.id),
            func.sum(MediaItem.duration),
            func.max(MediaItem.added_at),
            func.min(MediaItem.id),
        )
        .join(LibraryRootRow)
        .where(
            MediaItem.available,
            MediaItem.probe_ok,
            in_scope(),
            MediaItem.kind == "track",
        )  # fmt: skip
        .group_by(artist, func.coalesce(MediaItem.album, ""))
    )
    if where is not None:
        q = q.where(where)
    out = []
    for art_name, album, year, n, dur, added, first_id in s.execute(q):
        first = s.get(MediaItem, first_id)
        out.append({
            "key": encode_key(art_name, album), "title": album or "Unknown album",
            "artist": art_name or "Unknown artist", "year": year, "tracks": n,
            "duration": dur, "added_at": iso(added),
            "art": {"poster": art_url(first, "poster")},
            "_first_id": first_id,
        })  # fmt: skip
    _sort_groups(out, sort)
    return out


def album_tracks(s: Session, artist: str, album: str) -> list[MediaItem]:
    q = visible().where(
        MediaItem.kind == "track",
        _album_artist() == artist,
        func.coalesce(MediaItem.album, "") == album,
    )
    q = q.order_by(MediaItem.disc_no.nulls_first(), MediaItem.track_no.nulls_last(),
                   MediaItem.title)  # fmt: skip
    return list(s.scalars(q))


def _sort_groups(rows: list[dict], sort: str) -> None:
    if sort == "az":
        rows.sort(key=lambda r: (r["title"] or "").casefold())
    elif sort == "year":
        rows.sort(key=lambda r: (-(r["year"] or 0), (r["title"] or "").casefold()))
    else:
        rows.sort(key=lambda r: r["added_at"] or "", reverse=True)


def strip_private(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def text_match(term: str, *cols):
    like = f"%{term}%"
    return or_(*(c.ilike(like) for c in cols))


def counts(s: Session) -> dict[str, int]:
    base = and_(MediaItem.available, MediaItem.probe_ok, in_scope())

    def n(*conds) -> int:
        q = select(func.count(MediaItem.id)).join(LibraryRootRow).where(base, *conds)
        return s.scalar(q) or 0

    def distinct(col, *conds) -> int:
        q = select(func.count(func.distinct(col))).join(LibraryRootRow).where(base, *conds)
        return s.scalar(q) or 0

    return {
        "films": n(MediaItem.kind == "film"),
        "episodes": n(MediaItem.kind == "episode"),
        "shows": distinct(MediaItem.show, MediaItem.kind == "episode"),
        "tracks": n(MediaItem.kind == "track"),
        "albums": distinct(
            func.coalesce(MediaItem.album_artist, MediaItem.artist, "")
            + "\x1f"
            + func.coalesce(MediaItem.album, ""),
            MediaItem.kind == "track",
        ),  # fmt: skip
        "home_videos": n(MediaItem.kind == "home_video"),
        "photos": n(MediaItem.kind == "photo"),
        "photo_albums": distinct(func.coalesce(MediaItem.album, ""), MediaItem.kind == "photo"),
        "failed": s.scalar(
            select(func.count(MediaItem.id))
            .join(LibraryRootRow)
            .where(MediaItem.probe_ok.is_(False), MediaItem.available, in_scope())
        )
        or 0,  # fmt: skip
        "unavailable": s.scalar(
            select(func.count(MediaItem.id))
            .join(LibraryRootRow)
            .where(MediaItem.available.is_(False), in_scope())
        )
        or 0,  # fmt: skip
    }


# --------------------------------------------------------------- photos


def photo_json(i: MediaItem) -> dict:
    return {"id": i.id, "kind": "photo", "title": i.title, "album": i.album or "",
            "album_key": encode_key(i.album or ""), "taken_at": iso(i.recorded_at),
            "width": i.width, "height": i.height,
            "thumb": photo_url(i, "thumb"), "tv": photo_url(i, "tv"), "screen": photo_url(i, "screen"),
            "original": photo_url(i, "original")}  # fmt: skip


def photo_albums(s: Session) -> list[dict]:
    """One entry per folder of photos, newest first (by the latest photo)."""
    album = func.coalesce(MediaItem.album, "")
    q = (
        visible()
        .with_only_columns(
            album,
            func.count(MediaItem.id),
            func.min(MediaItem.recorded_at),
            func.max(MediaItem.recorded_at),
        )  # fmt: skip
        .where(MediaItem.kind == "photo")
        .group_by(album)
        .order_by(func.max(MediaItem.recorded_at).desc())
    )
    out = []
    for name, n, first, last in s.execute(q):
        cover = s.scalar(visible().where(MediaItem.kind == "photo", album == name)
                         .order_by(MediaItem.recorded_at, MediaItem.rel_path).limit(1))  # fmt: skip
        out.append({"key": encode_key(name), "title": name.rsplit("/", 1)[-1] or "Photos",
                    "path": name, "photos": n, "from": iso(first), "to": iso(last),
                    "art": {"poster": photo_url(cover, "thumb"),
                            "backdrop": photo_url(cover, "screen")} if cover else {}})  # fmt: skip
    return out


def album_photos(s: Session, name: str) -> list[MediaItem]:
    q = visible().where(MediaItem.kind == "photo", func.coalesce(MediaItem.album, "") == name)
    return list(s.scalars(q.order_by(MediaItem.recorded_at, MediaItem.rel_path)))


# --------------------------------------------------------------- music by folder
# Artists, albums and songs the way the files are organised on disk: an
# artist is a top-level folder of a music library, an album a folder inside
# it (everything below, e.g. CD1/CD2, belongs to it). Tracks loose in an
# artist folder form that artist's "Other tracks"; tracks at a library's top
# level are grouped under "Other tracks" too. Same-named folders in several
# libraries are merged. Built from paths only, cached until the library changes.

LOOSE = "Other tracks"
_tree_cache: dict[bool, dict] = {}  # per scope (kids only or not): {"stamp", "tree"}


def _music_tree(s: Session) -> dict:
    q = visible().where(MediaItem.kind == "track")
    stamp = s.execute(
        q.with_only_columns(func.count(MediaItem.id), func.max(MediaItem.updated_at))
    ).one()
    cache = _tree_cache.setdefault(SCOPE.get().kids_only, {"stamp": None, "tree": None})
    if cache["stamp"] == tuple(stamp):
        return cache["tree"]
    rows = s.execute(q.with_only_columns(MediaItem.id, MediaItem.rel_path, MediaItem.year,
                                         MediaItem.added_at, MediaItem.disc_no, MediaItem.track_no)
                     ).all()  # fmt: skip
    artists: dict[str, dict] = {}
    for tid, rel, year, added, disc, trk in rows:
        parts = rel.split("/")
        artist = parts[0] if len(parts) >= 2 else LOOSE
        album = parts[1] if len(parts) >= 3 else LOOSE
        a = artists.setdefault(artist.casefold(), {"name": artist, "albums": {}, "added": added})
        a["added"] = max(a["added"], added) if a["added"] and added else (a["added"] or added)
        al = a["albums"].setdefault(album.casefold(), {"name": album, "tracks": [], "year": None,
                                                        "added": added})  # fmt: skip
        al["tracks"].append((disc or 0, trk or 0, rel.casefold(), tid))
        al["year"] = max(al["year"] or 0, year or 0) or None
        al["added"] = max(al["added"], added) if al["added"] and added else (al["added"] or added)
    for a in artists.values():
        for al in a["albums"].values():
            al["tracks"].sort()
            name, year = split_year(al["name"]) if al["name"] != LOOSE else (al["name"], None)
            al["title"], al["year"] = name or al["name"], year or al["year"]
    cache.update(stamp=tuple(stamp), tree=artists)
    return artists


def _sort_name(name: str) -> str:
    n = name.casefold()
    return ("￿" + n) if name == LOOSE else (n[4:] if n.startswith("the ") else n)


def _album_json(artist: dict, al: dict) -> dict:
    cover = al["tracks"][0][3]
    return {"key": encode_key(artist["name"], al["name"]), "title": al["title"], "folder": al["name"],
            "artist": artist["name"], "artist_key": encode_key(artist["name"]), "year": al["year"],
            "tracks": len(al["tracks"]), "added_at": iso(al["added"]),
            "art": {"poster": f"/api/items/{cover}/art/poster"}}  # fmt: skip


def _sorted_albums(albums: list[dict], sort: str) -> list[dict]:
    if sort == "added":
        return sorted(albums, key=lambda x: x["added_at"] or "", reverse=True)
    if sort == "year":
        return sorted(albums, key=lambda x: (-(x["year"] or 0), _sort_name(x["title"])))
    return sorted(albums, key=lambda x: (_sort_name(x["title"]), _sort_name(x["artist"])))


def music_artists(s: Session, sort: str = "az") -> list[dict]:
    out = []
    for a in _music_tree(s).values():
        albums = sorted(
            a["albums"].values(), key=lambda al: (al["name"] == LOOSE, -(al["year"] or 0))
        )
        out.append({"key": encode_key(a["name"]), "name": a["name"], "albums": sum(
            1 for al in a["albums"].values() if al["name"] != LOOSE),
            "tracks": sum(len(al["tracks"]) for al in a["albums"].values()),
            "added_at": iso(a["added"]),
            "art": {"poster": f"/api/items/{albums[0]['tracks'][0][3]}/art/poster"}})  # fmt: skip
    if sort == "added":
        return sorted(out, key=lambda x: x["added_at"] or "", reverse=True)
    return sorted(out, key=lambda x: _sort_name(x["name"]))


def music_artist(s: Session, name: str) -> dict | None:
    a = _music_tree(s).get(name.casefold())
    if a is None:
        return None
    albums = [_album_json(a, al) for al in a["albums"].values() if al["name"] != LOOSE]
    loose = a["albums"].get(LOOSE.casefold())
    return {"key": encode_key(a["name"]), "name": a["name"],
            "albums": sorted(albums, key=lambda x: (-(x["year"] or 0), _sort_name(x["title"]))),
            "loose_ids": [t[3] for t in loose["tracks"]] if loose else []}  # fmt: skip


def music_albums(s: Session, sort: str = "az") -> list[dict]:
    return _sorted_albums([_album_json(a, al) for a in _music_tree(s).values()
                           for al in a["albums"].values()], sort)  # fmt: skip


def music_album(s: Session, artist: str, album: str) -> tuple[dict, list[int]] | None:
    a = _music_tree(s).get(artist.casefold())
    al = a["albums"].get(album.casefold()) if a else None
    if al is None:
        return None
    return _album_json(a, al), [t[3] for t in al["tracks"]]
