"""FastAPI application: JSON API for the web console, media streaming, static UI.

The contract is documented in docs/api.md. DLNA routes are mounted from
picturehouse.dlna when DLNA is enabled.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import json
import logging
import mimetypes
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from picturehouse import __version__
from picturehouse.db.models import (
    Device,
    LibraryRootRow,
    MediaItem,
    PlaybackState,
    ShowMeta,
    is_hdr,
    utcnow,
)
from picturehouse.errors import PictureHouseError
from picturehouse.library.photos import WEB_SAFE
from picturehouse.library.roots import add_root, remove_root, set_config_values
from picturehouse.log import LOG_FILE_NAME
from picturehouse.metadata.api_client import MetadataApiError
from picturehouse.paths import long_path, resolve_in_root
from picturehouse.services import Services
from picturehouse.streaming import images, procs
from picturehouse.streaming import levels as levels_mod
from picturehouse.streaming.artwork import find_artwork
from picturehouse.streaming.decide import decide_browser, decide_dlna, mime_of
from picturehouse.streaming.hls import HlsError, HlsManager
from picturehouse.streaming.hls import playlist as hls_playlist_text
from picturehouse.themes import ThemeStore, render_css
from picturehouse.themes.engine import ASSET_TYPES as THEME_ASSET_TYPES
from picturehouse.tls import ca_fingerprint
from picturehouse.web import access
from picturehouse.web import queries as Q

log = logging.getLogger(__name__)
# Every DLNA request a TV makes, for diagnosing device quirks (Library -> logs).
dlna_log = logging.getLogger("picturehouse.dlna.requests")

Sort = Literal["added", "az", "year"]
STATIC_DIR = Path(str(files("picturehouse.web").joinpath("static")))
# So phones offer to install the APK rather than show "unknown file".
mimetypes.add_type("application/vnd.android.package-archive", ".apk")


# Request bodies live at module level: with postponed annotations FastAPI
# can't resolve classes defined inside create_app(), and silently ignores them.


class Progress(BaseModel):
    position: float
    completed: bool = False


class NewLibrary(BaseModel):
    name: str
    path: str
    kind: Literal["films", "tv", "music", "home_videos", "photos", "mixed"] = "mixed"


class MatchRequest(BaseModel):
    force: bool = False


class FixMatch(BaseModel):
    tmdb_id: int | None = None
    imdb_id: str | None = Field(None, pattern=r"^tt\d{5,10}$")  # an OMDb pick


class MetadataEdit(BaseModel):
    title: str | None = None
    year: int | None = None
    overview: str | None = None
    genres: str | None = None
    certification: str | None = None


class MetadataSettings(BaseModel):
    tmdb_api_key: str | None = None
    omdb_api_key: str | None = None
    language: str | None = None
    region: str | None = None
    match_on_scan: bool | None = None
    save_local: bool | None = None


def remote_art(s, item: MediaItem) -> dict[str, str | None]:
    """Online artwork URLs for an item; episodes use their series' poster/backdrop."""
    if item.kind == "episode" and item.parsed_show:
        meta = s.get(ShowMeta, item.parsed_show)
        return {"poster": meta.poster_url if meta else None,
                "backdrop": meta.backdrop_url if meta else None, "thumb": item.poster_url}  # fmt: skip
    return {"poster": item.poster_url, "backdrop": item.backdrop_url, "thumb": None}


class ThemeSetting(BaseModel):
    default: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")


class StreamingSettings(BaseModel):
    tv_max_bitrate_mbps: float | None = Field(None, ge=0, le=200)  # 0 = off
    max_transcodes: int | None = Field(None, ge=0, le=16)  # 0 = no limit


class AudioCheckRequest(BaseModel):
    force: bool = False
    item_ids: list[int] | None = None


class AdminLogin(BaseModel):
    pin: str = Field(max_length=64)


class AdminPin(BaseModel):
    pin: str | None = Field(None, max_length=64)  # None removes the PIN


class ScanRequest(BaseModel):
    libraries: list[str] | None = None
    full: bool = False


@functools.lru_cache(maxsize=8)
def _icon(size: int) -> bytes:
    from picturehouse.dlna.xmldocs import icon_png

    return icon_png(size)


