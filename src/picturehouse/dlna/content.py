"""ContentDirectory Browse: the folder tree TVs see, rendered as DIDL-Lite.

Object ids:

    0                      root
    films | tv | music | videos
    show.<key>             a series      -> seasons
    season.<key>.<n>       a season      -> episodes
    album.<key>            an album      -> tracks
    item.<id>              a playable file
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy.orm import Session

from picturehouse.db.models import MediaItem
from picturehouse.streaming.decide import decide_dlna, mime_of
from picturehouse.web import queries as Q

DIDL_OPEN = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" '
    'xmlns:sec="http://www.sec.co.kr/">'
)
DIDL_CLOSE = "</DIDL-Lite>"

FLAGS_STREAM = "01700000000000000000000000000000"  # streaming, bg, stall, DLNA 1.5
FLAGS_TRANSCODE = "01500000000000000000000000000000"  # streaming, bg, DLNA 1.5
TOP = [("films", "Films"), ("tv", "TV Shows"), ("music", "Music"), ("videos", "Home Videos")]


class NoSuchObject(Exception):
    pass


@dataclass
class Container:
    id: str
    parent: str
    title: str
    child_count: int
    upnp_class: str = "object.container.storageFolder"
    art_url: str | None = None


def content_features(direct: bool) -> str:
    if direct:
        return f"DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS={FLAGS_STREAM}"
    return f"DLNA.ORG_OP=10;DLNA.ORG_CI=1;DLNA.ORG_FLAGS={FLAGS_TRANSCODE}"


def fmt_duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{ms:03d}"


def resources(
    item: MediaItem, base: str, max_bitrate: int = 0
) -> list[tuple[str, str, dict[str, str]]]:
    """(url, protocolInfo, attrs) for each way the item can be fetched; the
    preferred one first. TVs generally take the first they can handle."""
    d = decide_dlna(item, max_bitrate)
    attrs: dict[str, str] = {}
    if dur := fmt_duration(item.duration):
        attrs["duration"] = dur
    if item.width and item.height:
        attrs["resolution"] = f"{item.width}x{item.height}"
    if item.channels:
        attrs["nrAudioChannels"] = str(item.channels)
    direct = (
        f"{base}/dlna/media/{item.id}",
        f"http-get:*:{_dlna_mime(item)}:{content_features(True)}",
        {**attrs, "size": str(item.size)}
        | ({"bitrate": str(item.bitrate // 8)} if item.bitrate else {}),  # fmt: skip
    )
    if item.media_type == "audio":
        trans = (f"{base}/dlna/media/{item.id}/transcode.mp3",
                 f"http-get:*:audio/mpeg:{content_features(False)}", attrs)  # fmt: skip
    else:
        trans = (f"{base}/dlna/media/{item.id}/transcode.ts",
                 f"http-get:*:video/mpeg:{content_features(False)}", attrs)  # fmt: skip
    return [direct, trans] if d.mode == "direct" else [trans, direct]


def _dlna_mime(item: MediaItem) -> str:
    mime = mime_of(item)
    # Several TVs (Samsung especially) only recognise these spellings.
    return {"video/x-matroska": "video/x-mkv", "video/mp2t": "video/mpeg",
            "audio/flac": "audio/x-flac"}.get(mime, mime)  # fmt: skip


def item_didl(item: MediaItem, parent: str, base: str, max_bitrate: int = 0) -> str:
    cls = {
        "film": "object.item.videoItem.movie",
        "episode": "object.item.videoItem",
        "home_video": "object.item.videoItem",
        "track": "object.item.audioItem.musicTrack",
    }.get(
        item.kind,
        "object.item.videoItem" if item.media_type == "video" else "object.item.audioItem",
    )
    title = item.title
    if item.kind == "episode" and item.episode is not None:
        ep = f"{item.episode:02d}" + (f"-{item.episode_end:02d}" if item.episode_end else "")
        title = f"{ep}. {item.title}"
    elif item.kind == "film" and item.year:
        title = f"{item.title} ({item.year})"
    elif item.kind == "track" and item.track_no:
        title = f"{item.track_no:02d}. {item.title}"
    parts = [f"<item id={quoteattr(f'item.{item.id}')} parentID={quoteattr(parent)} restricted=\"1\">",
             f"<dc:title>{escape(title)}</dc:title>", f"<upnp:class>{cls}</upnp:class>"]  # fmt: skip
    if item.year:
        parts.append(f"<dc:date>{item.year:04d}-01-01</dc:date>")
    if item.overview:
        # Different TVs read different fields; both are standard.
        parts += [f"<dc:description>{escape(item.overview[:1000])}</dc:description>",
                  f"<upnp:longDescription>{escape(item.overview)}</upnp:longDescription>"]  # fmt: skip
    for g in (item.genres or "").split(", "):
        if g:
            parts.append(f"<upnp:genre>{escape(g)}</upnp:genre>")
    if item.kind == "track":
        artist = item.artist or item.album_artist
        if artist:
            parts += [f"<upnp:artist>{escape(artist)}</upnp:artist>",
                      f"<dc:creator>{escape(artist)}</dc:creator>"]  # fmt: skip
        if item.album:
            parts.append(f"<upnp:album>{escape(item.album)}</upnp:album>")
        if item.track_no:
            parts.append(f"<upnp:originalTrackNumber>{item.track_no}</upnp:originalTrackNumber>")
    if item.kind == "episode":
        if item.show:
            parts.append(f"<upnp:seriesTitle>{escape(item.show)}</upnp:seriesTitle>")
        if item.episode is not None:
            parts.append(f"<upnp:episodeNumber>{item.episode}</upnp:episodeNumber>")
    art = "poster" if item.kind in ("film", "track") else "thumb"
    parts.append(f'<upnp:albumArtURI dlna:profileID="JPEG_TN">'
                 f"{escape(base + Q.art_url(item, art))}</upnp:albumArtURI>")  # fmt: skip
    for url, proto, attrs in resources(item, base, max_bitrate):
        a = "".join(f" {k}={quoteattr(v)}" for k, v in attrs.items())
        parts.append(f"<res protocolInfo={quoteattr(proto)}{a}>{escape(url)}</res>")
    parts.append("</item>")
    return "".join(parts)


def container_didl(c: Container) -> str:
    art = f"<upnp:albumArtURI>{escape(c.art_url)}</upnp:albumArtURI>" if c.art_url else ""
    return (f"<container id={quoteattr(c.id)} parentID={quoteattr(c.parent)} restricted=\"1\" "
            f"searchable=\"0\" childCount=\"{c.child_count}\"><dc:title>{escape(c.title)}"
            f"</dc:title><upnp:class>{c.upnp_class}</upnp:class>{art}</container>")  # fmt: skip


# --------------------------------------------------------------------------


class ContentDirectory:
    def __init__(self, base: str, max_bitrate: int = 0) -> None:
        self.base = base  # http://ip:port the client used to reach us
        self.max_bitrate = max_bitrate  # bits/s; bigger videos are converted (0 = off)

    # Each node lists its children as Containers and/or MediaItems.

    def _top_counts(self, s: Session) -> dict[str, int]:
        c = Q.counts(s)
        return {"films": c["films"], "tv": c["shows"], "music": c["albums"],
                "videos": c["home_videos"]}  # fmt: skip

    def _root_children(self, s: Session) -> list[Container]:
        counts = self._top_counts(s)
        shown = [(k, t) for k, t in TOP if counts[k]] or TOP
        return [Container(k, "0", t, counts[k]) for k, t in shown]

    def _kind_items(self, s: Session, kind: str) -> list[MediaItem]:
        q = Q.sort_items(Q.visible().where(MediaItem.kind == kind), "az")
        return list(s.scalars(q))

    def _shows(self, s: Session) -> list[Container]:
        return [Container(f"show.{r['key']}", "tv", r["title"], r["seasons"],
                          art_url=f"{self.base}{r['art']['poster']}")
                for r in Q.shows(s, "az")]  # fmt: skip

    def _seasons(self, s: Session, key: str) -> list[Container]:
        show = _one(Q.decode_key(key))
        eps = Q.show_episodes(s, show)
        if not eps:
            raise NoSuchObject
        seasons: dict[int, int] = {}
        for e in eps:
            seasons[e.season if e.season is not None else -1] = seasons.get(
                e.season if e.season is not None else -1, 0) + 1  # fmt: skip
        order = sorted(seasons, key=lambda n: (n <= 0, n))
        return [Container(f"season.{key}.{n}", f"show.{key}",
                          "Specials" if n == 0 else "Other" if n < 0 else f"Season {n}",
                          seasons[n], art_url=f"{self.base}{Q.art_url(eps[0], 'poster')}")
                for n in order]  # fmt: skip

    def _season_items(self, s: Session, key: str, n: int) -> list[MediaItem]:
        show = _one(Q.decode_key(key))
        eps = [e for e in Q.show_episodes(s, show)
               if (e.season if e.season is not None else -1) == n]  # fmt: skip
        if not eps:
            raise NoSuchObject
        return eps

    def _albums(self, s: Session) -> list[Container]:
        return [Container(f"album.{r['key']}", "music", f"{r['title']} — {r['artist']}",
                          r["tracks"], "object.container.album.musicAlbum",
                          art_url=f"{self.base}{r['art']['poster']}")
                for r in Q.albums(s, "az")]  # fmt: skip

    def _album_items(self, s: Session, key: str) -> list[MediaItem]:
        parts = Q.decode_key(key)
        if len(parts) != 2:
            raise NoSuchObject
        tracks = Q.album_tracks(s, *parts)
        if not tracks:
            raise NoSuchObject
        return tracks

    def children(self, s: Session, oid: str) -> list[Container | MediaItem]:
        if oid == "0":
            return list(self._root_children(s))
        if oid == "films":
            return list(self._kind_items(s, "film"))
        if oid == "videos":
            return list(self._kind_items(s, "home_video"))
        if oid == "tv":
            return list(self._shows(s))
        if oid == "music":
            return list(self._albums(s))
        if oid.startswith("show."):
            return list(self._seasons(s, oid[5:]))
        if oid.startswith("season."):
            key, _, n = oid[7:].rpartition(".")
            try:
                return list(self._season_items(s, key, int(n)))
            except ValueError as e:
                raise NoSuchObject from e
        if oid.startswith("album."):
            return list(self._album_items(s, oid[6:]))
        raise NoSuchObject

    def parent_of(self, oid: str) -> str:
        if oid in ("films", "tv", "music", "videos"):
            return "0"
        if oid.startswith("show."):
            return "tv"
        if oid.startswith("season."):
            return "show." + oid[7:].rpartition(".")[0]
        if oid.startswith("album."):
            return "music"
        return "-1"

    def _item_parent(self, item: MediaItem) -> str:
        if item.kind == "film":
            return "films"
        if item.kind == "home_video":
            return "videos"
        if item.kind == "episode" and item.show:
            n = item.season if item.season is not None else -1
            return f"season.{Q.encode_key(item.show)}.{n}"
        if item.kind == "track":
            return f"album.{Q.encode_key(item.album_artist or item.artist or '', item.album or '')}"
        return "0"

    # ----------------------------------------------------------------- Browse

    def browse(
        self, s: Session, oid: str, flag: str, start: int, count: int
    ) -> tuple[str, int, int]:
        """Returns (DIDL, number returned, total matches)."""
        if flag == "BrowseMetadata":
            return self._metadata(s, oid), 1, 1
        kids = self.children(s, oid)
        total = len(kids)
        page = kids[start:] if count == 0 else kids[start : start + count]
        body = []
        for k in page:
            if isinstance(k, Container):
                body.append(container_didl(k))
            else:
                body.append(item_didl(k, oid, self.base, self.max_bitrate))
        return DIDL_OPEN + "".join(body) + DIDL_CLOSE, len(page), total

    def _metadata(self, s: Session, oid: str) -> str:
        if oid == "0":
            c = Container("0", "-1", "Root", len(self._root_children(s)))
            return DIDL_OPEN + container_didl(c) + DIDL_CLOSE
        if oid.startswith("item."):
            try:
                item = s.get(MediaItem, int(oid[5:]))
            except ValueError:
                item = None
            if item is None or not item.available or not item.probe_ok:
                raise NoSuchObject
            return (DIDL_OPEN + item_didl(item, self._item_parent(item), self.base, self.max_bitrate)
                    + DIDL_CLOSE)  # fmt: skip
        parent = self.parent_of(oid)
        siblings = self.children(s, parent) if parent != "-1" else []
        match = next((c for c in siblings if isinstance(c, Container) and c.id == oid), None)
        if match is None:
            raise NoSuchObject
        return DIDL_OPEN + container_didl(match) + DIDL_CLOSE


def _one(parts: list[str]) -> str:
    if len(parts) != 1:
        raise NoSuchObject
    return parts[0]