class RevalidatingStaticFiles(StaticFiles):
    """Static files that browsers must revalidate (cheap 304s via ETag), so an
    updated console is picked up on the next load instead of a stale cache."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# --------------------------------------------------------------------------
# Device tracking (throttled writes)
# --------------------------------------------------------------------------


class DeviceTracker:
    def __init__(self, svc: Services, min_interval: float = 60.0) -> None:
        self.svc = svc
        self.min_interval = min_interval
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def seen(self, address: str, user_agent: str, via: str) -> None:
        key = (address, user_agent[:300])
        now = time.monotonic()
        with self._lock:
            if now - self._last.get(key, -1e9) < self.min_interval:
                return
            self._last[key] = now
        try:
            with self.svc.db.session() as s:
                dev = s.scalar(select(Device).where(Device.address == key[0],
                                                    Device.user_agent == key[1]))  # fmt: skip
                if dev is None:
                    s.add(Device(address=key[0], user_agent=key[1], via=via))
                    log.info(
                        "new client seen", extra={"address": address, "ua": key[1], "via": via}
                    )
                else:
                    dev.last_seen, dev.via = utcnow(), via
        except Exception:
            log.exception("could not record device")


def busy_response(e: procs.TranscodeBusy) -> JSONResponse:
    """503 + Retry-After: every transcode slot is taken (see streaming/procs.py)."""
    return JSONResponse({"detail": str(e), "busy": True, "limit": e.limit}, 503,
                        headers={"Retry-After": "30"})  # fmt: skip


def device_name(user_agent: str) -> str:
    ua = user_agent.lower()
    for needle, name in [
        ("samsung", "Samsung TV"), ("lg", "LG TV"), ("webos", "LG TV"), ("sony", "Sony TV"),
        ("bravia", "Sony TV"), ("panasonic", "Panasonic TV"), ("philips", "Philips TV"),
        ("vlc", "VLC"), ("bubbleupnp", "BubbleUPnP"), ("kodi", "Kodi"), ("xbox", "Xbox"),
        ("playstation", "PlayStation"), ("windows-media-player", "Windows Media Player"),
        ("roku", "Roku"), ("android", "Android device"), ("iphone", "iPhone"),
        ("ipad", "iPad"), ("edg/", "Edge browser"), ("firefox", "Firefox"),
        ("chrome", "Chrome browser"), ("safari", "Safari"),
    ]:  # fmt: skip
        if needle in ua:
            return name
    return user_agent.split("/")[0][:40] or "Unknown device"


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------


def create_app(svc: Services, *, dlna=None, https: dict | None = None) -> FastAPI:
    """``https``: {"port": int, "files": TlsFiles} when the HTTPS listener runs."""
    started_at = datetime.now(UTC)
    tracker = DeviceTracker(svc)
    hls_mgr = HlsManager(svc.ffmpeg.path, svc.layout.transcode_dir / "hls")
    procs.slots.limit = svc.settings.ffmpeg.max_transcodes
    themes = ThemeStore(svc.layout.themes_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        hls_mgr.shutdown()
        procs.kill_all()

    app = FastAPI(title="picturehouse", version=__version__, lifespan=lifespan)
    app.state.svc = svc
    app.state.hls = hls_mgr
    app.state.tracker = tracker
    gate = access.AdminGate(svc.layout.root)
    app.state.admin_gate = gate
    api = APIRouter(prefix="/api")

    def client_host(request: Request) -> str:
        return request.client.host if request.client else ""

    def on_this_pc(request: Request) -> bool:
        return access.is_loopback(client_host(request)) and access.host_is_loopback(
            request.headers.get("host")
        )

    def is_admin(request: Request) -> bool:
        if on_this_pc(request):
            return True
        return gate.pin_set and gate.check_cookie(request.cookies.get(access.COOKIE))

    @app.middleware("http")
    async def admin_guard(request: Request, call_next):
        """Other devices may browse and play; changes need this PC or the PIN
        (see web/access.py). Media and DLNA paths skip all of this."""
        path, method = request.url.path, request.method
        if path.startswith("/api/"):
            h = request.headers
            if not access.origin_ok(method, h.get("origin"), h.get("referer"), h.get("host")):
                return JSONResponse({"detail": "request came from another website"}, 403)
            if access.needs_admin(method, path) and not is_admin(request):
                detail = ("enter the admin PIN to change this" if gate.pin_set
                          else "only allowed on the PC running PictureHouse")  # fmt: skip
                return JSONResponse({"detail": detail, "admin_required": True,
                                     "pin_set": gate.pin_set}, 403)  # fmt: skip
        return await call_next(request)

    @app.middleware("http")
    async def media_cors(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/hls/") or path.endswith(("/file", "/hls.m3u8", "/stream.mp4")):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range"
        return response

    @app.middleware("http")
    async def track_clients(request: Request, call_next):
        path = request.url.path
        if request.client and (path.startswith("/dlna/") or path.endswith(("/file", "/play"))):
            via = "dlna" if path.startswith("/dlna/") else "web"
            await anyio.to_thread.run_sync(
                tracker.seen, request.client.host, request.headers.get("user-agent", ""), via
            )
        if not path.startswith("/dlna/"):
            return await call_next(request)
        t0 = time.monotonic()
        response = await call_next(request)
        h = request.headers
        dlna_log.info(
            "%s %s -> %s", request.method, path, response.status_code,
            extra={
                "client": request.client.host if request.client else None,
                "ua": h.get("user-agent", ""),
                "range": h.get("range"),
                "timeseek": h.get("timeseekrange.dlna.org"),
                "soapaction": h.get("soapaction"),
                "content_features_requested": h.get("getcontentfeatures.dlna.org"),
                "caption_requested": h.get("getcaptioninfo.sec"),
                "ms": round((time.monotonic() - t0) * 1000),
            },
        )  # fmt: skip
        return response

    def get_item(s, item_id: int) -> MediaItem:
        item = s.get(MediaItem, item_id)
        if item is None:
            raise HTTPException(404, "no such item")
        return item

    def item_path(item: MediaItem) -> Path:
        return long_path(resolve_in_root(Path(item.root.path), item.rel_path).absolute())

    # ------------------------------------------------------------ status

    @api.get("/hello")
    def hello():
        """ "Is this PictureHouse?" for the LG TV app, which scans the network
        from its own (file://) origin: hence CORS, and nothing private."""
        return JSONResponse({"app": "picturehouse", "name": svc.settings.server.friendly_name,
                             "version": __version__, "tv": "/tv/"},
                            headers={"Access-Control-Allow-Origin": "*"})  # fmt: skip

    @api.get("/status")
    def status(request: Request):
        with svc.db.session() as s:
            counts = Q.counts(s)
            total = s.scalar(
                select(func.sum(MediaItem.size)).join(LibraryRootRow)
                .where(MediaItem.available, LibraryRootRow.configured)
            ) or 0  # fmt: skip
        return {
            "server": {
                "name": svc.settings.server.friendly_name,
                "version": __version__,
                "started_at": started_at.isoformat(),
                # Chromecasts get plain-http media URLs (they'll never trust our CA).
                "http_port": svc.settings.server.http_port,
                # Who started this server ("desktop" = the PictureHouse app, which
                # replaces one left over from an earlier run) and its process id.
                "launched_by": os.environ.get("PICTUREHOUSE_LAUNCHED_BY", "terminal"),
                "pid": os.getpid(),
                # Where media lives as the server sees it (a container's mount
                # point): the console's "Add folder" form suggests it.
                "media_hint": os.environ.get("PICTUREHOUSE_MEDIA_HINT") or None,
            },  # fmt: skip
            "scan": scan_state(),
            "counts": counts,
            "total_bytes": total,
            "dlna": {
                "enabled": dlna is not None,
                "addresses": dlna.description_urls() if dlna else [],
            },  # fmt: skip
            "https": https_state(),
            # The Android app, if it was built into the console (Android APK/build_android.py).
            "android_apk": (STATIC_DIR / "downloads" / "PictureHouse.apk").is_file(),
            "streams_active": procs.active_count(),
            "hls_encoders": hls_mgr.active_jobs,
            "transcodes": {"limit": procs.slots.limit, "active": procs.slots.holders()},
            "metadata": metadata_state(),
            # What this device may do (see web/access.py).
            "access": admin_state(request),
        }

    # ------------------------------------------------------------ browse

    # Lists take ``offset``/``limit`` (and report ``total``) so the TV can load
    # a page at a time; without ``limit`` they return everything, as before.
    Offset = Query(0, ge=0)
    Limit = Query(None, ge=1, le=1000)

    def list_kind(kind: str, sort: str, offset: int = 0, limit: int | None = None):
        with svc.db.session() as s:
            q = Q.visible().where(MediaItem.kind == kind)
            total = s.scalar(q.with_only_columns(func.count(MediaItem.id))) or 0
            page = Q.sort_items(q, sort).offset(offset)
            if limit:
                page = page.limit(limit)
            return {"items": Q.items_json(s, list(s.scalars(page))), "total": total}

    def page_of(rows: list, offset: int, limit: int | None) -> list:
        return rows[offset : offset + limit] if limit else rows[offset:]

    @api.get("/films")
    def films(sort: Sort = "added", offset: int = Offset, limit: int | None = Limit):
        return list_kind("film", sort, offset, limit)

    @api.get("/home-videos")
    def home_videos(sort: Sort = "added", offset: int = Offset, limit: int | None = Limit):
        return list_kind("home_video", sort, offset, limit)

    @api.get("/shows")
    def shows(sort: Sort = "added", offset: int = Offset, limit: int | None = Limit):
        with svc.db.session() as s:
            rows = Q.strip_private(Q.shows(s, sort))
        return {"shows": page_of(rows, offset, limit), "total": len(rows)}

    @api.get("/shows/{key}")
    def show(key: str):
        parts = Q.decode_key(key)
        if len(parts) != 1:
            raise HTTPException(404, "no such show")
        with svc.db.session() as s:
            rows = Q.shows(s, where=MediaItem.show == parts[0])
            if not rows:
                raise HTTPException(404, "no such show")
            eps = Q.show_episodes(s, parts[0])
            ej = Q.items_json(s, eps)
        seasons: dict[int, list] = {}
        for e in ej:
            seasons.setdefault(e["season"] if e["season"] is not None else -1, []).append(e)
        order = sorted(seasons, key=lambda n: (n <= 0, n))  # specials last
        next_up = next((e for e in ej if e["progress"] and not e["progress"]["completed"]), None)
        if next_up is None:
            watched = [k for k, e in enumerate(ej) if e["progress"] and e["progress"]["completed"]]
            start = (watched[-1] + 1) if watched else 0
            regular = [e for e in ej[start:] if (e["season"] or 0) > 0] or ej[start:]
            next_up = regular[0] if regular else (ej[0] if ej else None)
        return {**Q.strip_private(rows)[0],
                "season_list": [{"season": n, "episodes": seasons[n]} for n in order],
                "next_up": next_up}  # fmt: skip

    @api.get("/albums")
    def albums(sort: Sort = "added", offset: int = Offset, limit: int | None = Limit):
        with svc.db.session() as s:
            rows = Q.strip_private(Q.albums(s, sort))
        return {"albums": page_of(rows, offset, limit), "total": len(rows)}

    # Music the way the folders are organised (Artist/Album/tracks): see
    # queries.music_*. The tag-based /api/albums stays for the console.
    def items_by_ids(s, ids: list[int]) -> list[dict]:
        found = {i.id: i for i in s.scalars(Q.visible().where(MediaItem.id.in_(ids)))}
        return Q.items_json(s, [found[i] for i in ids if i in found])

    @api.get("/music/artists")
    def music_artists(sort: Literal["az", "added"] = "az", offset: int = Offset,
                      limit: int | None = Limit):  # fmt: skip
        with svc.db.session() as s:
            rows = Q.music_artists(s, sort)
        return {"artists": page_of(rows, offset, limit), "total": len(rows)}

    @api.get("/music/artists/{key}")
    def music_artist(key: str):
        parts = Q.decode_key(key)
        with svc.db.session() as s:
            a = Q.music_artist(s, parts[0]) if len(parts) == 1 else None
            if a is None:
                raise HTTPException(404, "no such artist")
            return {"key": a["key"], "name": a["name"], "albums": a["albums"],
                    "tracks": items_by_ids(s, a["loose_ids"])}  # fmt: skip

    @api.get("/music/albums")
    def music_albums(sort: Sort = "az", offset: int = Offset, limit: int | None = Limit):
        with svc.db.session() as s:
            rows = Q.music_albums(s, sort)
        return {"albums": page_of(rows, offset, limit), "total": len(rows)}

    @api.get("/music/albums/{key}")
    def music_album(key: str):
        parts = Q.decode_key(key)
        with svc.db.session() as s:
            found = Q.music_album(s, *parts) if len(parts) == 2 else None
            if found is None:
                raise HTTPException(404, "no such album")
            album, ids = found
            return {**album, "track_list": items_by_ids(s, ids)}

    @api.get("/music/songs")
    def music_songs(offset: int = Offset, limit: int | None = Limit):
        with svc.db.session() as s:
            q = Q.visible().where(MediaItem.kind == "track")
            total = s.scalar(q.with_only_columns(func.count(MediaItem.id))) or 0
            page = q.order_by(MediaItem.sort_title, MediaItem.id).offset(offset)
            if limit:
                page = page.limit(limit)
            return {"items": Q.items_json(s, list(s.scalars(page))), "total": total}

    @api.get("/albums/{key}")
    def album(key: str):
        parts = Q.decode_key(key)
        if len(parts) != 2:
            raise HTTPException(404, "no such album")
        artist, title = parts
        with svc.db.session() as s:
            tracks = Q.album_tracks(s, artist, title)
            if not tracks:
                raise HTTPException(404, "no such album")
            rows = Q.albums(s, where=MediaItem.id.in_([t.id for t in tracks]))
            return {**Q.strip_private(rows)[0], "track_list": Q.items_json(s, tracks)}

    @api.get("/recent")
    def recent(limit: int = Query(20, ge=1, le=200)):
        with svc.db.session() as s:
            q = Q.visible().where(MediaItem.kind.in_(Q.VIDEO_KINDS))
            items = list(s.scalars(Q.sort_items(q, "added").limit(limit)))
            return {"items": Q.items_json(s, items)}

    @api.get("/continue")
    def continue_watching(limit: int = Query(20, ge=1, le=100), audiobooks: bool = False):
        """Started, not finished. ``audiobooks``: long tracks (over 20 min:
        audiobook chapters) too, for the TV app's Continue row."""
        kinds = MediaItem.media_type == "video"
        if audiobooks:
            kinds = kinds | ((MediaItem.kind == "track") & (MediaItem.duration > 20 * 60))
        with svc.db.session() as s:
            q = (Q.visible().join(PlaybackState, PlaybackState.item_id == MediaItem.id)
                 .where(PlaybackState.position > 5, PlaybackState.completed.is_(False), kinds)
                 .order_by(PlaybackState.updated_at.desc()).limit(limit))  # fmt: skip
            return {"items": Q.items_json(s, list(s.scalars(q)))}

    @api.get("/search")
    def search(q: str = Query("", max_length=200)):
        term = q.strip()
        empty = {k: [] for k in ("films", "episodes", "home_videos", "tracks", "shows", "albums")}
        if not term:
            return empty
        with svc.db.session() as s:

            def kind(k, *cols, limit=50):
                qq = Q.visible().where(MediaItem.kind == k, Q.text_match(term, *cols))
                return Q.items_json(s, list(s.scalars(Q.sort_items(qq, "az").limit(limit))))

            return {
                "films": kind("film", MediaItem.title),
                "episodes": kind("episode", MediaItem.title, limit=30),
                "home_videos": kind("home_video", MediaItem.title, MediaItem.rel_path),
                "tracks": kind("track", MediaItem.title, MediaItem.artist, limit=30),
                "shows": Q.strip_private(Q.shows(s, "az", Q.text_match(term, MediaItem.show))),
                "albums": Q.strip_private(Q.albums(s, "az", Q.text_match(
                    term, MediaItem.album, MediaItem.artist, MediaItem.album_artist))),
            }  # fmt: skip

    @api.get("/items/{item_id}")
    def item_detail(item_id: int):
        with svc.db.session() as s:
            i = get_item(s, item_id)
            prog = s.get(PlaybackState, item_id)
            data = Q.item_json(i, prog)
            streams = [{
                "index": st.index, "type": st.type, "codec": st.codec, "language": st.language,
                "title": st.title, "channels": st.channels, "default": st.is_default,
                "forced": st.is_forced,
            } for st in i.streams if not st.attached_pic and st.type in ("video", "audio", "subtitle")]  # fmt: skip
            b, d = decide_browser(i), decide_dlna(i)
            data.update({
                "library": i.root.name, "file": i.rel_path, "size": i.size,
                "container": i.container, "bitrate": i.bitrate,
                "video": {"codec": i.vcodec, "profile": i.vprofile, "width": i.width,
                          "height": i.height, "pix_fmt": i.pix_fmt, "fps": i.fps}
                if i.vcodec else None,
                "streams": streams,
                "playback": {"browser": {"mode": b.mode, "reason": b.reason},
                             "dlna": {"mode": d.mode, "reason": d.reason}},
                "probe": {"ok": i.probe_ok, "error": i.probe_error},
                "metadata": Q.metadata_json(i),
            })  # fmt: skip
            return data

    # ------------------------------------------------------------ playback

    @api.get("/items/{item_id}/play")
    def play(item_id: int, start: float = Query(0.0, ge=0),
             client: Literal["browser", "tv"] = "browser"):  # fmt: skip
        """``client=tv``: the LG TV app (webOS). Its player handles what the TV
        does over DLNA (HEVC, MKV, AC3, HDR), so it gets the original file
        whenever the DLNA profile allows, else HLS, which webOS plays natively."""
        with svc.db.session() as s:
            i = get_item(s, item_id)
            if not i.available or not i.probe_ok:
                raise HTTPException(409, "this file is not available right now")
            if client == "tv":
                return tv_play(i)
            d = decide_browser(i)
            if d.mode == "direct":
                url, mime, seekable = f"/api/items/{i.id}/file", mime_of(i), True
            else:
                url = f"/api/items/{i.id}/stream.mp4?start={start:g}"
                mime = "audio/mp4" if i.media_type == "audio" else "video/mp4"
                seekable = False
            hls_url = (f"/api/items/{i.id}/hls.m3u8"
                       if d.mode != "direct" and i.media_type == "video" and i.duration else None)  # fmt: skip
            return {"mode": d.mode, "reason": d.reason, "url": url, "mime": mime,
                    "duration": i.duration, "seekable": seekable, "start": 0 if seekable else start,
                    "hls_url": hls_url}  # fmt: skip

    def tv_play(i: MediaItem) -> dict:
        d = decide_dlna(i, int(svc.settings.dlna.max_bitrate_mbps * 1_000_000))
        if i.media_type == "audio" and d.mode != "direct":
            # Music/audiobooks the TV can't play as they are: an AAC stream
            # (restart it at ?start=N to seek).
            return {"mode": "transcode", "reason": f"{i.acodec} audio converted to AAC",
                    "seekable": False, "start": 0,
                    "url": f"/api/items/{i.id}/stream.mp4", "mime": "audio/mp4",
                    "duration": i.duration, "hls_url": None}  # fmt: skip
        hls = f"/api/items/{i.id}/hls.m3u8" if i.media_type == "video" and i.duration else None
        if d.mode == "direct" or hls is None:
            url, mime = f"/api/items/{i.id}/file", mime_of(i)
            mode = "direct" if d.mode == "direct" else "direct-try"  # no HLS: let the TV try
        else:
            url, mime, mode = hls, "application/vnd.apple.mpegurl", "transcode"
        return {"mode": mode, "reason": d.reason, "url": url, "mime": mime,
                "duration": i.duration, "seekable": True, "start": 0, "hls_url": hls}  # fmt: skip

    # HLS: the whole-file playlist plus on-demand segments (see streaming/hls.py).
    # Used by Safari/iOS, Chromecast and the console's player for transcodes.

    @api.get("/items/{item_id}/hls.m3u8")
    def hls_playlist(request: Request, item_id: int, audio: int | None = None,
                     c: str | None = None):  # fmt: skip
        """``audio``: stream index (default: the default-flagged audio track).
        ``c``: a player id, so two tabs never share (and close) one session."""
        with svc.db.session() as s:
            i = get_item(s, item_id)
            if i.media_type != "video" or not i.probe_ok:
                raise HTTPException(400, "HLS is for video files")
            path, duration, hdr = item_path(i), i.duration, is_hdr(i)
            who = f"{device_name(request.headers.get('user-agent', ''))}: {i.title}"
            if audio is None:
                tracks = [st for st in i.streams if st.type == "audio"]
                default = next(
                    (st for st in tracks if st.is_default), tracks[0] if tracks else None
                )
                audio = default.index if default else None
        if not path.is_file():
            raise HTTPException(404, "file is not reachable")
        try:
            sess = hls_mgr.open(item_id, path, duration or 0, audio, hdr=hdr, client=c, who=who)
        except procs.TranscodeBusy as e:
            return busy_response(e)
        except HlsError as e:
            raise HTTPException(409, str(e)) from e
        return Response(hls_playlist_text(sess, f"/api/hls/{sess.id}/{{k}}.ts"),
                        media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store", "X-HLS-Session": sess.id})  # fmt: skip

    @api.get("/hls/{sid}/{k}.ts")
    async def hls_segment(sid: str, k: int):
        sess = hls_mgr.get(sid)
        if sess is None:
            raise HTTPException(404, "HLS session expired; reload the playlist")
        try:
            path = await anyio.to_thread.run_sync(hls_mgr.segment, sess, k)
        except procs.TranscodeBusy as e:
            return busy_response(e)
        except HlsError as e:
            raise HTTPException(500 if "failed" in str(e) else 404, str(e)) from e
        return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "no-store"})

    @api.delete("/hls/{sid}", status_code=204)
    def hls_close(sid: str):
        hls_mgr.close(sid)
        return Response(status_code=204)

    @api.api_route("/items/{item_id}/file", methods=["GET", "HEAD"])
    def item_file(item_id: int):
        with svc.db.session() as s:
            i = get_item(s, item_id)
            path, mime = item_path(i), mime_of(i)
        if not path.is_file():
            raise HTTPException(404, "file is not reachable")
        return FileResponse(path, media_type=mime, filename=None,
                            headers={"Cache-Control": "no-store"})  # fmt: skip

    @api.get("/items/{item_id}/stream.mp4")
    def item_stream(request: Request, item_id: int, start: float = Query(0.0, ge=0),
                    audio: int | None = None):  # fmt: skip
        with svc.db.session() as s:
            i = get_item(s, item_id)
            path, d, audio_only = item_path(i), decide_browser(i), i.media_type == "audio"
            label = f"web:{i.id}:{d.mode}"
            who = f"{device_name(request.headers.get('user-agent', ''))}: {i.title}"
        if not path.is_file():
            raise HTTPException(404, "file is not reachable")
        lease = None
        if not audio_only and not d.copy_video:  # only video encodes are capped
            try:
                lease = procs.Lease(f"web:{client_host(request)}:{item_id}", who)
            except procs.TranscodeBusy as e:
                return busy_response(e)
        cmd = procs.fmp4_cmd(svc.ffmpeg.path, path, start=start, audio_only=audio_only,
                             copy_video=d.copy_video, copy_audio=d.copy_audio and audio is None,
                             audio_index=audio, hdr=d.hdr)  # fmt: skip
        return StreamingResponse(procs.stream_with_lease(cmd, label=label, lease=lease),
                                 media_type="audio/mp4" if audio_only else "video/mp4",
                                 headers={"Cache-Control": "no-store"})  # fmt: skip

    @api.put("/items/{item_id}/progress", status_code=204)
    def put_progress(item_id: int, body: Progress):
        with svc.db.session() as s:
            get_item(s, item_id)
            values = {"position": max(0.0, body.position), "completed": body.completed,
                      "updated_at": utcnow()}  # fmt: skip
            # Upsert: two first-time saves arriving together used to both INSERT.
            s.execute(
                sqlite_insert(PlaybackState)
                .values(item_id=item_id, **values)
                .on_conflict_do_update(index_elements=["item_id"], set_=values)
            )
        return Response(status_code=204)

    # ------------------------------------------------------------ photos

    @api.get("/photos")
    def photos():
        """Photo albums (one per folder), newest first."""
        with svc.db.session() as s:
            return {"albums": Q.photo_albums(s)}

    @api.get("/photos/recent")
    def recent_photos(limit: int = Query(60, ge=1, le=500)):
        with svc.db.session() as s:
            q = Q.visible().where(MediaItem.kind == "photo")
            items = s.scalars(q.order_by(MediaItem.recorded_at.desc()).limit(limit))
            return {"photos": [Q.photo_json(i) for i in items]}

    @api.get("/photos/all")
    def all_photos(order: Literal["taken", "folder"] = "taken"):
        """Every photo, for slideshows of the whole library. Compact (a big
        library is thousands of photos and TVs parse JSON slowly): rows of
        [id, version, title, taken date]; the photo's URLs are
        /api/items/{id}/photo?size=...&v={version}."""
        with svc.db.session() as s:
            q = (
                Q.visible()
                .where(MediaItem.kind == "photo")
                .with_only_columns(
                    MediaItem.id, MediaItem.mtime_ns, MediaItem.title, MediaItem.recorded_at
                )
            )
            q = q.order_by(MediaItem.recorded_at, MediaItem.rel_path) if order == "taken" else                 q.order_by(MediaItem.root_id, MediaItem.rel_path)  # fmt: skip
            rows = [[i, str(v), t, Q.iso(d)[:10] if d else None] for i, v, t, d in s.execute(q)]
        return {"fields": ["id", "v", "title", "taken"], "rows": rows}

    @api.get("/photos/albums/{key}")
    def photo_album(key: str):
        parts = Q.decode_key(key)
        if len(parts) != 1:
            raise HTTPException(404, "no such album")
        with svc.db.session() as s:
            items = Q.album_photos(s, parts[0])
            if not items:
                raise HTTPException(404, "no such album")
            return {"key": key, "title": parts[0].rsplit("/", 1)[-1] or "Photos",
                    "path": parts[0], "photos": [Q.photo_json(i) for i in items]}  # fmt: skip

    @api.get("/items/{item_id}/photo")
    def photo(item_id: int, size: Literal["thumb", "tv", "screen", "original"] = "screen"):
        """A picture: a cached thumbnail / screen-sized JPEG (upright, HEIC
        converted), or the original file when browsers can show it as is."""
        with svc.db.session() as s:
            i = get_item(s, item_id)
            if i.kind != "photo":
                raise HTTPException(404, "not a photo")
            path, mtime = item_path(i), i.mtime_ns
        if not path.is_file():
            raise HTTPException(404, "file is not reachable")
        cache = {"Cache-Control": "public, max-age=604800, immutable"}  # URLs carry ?v=mtime
        if size == "original" and path.suffix.lower() in WEB_SAFE:
            return FileResponse(path, headers=cache)
        try:
            out = images.rendition(path, svc.layout.artwork_dir / "photos", item_id, mtime,
                                   "screen" if size == "original" else size)  # fmt: skip
        except Exception as e:  # a corrupt picture mustn't 500 a whole grid noisily
            log.warning("photo rendition failed: %s", e, extra={"item": item_id})
            raise HTTPException(415, "this picture can't be shown") from e
        return FileResponse(out, media_type="image/jpeg", headers=cache)

    @api.get("/items/{item_id}/levels")
    def item_levels(item_id: int):
        """Precomputed visualiser levels for a track (see streaming/levels.py):
        {"fps", "bands", "data": base64 of frames x bands bytes}."""
        with svc.db.session() as s:
            i = get_item(s, item_id)
            if i.media_type != "audio":
                raise HTTPException(404, "not audio")
            path, mtime = item_path(i), i.mtime_ns
        if not path.is_file():
            raise HTTPException(404, "file is not reachable")
        try:
            data = levels_mod.levels(svc.ffmpeg.path, path, svc.layout.cache_dir / "levels",
                                     item_id, mtime)  # fmt: skip
        except levels_mod.LevelsError as e:
            log.info("visualiser levels failed: %s", e, extra={"item": item_id})
            raise HTTPException(422, "this track can't be analysed") from e
        return JSONResponse({"fps": levels_mod.FPS, "bands": len(levels_mod.BANDS),
                             "data": base64.b64encode(data).decode("ascii")},
                            headers={"Cache-Control": "public, max-age=86400"})  # fmt: skip

    @api.get("/items/{item_id}/art/{which}")
    def art(item_id: int, which: Literal["poster", "thumb", "backdrop"],
            w: int | None = Query(None, ge=64, le=2000)):  # fmt: skip
        """``w``: scaled down to about this width (cached), for grids."""
        with svc.db.session() as s:
            i = get_item(s, item_id)
            root = Path(i.root.path)
            try:
                found = find_artwork(i, root, which, svc.ffmpeg.path, svc.layout.artwork_dir,
                                     remote=remote_art(s, i))  # fmt: skip
            except Exception:
                log.exception("artwork lookup failed", extra={"item": item_id})
                found = None
        if found is None:
            raise HTTPException(404, "no artwork")
        if w:
            try:
                found = images.resized(found, svc.layout.artwork_dir / "sized", w)
            except Exception as e:  # odd format Pillow can't read: send the original
                log.info("artwork resize failed: %s", e, extra={"item": item_id})
        return FileResponse(found, headers={"Cache-Control": "public, max-age=3600"})

    # ------------------------------------------------------------ library management

    @api.get("/browse")
    def browse_folders(path: str | None = None):
        """Folders on the server, for the Add folder form's Browse button.
        Without ``path``: where to start. Admin only (access.ADMIN_READS)."""
        from dataclasses import asdict

        from picturehouse.library import browse

        if not path:
            return {"roots": [asdict(p) for p in browse.roots()]}
        try:
            return asdict(browse.list_dir(path))
        except browse.BrowseError as e:
            raise HTTPException(400, e.message) from None

    @api.get("/libraries")
    def libraries():
        with svc.db.session() as s:
            rows = s.execute(
                select(LibraryRootRow, func.count(MediaItem.id), func.sum(MediaItem.size))
                .outerjoin(MediaItem, (MediaItem.root_id == LibraryRootRow.id)
                           & MediaItem.available & MediaItem.probe_ok)
                .where(LibraryRootRow.configured)
                .group_by(LibraryRootRow.id).order_by(LibraryRootRow.name)
            ).all()  # fmt: skip
        return {"libraries": [
            {"name": r.name, "path": r.path, "kind": r.kind, "available": r.available,
             "unavailable_since": Q.iso(r.unavailable_since), "items": n, "bytes": b or 0,
             "last_scan_at": Q.iso(r.last_scan_at)}
            for r, n, b in rows]}  # fmt: skip

    @api.post("/libraries", status_code=201)
    def create_library(body: NewLibrary):
        try:
            add_root(svc.config_file, body.name.strip(), Path(body.path.strip()), body.kind)
            svc.reload_settings()
        except PictureHouseError as e:
            raise HTTPException(400, e.message) from e
        lib = next((x for x in libraries()["libraries"] if x["name"] == body.name.strip()), None)
        return lib

    @api.delete("/libraries/{name}", status_code=204)
    def delete_library(name: str):
        try:
            remove_root(svc.config_file, name)
            svc.reload_settings()
        except PictureHouseError as e:
            raise HTTPException(404, e.message) from e
        return Response(status_code=204)

    def scan_state() -> dict:
        p = svc.scanner.progress
        return {
            "running": p.running, "library": p.library, "done": p.done, "total": p.total,
            "current": p.current,
            "finished_at": datetime.fromtimestamp(p.finished_at, UTC).isoformat()
            if p.finished_at else None,
            "results": [
                {"library": r.library, "available": r.available, "new": r.new,
                 "changed": r.changed, "unchanged": r.unchanged, "failed": r.failed,
                 "missing": r.missing, "seconds": r.seconds} for r in p.results],
        }  # fmt: skip

    def start_scan(names: list[str] | None = None, full: bool = False) -> bool:
        return svc.scanner.start_in_background(names, full=full)

    app.state.start_scan = start_scan

    # ------------------------------------------------------------ metadata

    def metadata_state() -> dict:
        with svc.db.session() as s:

            def n(*conds):
                return s.scalar(select(func.count(MediaItem.id)).where(
                    MediaItem.available, MediaItem.probe_ok, *conds)) or 0  # fmt: skip

            attention = n(MediaItem.kind == "film",
                          MediaItem.match_status.in_(("ambiguous", "unmatched")))  # fmt: skip
            attention += s.scalar(select(func.count()).select_from(ShowMeta).where(
                ShowMeta.match_status.in_(("ambiguous", "unmatched")))) or 0  # fmt: skip
            return {
                "tmdb": svc.matcher.tmdb is not None,
                "omdb": svc.matcher.omdb is not None,
                "running": svc.matcher.running,
                "pending": n(MediaItem.kind.in_(("film", "episode")),
                             MediaItem.match_status == "pending"),
                "needs_attention": attention,
            }  # fmt: skip

    def start_match(force: bool = False, refresh: bool = False) -> bool:
        if svc.matcher.running:
            return False

        def run():
            try:
                svc.matcher.refresh() if refresh else svc.matcher.match(force=force)
            except Exception:
                log.exception("metadata matching failed")

        threading.Thread(target=run, name="match", daemon=True).start()
        return True

    app.state.start_match = start_match

    def start_audio_check(force: bool = False, item_ids: list[int] | None = None) -> bool:
        if svc.audio.running:
            return False

        def run():
            try:
                svc.audio.run(force=force, item_ids=item_ids)
            except Exception:
                log.exception("audio check failed")

        threading.Thread(target=run, name="audio-check", daemon=True).start()
        return True

    app.state.start_audio_check = start_audio_check

    @api.post("/audio-check", status_code=202)
    def post_audio_check(body: AudioCheckRequest | None = None):
        body = body or AudioCheckRequest()
        return {"started": start_audio_check(body.force, body.item_ids)}

    @api.post("/items/{item_id}/audio-check/dismiss", status_code=204)
    def dismiss_audio(item_id: int):
        try:
            svc.audio.dismiss(item_id)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        return Response(status_code=204)

    def show_target(key: str) -> tuple[str, list[str]]:
        """Display key -> (display title, parsed folder names grouped under it)."""
        parts = Q.decode_key(key)
        if len(parts) != 1:
            raise HTTPException(404, "no such show")
        with svc.db.session() as s:
            parsed = sorted({p for p in s.scalars(select(MediaItem.parsed_show).where(
                MediaItem.show == parts[0], MediaItem.kind == "episode")) if p})  # fmt: skip
        if not parsed:
            raise HTTPException(404, "no such show")
        return parts[0], parsed

    @api.get("/metadata")
    def get_metadata_state():
        return metadata_state()

    @api.post("/metadata/match", status_code=202)
    def post_match(body: MatchRequest | None = None):
        body = body or MatchRequest()
        return {"started": start_match(force=body.force)}

    @api.post("/metadata/refresh", status_code=202)
    def post_refresh():
        return {"started": start_match(refresh=True)}

    @api.get("/unmatched")
    def unmatched():
        with svc.db.session() as s:
            films = list(s.scalars(Q.visible().where(
                MediaItem.kind == "film",
                MediaItem.match_status.in_(("ambiguous", "unmatched"))
            ).order_by(MediaItem.sort_title)))  # fmt: skip
            out_films = []
            for f in films:
                d = Q.item_json(f)
                d.update({"file": f.rel_path, "library": f.root.name,
                          "metadata": Q.metadata_json(f)})  # fmt: skip
                out_films.append(d)
            shows = [r for r in Q.strip_private(Q.shows(s, "az"))
                     if r["metadata"] and r["metadata"]["status"] in ("ambiguous", "unmatched")]  # fmt: skip
        return {"films": out_films, "shows": shows, "tmdb": svc.matcher.tmdb is not None,
                "omdb": svc.matcher.omdb is not None}  # fmt: skip

    def _search(kind: str, q: str, year: int | None):
        try:
            return {"candidates": [c.json() for c in svc.matcher.search(kind, q, year)]}
        except MetadataApiError as e:
            raise HTTPException(503, str(e)) from e

    @api.get("/items/{item_id}/candidates")
    def item_candidates(item_id: int, q: str | None = None, year: int | None = None):
        with svc.db.session() as s:
            i = get_item(s, item_id)
            kind = "show" if i.kind == "episode" else "film"
            query = q or (i.parsed_show if kind == "show" else i.parsed_title) or i.title
            year = year if q else (year or i.parsed_year)
        return _search(kind, query, year)

    @api.get("/shows/{key}/candidates")
    def show_candidates(key: str, q: str | None = None, year: int | None = None):
        _title, parsed = show_target(key)
        return _search("show", q or parsed[0], year)

    def _fix(fn):
        try:
            return fn()
        except MetadataApiError as e:
            raise HTTPException(503, str(e)) from e

    @api.post("/items/{item_id}/match")
    def match_item(item_id: int, body: FixMatch):
        with svc.db.session() as s:
            i = get_item(s, item_id)
            kind, parsed_show = i.kind, i.parsed_show
        if body.tmdb_id is None and body.imdb_id is None:
            raise HTTPException(400, "give tmdb_id or imdb_id")
        ids = {"tmdb_id": body.tmdb_id, "imdb_id": body.imdb_id}
        if kind == "episode" and parsed_show:
            _fix(lambda: svc.matcher.match_show(parsed_show, manual=True, **ids))
        elif kind == "film":
            _fix(lambda: svc.matcher.match_film(item_id, manual=True, **ids))
        else:
            raise HTTPException(400, "only films and TV episodes can be matched")
        for cb in svc.matcher.on_change:
            cb()
        return item_detail(item_id)

    @api.post("/shows/{key}/match")
    def match_show_route(key: str, body: FixMatch):
        _title, parsed = show_target(key)
        if body.tmdb_id is None and body.imdb_id is None:
            raise HTTPException(400, "give tmdb_id or imdb_id")
        for p in parsed:
            _fix(lambda p=p: svc.matcher.match_show(p, tmdb_id=body.tmdb_id,
                                                    imdb_id=body.imdb_id, manual=True))  # fmt: skip
        for cb in svc.matcher.on_change:
            cb()
        with svc.db.session() as s:
            ep = s.scalar(select(MediaItem).where(MediaItem.parsed_show == parsed[0]))
            return {"key": Q.encode_key(ep.show)}

    def _edit(item_id: int | None, show: str | None, body: MetadataEdit):
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(400, "nothing to change")
        try:
            svc.matcher.edit(item_id, show, fields)
        except (ValueError, LookupError) as e:
            raise HTTPException(400, str(e)) from e
        svc.metadata_changed()

    @api.put("/items/{item_id}/metadata")
    def edit_item(item_id: int, body: MetadataEdit):
        _edit(item_id, None, body)
        return item_detail(item_id)

    @api.put("/shows/{key}/metadata")
    def edit_show(key: str, body: MetadataEdit):
        _title, parsed = show_target(key)
        for p in parsed:
            _edit(None, p, body)
        with svc.db.session() as s:
            ep = s.scalar(select(MediaItem).where(MediaItem.parsed_show == parsed[0]))
            return {"key": Q.encode_key(ep.show)}

    @api.post("/items/{item_id}/unlock", status_code=202)
    def unlock_item(item_id: int):
        svc.matcher.unlock(item_id, None)
        return {"started": start_match()}

    @api.post("/shows/{key}/unlock", status_code=202)
    def unlock_show(key: str):
        _title, parsed = show_target(key)
        for p in parsed:
            svc.matcher.unlock(None, p)
            # Offline: the show keeps its folder name until the next match.
            with contextlib.suppress(MetadataApiError):
                svc.matcher.match_show(p)
        svc.metadata_changed()
        with svc.db.session() as s:
            ep = s.scalar(select(MediaItem).where(MediaItem.parsed_show == parsed[0]))
            return {"started": False, "key": Q.encode_key(ep.show)}

    @api.get("/settings/metadata")
    def get_metadata_settings():
        m = svc.settings.metadata
        key = m.tmdb_api_key or ""
        okey = m.omdb_api_key or ""
        return {"tmdb_api_key_set": bool(key), "tmdb_api_key_hint": key[-4:] if key else None,
                "omdb_api_key_set": bool(okey), "omdb_api_key_hint": okey[-4:] if okey else None,
                "language": m.language, "region": m.region, "match_on_scan": m.match_on_scan,
                "save_local": m.save_local,
                "save_local_running": bool(svc.saver and svc.saver.running)}  # fmt: skip

    @api.put("/settings/metadata")
    def put_metadata_settings(body: MetadataSettings):
        values = body.model_dump(exclude_unset=True)
        try:
            set_config_values(svc.config_file, "metadata", values)
            svc.reload_settings()
        except PictureHouseError as e:
            raise HTTPException(400, e.message) from e
        if values.get("tmdb_api_key") or values.get("omdb_api_key"):
            start_match()
        if values.get("save_local"):
            svc.metadata_changed()  # write copies for everything matched so far
        return get_metadata_settings()

    # ------------------------------------------------------------ themes

    @api.get("/themes")
    def list_themes():
        """Themes this server has, and which one devices use by default."""
        found = themes.themes()
        default = themes.get(svc.settings.ui.theme).id
        return {"default": default,
                "themes": [t.summary() for t in sorted(found.values(), key=lambda t: (t.id != default, t.name.casefold()))],
                "problems": themes.problems}  # fmt: skip

    @api.put("/settings/theme")
    def put_theme(body: ThemeSetting):
        if body.default not in themes.themes():
            raise HTTPException(404, f"no theme called {body.default!r}")
        try:
            set_config_values(svc.config_file, "ui", {"theme": body.default})
            svc.reload_settings()
        except PictureHouseError as e:
            raise HTTPException(400, e.message) from e
        log.info("default theme changed", extra={"theme": body.default})
        return list_themes()

    @api.get("/settings/streaming")
    def get_streaming_settings():
        st = svc.settings
        return {"tv_max_bitrate_mbps": st.dlna.max_bitrate_mbps,
                "max_transcodes": st.ffmpeg.max_transcodes}  # fmt: skip

    @api.put("/settings/streaming")
    def put_streaming_settings(body: StreamingSettings):
        try:
            if body.tv_max_bitrate_mbps is not None:
                set_config_values(svc.config_file, "dlna",
                                  {"max_bitrate_mbps": body.tv_max_bitrate_mbps})  # fmt: skip
            if body.max_transcodes is not None:
                set_config_values(svc.config_file, "ffmpeg",
                                  {"max_transcodes": body.max_transcodes})  # fmt: skip
            svc.reload_settings()
        except PictureHouseError as e:
            raise HTTPException(400, e.message) from e
        procs.slots.limit = svc.settings.ffmpeg.max_transcodes
        log.info("streaming settings changed", extra=get_streaming_settings())
        return get_streaming_settings()

    @api.post("/scan", status_code=202)
    def post_scan(body: ScanRequest | None = None):
        body = body or ScanRequest()
        if body.libraries:
            known = {r.name.casefold(): r.name for r in svc.settings.libraries}
            unknown = [n for n in body.libraries if n.casefold() not in known]
            if unknown:
                raise HTTPException(404, f"no library named {', '.join(map(repr, unknown))}")
            body.libraries = [known[n.casefold()] for n in body.libraries]
        return {"started": start_scan(body.libraries, body.full)}

    @api.get("/scan")
    def get_scan():
        return scan_state()

    @api.get("/problems")
    def problems():
        with svc.db.session() as s:
            files_ = s.execute(
                select(MediaItem, LibraryRootRow.name).join(LibraryRootRow)
                .where(MediaItem.probe_ok.is_(False), MediaItem.available,
                       LibraryRootRow.configured)
                .order_by(MediaItem.probe_failed_at.desc())
            ).all()  # fmt: skip
            audio = s.execute(
                select(MediaItem, LibraryRootRow.name).join(LibraryRootRow)
                .where(MediaItem.audio_check == "warn", MediaItem.available,
                       LibraryRootRow.configured)
                .order_by(MediaItem.sort_title)
            ).all()  # fmt: skip
            offline = s.execute(
                select(LibraryRootRow, func.count(MediaItem.id))
                .outerjoin(MediaItem, MediaItem.root_id == LibraryRootRow.id)
                .where(LibraryRootRow.configured, LibraryRootRow.available.is_(False))
                .group_by(LibraryRootRow.id)
            ).all()  # fmt: skip
        return {
            "files": [{"id": i.id, "library": n, "file": i.rel_path, "error": i.probe_error,
                       "failed_at": Q.iso(i.probe_failed_at), "retry_count": i.retry_count}
                      for i, n in files_],
            "libraries": [{"name": r.name, "path": r.path,
                           "unavailable_since": Q.iso(r.unavailable_since), "items": n}
                          for r, n in offline],
            # Files that play but whose sound looks broken (library/audiocheck.py).
            "audio": [{"id": i.id, "library": n, "file": i.rel_path,
                       "title": (f"{i.show} S{i.season or 0:02d}E{i.episode or 0:02d} · {i.title}"
                                 if i.kind == "episode" else i.title),
                       "note": i.audio_note, "level_db": i.audio_level_db,
                       "dual_mono": i.audio_dual_mono,
                       "checked_at": Q.iso(i.audio_checked_at)} for i, n in audio],
            "audio_check": {"running": svc.audio.running},
        }  # fmt: skip

    @api.post("/items/{item_id}/retry", status_code=202)
    def retry(item_id: int):
        with svc.db.session() as s:
            i = get_item(s, item_id)
            i.retry_count = 0
            name = i.root.name
        return {"started": start_scan([name])}

    @api.get("/devices")
    def devices():
        with svc.db.session() as s:
            rows = list(s.scalars(select(Device).order_by(Device.last_seen.desc())))
        return {"devices": [
            {"address": d.address, "user_agent": d.user_agent, "name": device_name(d.user_agent),
             "via": d.via, "last_seen": Q.iso(d.last_seen)} for d in rows]}  # fmt: skip

    # ------------------------------------------------------------ admin access

    @api.get("/admin")
    def admin_state(request: Request):
        return {"admin": is_admin(request), "this_pc": on_this_pc(request),
                "pin_set": gate.pin_set,
                # The PIN from the app's settings (PICTUREHOUSE_ADMIN_PIN) was
                # left blank or wasn't 4-12 digits: say so instead of pointing
                # at a "PC running PictureHouse" a NAS doesn't have.
                "pin_problem": None if gate.pin_set else svc.pin_problem}  # fmt: skip

    @api.post("/admin/login")
    def admin_login(body: AdminLogin, request: Request):
        who = client_host(request)
        wait = gate.locked_out(who)
        if wait:
            raise HTTPException(429, f"too many wrong PINs; try again in {wait // 60 + 1} min")
        if not gate.pin_set:
            raise HTTPException(403, "no admin PIN is set; set one on the PC running PictureHouse")
        if not gate.check_pin(body.pin):
            gate.record_failure(who)
            log.warning("wrong admin PIN", extra={"client": who})
            raise HTTPException(403, "wrong PIN")
        gate.clear_failures(who)
        log.info("admin signed in", extra={"client": who})
        response = JSONResponse({"admin": True})
        response.set_cookie(access.COOKIE, gate.make_cookie(), max_age=access.COOKIE_DAYS * 86400,
                            httponly=True, samesite="strict", path="/")  # fmt: skip
        return response

    @api.post("/admin/logout")
    def admin_logout():
        response = JSONResponse({"admin": False})
        response.delete_cookie(access.COOKIE, path="/")
        return response

    @api.put("/admin/pin", status_code=204)
    def admin_set_pin(body: AdminPin, request: Request):
        """Set or remove the PIN. Only on this PC, even when signed in elsewhere."""
        if not on_this_pc(request):
            raise HTTPException(403, "the PIN can only be changed on the PC running PictureHouse")
        if body.pin is not None and not access.valid_pin(body.pin):
            raise HTTPException(400, "the PIN must be 4 to 12 digits")
        gate.set_pin(body.pin)
        log.info("admin PIN %s", "set" if body.pin else "removed")
        return Response(status_code=204)

    @api.post("/admin/shutdown", status_code=202, include_in_schema=False)
    def admin_shutdown(request: Request):
        """Clean stop (SSDP byebye, FFmpeg killed, DB closed) for the desktop app.
        Only from this machine: anything on the LAN gets 403."""
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "only allowed from this computer")
        stop = getattr(app.state, "request_shutdown", None)
        if stop is None:
            raise HTTPException(503, "not running under `picturehouse serve`")
        log.info("shutdown requested from %s", host)
        stop()
        return {"stopping": True}

    @api.get("/logs")
    def logs(lines: int = Query(200, ge=1, le=2000)):
        f = svc.layout.log_dir / LOG_FILE_NAME
        out: deque = deque(maxlen=lines)
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    out.append(line)
        except OSError:
            pass
        parsed = []
        for line in out:
            try:
                parsed.append(json.loads(line))
            except ValueError:
                continue
        return {"lines": parsed}

    app.include_router(api)

    # ------------------------------------------------------------ DLNA + UI

    if dlna is not None:
        app.include_router(dlna.router())

    if STATIC_DIR.is_dir():
        app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")

    # ------------------------------------------------------------ HTTPS + PWA

    def https_state() -> dict:
        if not https:
            return {"enabled": False}
        files = https["files"]
        ips = dlna.ssdp.joined if dlna else []
        from picturehouse.dlna.ssdp import local_ipv4_addresses

        ips = ips or local_ipv4_addresses()
        return {
            "enabled": True,
            "port": https["port"],
            "urls": [f"https://{ip}:{https['port']}/" for ip in ips],
            "ca_url": "/ca.crt",
            "ca_fingerprint": ca_fingerprint(files),
        }

    @app.api_route("/ca.crt", methods=["GET", "HEAD"], include_in_schema=False)
    def ca_cert():
        if not https:
            raise HTTPException(404, "HTTPS is off")
        # iOS/Android install a CA from a download with this type. "inline", not an
        # attachment: Safari before iOS 13 has no downloads at all and can only
        # *open* it, which starts the profile installer; others still save it.
        return FileResponse(https["files"].ca_cert, media_type="application/x-x509-ca-cert",
                            filename="PictureHouse-CA.crt", content_disposition_type="inline")  # fmt: skip

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        name = svc.settings.server.friendly_name
        icons = [{"src": f"/icons/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png",
                  "purpose": "any maskable"} for n in (192, 512)]  # fmt: skip
        body = {"name": name, "short_name": "PictureHouse", "start_url": "/", "scope": "/",
                "display": "standalone", "background_color": "#0a0d11",
                "theme_color": "#0a0d11", "icons": icons,
                "description": "Your films, TV and music at home."}  # fmt: skip
        return Response(json.dumps(body), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})  # fmt: skip

    @app.get("/icons/icon-{size}.png", include_in_schema=False)
    def app_icon(size: int):
        if size not in (48, 120, 152, 167, 180, 192, 512):
            raise HTTPException(404)
        return Response(_icon(size), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})  # fmt: skip

    @app.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
    def service_worker():
        # Served from the root so its scope covers the whole app.
        sw = STATIC_DIR / "sw.js"
        if not sw.is_file():
            raise HTTPException(404)
        return FileResponse(sw, media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})  # fmt: skip

    @app.get("/theme.css", include_in_schema=False)
    def theme_css(id: str | None = None, target: str = "console"):
        """The chosen theme as one stylesheet, applied on top of app.css (or tv.css).
        Without ?id= it's the server's default theme. An unknown id falls back
        to the default, so a device whose theme was removed still works."""
        theme = themes.get(id or svc.settings.ui.theme)
        css = render_css(theme, "tv" if target == "tv" else "console")
        return Response(css, media_type="text/css; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})  # fmt: skip

    @app.get("/themes/{theme_id}/{asset:path}", include_in_schema=False)
    def theme_asset(theme_id: str, asset: str):
        """A theme's own images and fonts (only those types, only inside its folder)."""
        theme = themes.themes().get(theme_id)
        rel = asset.replace("\\", "/")
        if theme is None or rel not in theme.assets:
            raise HTTPException(404)
        path = theme.folder / rel
        kind = THEME_ASSET_TYPES[path.suffix.lower()]
        return FileResponse(path, media_type=kind,
                            headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})  # fmt: skip

    @app.get("/tv", include_in_schema=False)
    @app.get("/tv/", include_in_schema=False)
    def tv_index():
        """The big-screen interface the LG TV app opens (static/tv/)."""
        page = STATIC_DIR / "tv" / "index.html"
        if not page.is_file():
            raise HTTPException(404)
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def index():
        page = STATIC_DIR / "index.html"
        if page.is_file():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>picturehouse</h1><p>Web console not built yet. API docs: "
                            "<a href='/docs'>/docs</a></p>")  # fmt: skip

    return app
