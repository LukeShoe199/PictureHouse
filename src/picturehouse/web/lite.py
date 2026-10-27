"""PictureHouse Lite: the library as plain web pages, for old browsers.

The console (static/app.js) needs a modern browser; Safari on iOS 9/10 (the
first iPad mini, old iPhones) and similar can't run it. Lite is built on the
server: links, pictures and a <video>/<audio> element, plus one small ES3
script that saves watch progress. No CSS grid, flex gaps, variables or
object-fit either (all too new for iOS 9). index.html sends browsers that fail
its feature test here (see static/index.html).

Pages reuse the JSON endpoints' own functions (passed in by create_app), so
lists, order, visibility and progress are exactly what the console shows.
Everything here is read-only; the only write is the progress PUT, which any
device may already make (web/access.py OPEN_WRITES).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from picturehouse.profiles import SCOPE, ProfileError
from picturehouse.web import access

log = logging.getLogger(__name__)

PAGE = 60  # items per page: old devices are slow and there's no lazy loading

SECTIONS = [("Films", "/lite/films"), ("TV", "/lite/tv"), ("Music", "/lite/music"),
            ("Videos", "/lite/home-videos"), ("Photos", "/lite/photos")]  # fmt: skip

# Progress for the one player on the page: resume, save every 15s / on pause,
# mark watched near the end. ES3 only (no arrows, let, fetch, JSON needs).
PLAYER_JS = """<script>
(function () {
  var v = document.getElementById("pv");
  if (!v || !v.addEventListener) return;
  var id = v.getAttribute("data-id"), t = +v.getAttribute("data-t") || 0,
      total = +v.getAttribute("data-total") || 0, last = 0, seeked = t <= 0;
  function progressive() { return /stream\\.mp[34]/.test(v.currentSrc || v.src || ""); }
  function pos() { return (progressive() ? t : 0) + (v.currentTime || 0); }
  function seek() {
    if (seeked || progressive()) { seeked = true; return; }
    try { v.currentTime = t; seeked = true; } catch (e) {}
  }
  function save(end) {
    var p = end && total ? total : pos(), x;
    if (p <= 0) return;
    x = new XMLHttpRequest();
    x.open("PUT", "/api/items/" + id + "/progress", true);
    x.setRequestHeader("Content-Type", "application/json");
    x.send('{"position":' + Math.round(p) + ',"completed":' +
           (total && p > total * 0.92 ? "true" : "false") + "}");
  }
  // Tell the server what the player did (its log is all we see of old devices).
  function report(ev) {
    var err = v.error ? v.error.code : "";
    (new Image()).src = "/lite/report?ev=" + ev + "&err=" + err + "&ns=" + v.networkState +
      "&p=" + encodeURIComponent(location.protocol) +
      "&src=" + encodeURIComponent(v.currentSrc || v.src || "");
  }
  v.addEventListener("play", function () { report("play"); }, false);
  v.addEventListener("error", function () { report("error"); }, false);
  v.addEventListener("loadedmetadata", seek, false);
  v.addEventListener("playing", seek, false);
  v.addEventListener("timeupdate", function () {
    var now = new Date().getTime();
    if (seeked && now - last > 15000) { last = now; save(false); }
  }, false);
  v.addEventListener("pause", function () { if (seeked) save(false); }, false);
  v.addEventListener("ended", function () { save(true); }, false);
})();
</script>"""


def e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def url(path: str) -> str:
    """A server URL made safe to drop into an attribute or url(...)."""
    return escape(quote(path, safe="/?=&:%-_.~"), quote=True)


def clock(seconds: float | None) -> str:
    s = int(seconds or 0)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def sized(art: str | None, w: int) -> str | None:
    if not art:
        return None
    return f"{art}{'&' if '?' in art else '?'}w={w}" if "/art/" in art else art


def page(name: str, title: str, body: str, *, back: tuple[str, str] | None = None,
         status: int = 200, who: str | None = None) -> HTMLResponse:  # fmt: skip
    """``who``: this device's profile, shown as a link to change it."""
    sections = SECTIONS + ([(f"&#9786; {e(who)}", "/lite/profiles")] if who else [])
    nav = "".join(f'<a href="{h}">{label}</a>' for label, h in sections)
    crumb = f'<a class="back" href="{url(back[1])}">&lsaquo; {e(back[0])}</a>' if back else ""
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PictureHouse">
<link rel="apple-touch-icon" href="/icons/icon-180.png">
<title>{e(title)} &middot; {e(name)}</title>
<link rel="stylesheet" href="/static/lite.css">
</head><body>
<div class="bar"><a class="logo" href="/lite/">PH</a><span class="nav">{nav}</span></div>
<div class="main">{crumb}{body}</div>
<p class="foot">{e(name)} &middot; PictureHouse Lite, for older devices &middot;
<a href="/?full=1">Full version</a> (newer browsers)</p>
</body></html>"""
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-cache"})


def card(href: str, title: str, sub: str = "", art: str | None = None, *, shape: str = "p",
         pct: float = 0) -> str:  # fmt: skip
    bg = f' style="background-image:url({url(art)})"' if art else ""
    bar = f'<span class="pr"><span style="width:{pct:.0f}%"></span></span>' if pct else ""
    return (f'<a class="c {shape}" href="{url(href)}"><span class="a"{bg}>'
            f'<span class="n">{e(title)}</span>{bar}</span>'
            f'<span class="t">{e(title)}</span><span class="s">{e(sub)}</span></a>')  # fmt: skip


def grid(cards: list[str], empty: str = "Nothing here yet.") -> str:
    return f'<div class="g">{"".join(cards)}</div>' if cards else f'<p class="dim">{e(empty)}</p>'


def pager(base: str, offset: int, total: int) -> str:
    links = []
    if offset > 0:
        links.append(f'<a class="btn" href="{url(f"{base}?offset={max(0, offset - PAGE)}")}">'
                     "&lsaquo; Previous</a>")  # fmt: skip
    if offset + PAGE < total:
        links.append(
            f'<a class="btn" href="{url(f"{base}?offset={offset + PAGE}")}">Next &rsaquo;</a>'
        )
    shown = f"{offset + 1}&ndash;{min(total, offset + PAGE)} of {total}" if total else ""
    return f'<p class="pg"><span class="dim">{shown}</span> {" ".join(links)}</p>' if links else ""


def item_card(it: dict, *, shape: str = "p") -> str:
    sub = it.get("year") or ""
    if it["kind"] == "episode":
        sub = it.get("show") or ""
    prog = it.get("progress")
    pct = 0.0
    if prog and not prog["completed"] and it.get("duration"):
        pct = min(100.0, prog["position"] / it["duration"] * 100)
    art = sized(it["art"].get("thumb" if shape == "l" else "poster"), 360 if shape == "l" else 240)
    return card(f"/lite/item/{it['id']}", episode_label(it), str(sub), art, shape=shape, pct=pct)


def episode_label(it: dict) -> str:
    if it["kind"] != "episode" or it.get("episode") is None:
        return it["title"]
    num = f"{it['episode']}" + (f"-{it['episode_end']}" if it.get("episode_end") else "")
    return f"{num}. {it['title']}"


def router(svc, api, profiles=None) -> APIRouter:
    """``api``: the JSON endpoint functions, by name (see create_app). They're
    called directly, so every parameter is passed: their defaults are
    FastAPI Query objects, not values."""
    r = APIRouter(prefix="/lite", include_in_schema=False)

    def name() -> str:
        return svc.settings.server.friendly_name

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException as ex:
            raise LiteError(ex.status_code, str(ex.detail)) from ex

    def who() -> str | None:
        if profiles is None or len(profiles.profiles()) < 2:
            return None
        return profiles.get(SCOPE.get().profile_id).name

    def render(title: str, body: str, back: tuple[str, str] | None = None) -> HTMLResponse:
        return page(name(), title, body, back=back, who=who())

    @r.get("/profiles")
    def profile_page(request: Request, error: str | None = None):
        """Who's watching: a plain form per profile (the exit code when
        leaving a child profile). ``error``: from a failed switch."""
        me = profiles.get(SCOPE.get().profile_id)
        body = "<h1>Who's watching?</h1>"
        if error:
            body += f'<p class="msg">{e(error)}</p>'
        for p in profiles.profiles():
            if p.id == me.id:
                body += f'<p class="btn primary">{e(p.name)} (this device)</p>'
                continue
            needs = me.child and not p.child
            code = ('<input class="code" name="code" type="password" inputmode="numeric" '
                    'pattern="[0-9]*" maxlength="12" placeholder="Exit code" autocomplete="off">'
                    if needs else "")  # fmt: skip
            body += (f'<form class="prof" method="post" action="/lite/profiles">'
                     f'<input type="hidden" name="id" value="{p.id}">{code}'
                     f'<button class="btn" type="submit">{e(p.name)}'
                     f'{" (child)" if p.child else ""}</button></form>')  # fmt: skip
        if me.child:
            body += '<p class="dim">Leaving this child profile needs the exit code.</p>'
        return render("Profiles", body)

    @r.post("/profiles")
    async def profile_switch(request: Request):
        if not access.origin_ok("POST", request.headers.get("origin"),
                                request.headers.get("referer"), request.headers.get("host")):  # fmt: skip
            raise LiteError(403, "request came from another website")
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        try:
            pid = int(form.get("id", [""])[0])
            profiles.switch(request.client.host if request.client else "", pid,
                            form.get("code", [""])[0].strip() or None)  # fmt: skip
        except ValueError:
            raise LiteError(400, "no such profile") from None
        except ProfileError as ex:
            return RedirectResponse(f"/lite/profiles?error={quote(ex.message)}", 303)
        return RedirectResponse("/lite/", 303)

    @r.get("")
    @r.get("/")
    def home():
        cont = call(api.continue_watching, limit=12, audiobooks=False)["items"]
        recent = call(api.recent, limit=12)["items"]
        body = (
            '<div class="secs">'
            + "".join(f'<a class="btn big" href="{h}">{label}</a>' for label, h in SECTIONS)
            + "</div>"
        )
        if cont:
            body += "<h2>Continue watching</h2>" + grid([item_card(i, shape="l") for i in cont])
        body += "<h2>Recently added</h2>" + grid([item_card(i) for i in recent])
        return render("Home", body)

    def kind_list(fn, title: str, base: str, offset: int):
        d = call(fn, "az", offset, PAGE)
        body = f"<h1>{e(title)}</h1>" + grid([item_card(i) for i in d["items"]])
        return render(title, body + pager(base, offset, d["total"]))

    @r.get("/films")
    def films(offset: int = Query(0, ge=0)):
        return kind_list(api.films, "Films", "/lite/films", offset)

    @r.get("/home-videos")
    def home_videos(offset: int = Query(0, ge=0)):
        return kind_list(api.home_videos, "Home videos", "/lite/home-videos", offset)

    @r.get("/tv")
    def shows(offset: int = Query(0, ge=0)):
        d = call(api.shows, "az", offset, PAGE)
        cards = [card(f"/lite/tv/{s['key']}", s["title"],
                      f"{s['seasons']} season{'s' * (s['seasons'] != 1)}",
                      sized(s["art"].get("poster"), 240)) for s in d["shows"]]  # fmt: skip
        return render("TV", "<h1>TV</h1>" + grid(cards) + pager("/lite/tv", offset, d["total"]))

    @r.get("/tv/{key}")
    def show(key: str):
        d = call(api.show, key)
        body = f"<h1>{e(d['title'])}</h1>"
        if d.get("overview"):
            body += f'<p class="syn">{e(d["overview"])}</p>'
        nxt = d.get("next_up")
        if nxt:
            what = "Resume" if nxt.get("progress") else "Play"
            body += (f'<p><a class="btn primary" href="/lite/item/{nxt["id"]}">{what} '
                     f"{e(season_ep(nxt))}</a></p>")  # fmt: skip
        for season in d["season_list"]:
            n = season["season"]
            body += f"<h2>{'Specials' if n is None or n <= 0 else f'Season {n}'}</h2>"
            body += grid([item_card(ep, shape="l") for ep in season["episodes"]])
        return render(d["title"], body, back=("TV", "/lite/tv"))

    @r.get("/music")
    def artists(offset: int = Query(0, ge=0)):
        d = call(api.music_artists, "az", offset, PAGE)
        cards = [card(f"/lite/music/{a['key']}", a["name"],
                      f"{a['albums']} album{'s' * (a['albums'] != 1)}" if a["albums"]
                      else f"{a['tracks']} track{'s' * (a['tracks'] != 1)}",
                      sized(a["art"].get("poster"), 240), shape="q") for a in d["artists"]]  # fmt: skip
        return render(
            "Music", "<h1>Music</h1>" + grid(cards) + pager("/lite/music", offset, d["total"])
        )

    @r.get("/music/{key}")
    def artist(key: str):
        d = call(api.music_artist, key)
        body = f'<h1>{e(d["name"])}</h1>' + grid([
            card(f"/lite/album/{a['key']}", a["title"], a.get("year") or "",
                 sized(a["art"].get("poster"), 240), shape="q") for a in d["albums"]])  # fmt: skip
        if d["tracks"]:
            body += "<h2>Other tracks</h2>" + track_list(d["tracks"], artist=key)
        return render(d["name"], body, back=("Music", "/lite/music"))

    @r.get("/album/{key}")
    def album(key: str):
        d = call(api.music_album, key)
        art = sized(d["art"].get("poster"), 240)
        cover = (
            f'<span class="cover" style="background-image:url({url(art)})"></span>' if art else ""
        )
        body = (f'<div class="head">{cover}<h1>{e(d["title"])}</h1>'
                f'<p class="dim">{e(d["artist"])}{" &middot; " + e(d["year"]) if d.get("year") else ""}</p>'
                f'</div>{track_list(d["track_list"], album=key)}')  # fmt: skip
        return render(d["title"], body, back=(d["artist"], f"/lite/music/{d['artist_key']}"))

    @r.get("/photos")
    def photo_albums():
        d = call(api.photos)
        cards = [card(f"/lite/photos/{a['key']}", a["title"],
                      f"{a['photos']} photo{'s' * (a['photos'] != 1)}",
                      a["art"].get("poster"), shape="q") for a in d["albums"]]  # fmt: skip
        return render("Photos", "<h1>Photos</h1>" + grid(cards))

    @r.get("/photos/{key}")
    def photo_album(key: str):
        d = call(api.photo_album, key)
        cards = [card(f"/lite/photos/{key}/{n}", "", "", p["thumb"], shape="q ph")
                 for n, p in enumerate(d["photos"])]  # fmt: skip
        return render(d["title"], f'<h1>{e(d["title"])}</h1>' + grid(cards),
                      back=("Photos", "/lite/photos"))  # fmt: skip

    @r.get("/photos/{key}/{n}")
    def photo(key: str, n: int):
        d = call(api.photo_album, key)
        photos = d["photos"]
        if not 0 <= n < len(photos):
            raise LiteError(404, "no such photo")
        p, base = photos[n], f"/lite/photos/{key}"
        links = []
        if n > 0:
            links.append(f'<a class="btn" href="{url(f"{base}/{n - 1}")}">&lsaquo; Previous</a>')
        if n + 1 < len(photos):
            links.append(f'<a class="btn" href="{url(f"{base}/{n + 1}")}">Next &rsaquo;</a>')
        body = (f'<p class="pg"><span class="dim">{n + 1} of {len(photos)}</span> {" ".join(links)}</p>'
                f'<a href="{url(p["original"])}"><img class="photo" src="{url(p["screen"])}" '
                f'alt="{e(p["title"])}"></a>')  # fmt: skip
        return render(p["title"] or d["title"], body, back=(d["title"], base))

    @r.get("/report")
    def report(request: Request, ev: str = "", err: str = "", ns: str = "", p: str = "",
               src: str = ""):  # fmt: skip
        """What a Lite player did (play pressed, or an error), sent by its script."""
        level = logging.WARNING if ev == "error" else logging.INFO
        log.log(level, "Lite player: %s", ev[:20], extra={
            "error_code": err[:10], "network_state": ns[:10], "protocol": p[:10],
            "src": src[:300], "ua": request.headers.get("user-agent", "")[:300]})  # fmt: skip
        return Response(status_code=204)

    @r.get("/item/{item_id}")
    def item(request: Request, item_id: int, t: float | None = Query(None, ge=0),
             album: str | None = None, artist: str | None = None):  # fmt: skip
        """``album`` / ``artist``: the music folder a track was opened from,
        for its Next track link and the way back (tags can differ from folders)."""
        it = call(api.item_detail, item_id)
        ctx = f"&album={album}" if album else f"&artist={artist}" if artist else ""
        prog = it.get("progress")
        start = t if t is not None else (
            prog["position"] if prog and not prog["completed"] else 0.0)  # fmt: skip
        if it.get("duration") and start >= it["duration"] * 0.98:
            start = 0.0
        body = f"<h1>{e(it['title'])}</h1>{meta_line(it)}"
        try:
            ua = request.headers.get("user-agent", "")
            src = choose(it, call(api.play, item_id, start, "browser"), start, apple(ua))
            log.info("Lite play: %s", src.route, extra={"item": item_id, "ua": ua})
            body += player(it, src, start)
        except LiteError as ex:
            body += f'<p class="msg">{e(ex.detail)}</p>'
        if start > 0:
            body += (f'<p class="dim">Resuming from {clock(start)}. '
                     f'<a href="{url(f"/lite/item/{item_id}?t=0{ctx}")}">Start from the beginning</a></p>')  # fmt: skip
        if it.get("overview"):
            body += f'<p class="syn">{e(it["overview"])}</p>'
        body += neighbours(it, album, artist, ctx)
        return render(it["title"], body, back=back_for(it, album, artist))

    def player(it: dict, src: Source, start: float) -> str:
        tag = "audio" if src.mime.startswith("audio/") else "video"
        attrs = (f'id="pv" controls preload="none" src="{url(src.url)}" data-id="{it["id"]}" '
                 f'data-t="{start:g}" data-total="{it.get("duration") or 0:g}"')  # fmt: skip
        if tag == "video":
            poster = sized(it["art"].get("backdrop") or it["art"].get("thumb"), 1024)
            attrs += f' poster="{url(poster)}"' if poster else ""
        # One src, no <source> list: old Safari's source picking is unreliable.
        # The link opens the same stream in the device's own player, a way
        # out if the page's player won't start (it can't resume or save, though).
        return (f'<div class="pl {tag}"><{tag} {attrs}></{tag}></div>'
                f'<p><a class="btn" href="{url(src.url)}">Play full screen</a></p>'
                f"{PLAYER_JS}")  # fmt: skip

    def neighbours(it: dict, album: str | None, artist: str | None, ctx: str) -> str:
        """Next episode / next track: there's no queue, so a link."""
        nxt = None
        if it["kind"] == "episode" and it.get("show_key"):
            eps = [
                ep for s in call(api.show, it["show_key"])["season_list"] for ep in s["episodes"]
            ]
            ids = [ep["id"] for ep in eps]
            k = ids.index(it["id"]) if it["id"] in ids else -1
            if 0 <= k < len(eps) - 1:
                nxt = ("Next episode", eps[k + 1])
        elif it["kind"] == "track" and (album or artist):
            tracks = (call(api.music_album, album)["track_list"] if album
                      else call(api.music_artist, artist)["tracks"])  # fmt: skip
            ids = [tr["id"] for tr in tracks]
            k = ids.index(it["id"]) if it["id"] in ids else -1
            if 0 <= k < len(tracks) - 1:
                nxt = ("Next track", tracks[k + 1])
        if not nxt:
            return ""
        label, n = nxt
        return (f'<p><a class="btn" href="{url(f"/lite/item/{n['id']}?t=0{ctx}")}">{label}: '
                f"{e(episode_label(n))} &rsaquo;</a></p>")  # fmt: skip

    def back_for(it: dict, album: str | None, artist: str | None) -> tuple[str, str]:
        if it["kind"] == "episode" and it.get("show_key"):
            return it.get("show") or "TV", f"/lite/tv/{it['show_key']}"
        if it["kind"] == "track" and album:
            return it.get("album") or "Album", f"/lite/album/{album}"
        if it["kind"] == "track" and artist:
            return it.get("artist") or "Artist", f"/lite/music/{artist}"
        if it["kind"] == "track":
            return "Music", "/lite/music"
        if it["kind"] == "home_video":
            return "Home videos", "/lite/home-videos"
        return "Films", "/lite/films"

    return r


def season_ep(it: dict) -> str:
    if it.get("season") is not None and it.get("episode") is not None:
        return f"S{it['season']} E{it['episode']}"
    return it["title"]


def meta_line(it: dict) -> str:
    bits = [it.get("show") if it["kind"] == "episode" else None,
            season_ep(it) if it["kind"] == "episode" else None,
            it.get("artist") if it["kind"] == "track" else None, it.get("album"),
            it.get("year"), clock(it["duration"]) if it.get("duration") else None,
            it.get("certification"), it.get("quality")]  # fmt: skip
    shown = [e(b) for b in bits if b]
    return f'<p class="dim">{" &middot; ".join(shown)}</p>' if shown else ""


def track_list(tracks: list[dict], *, album: str = "", artist: str = "") -> str:
    ctx = f"?album={album}" if album else f"?artist={artist}" if artist else ""
    rows = "".join(
        f'<li><a href="{url(f"/lite/item/{t['id']}{ctx}")}"><span class="no">{e(t.get("track_no") or "")}</span>'
        f'{e(t["title"])}<span class="dur">{clock(t["duration"]) if t.get("duration") else ""}'
        f"</span></a></li>"
        for t in tracks
    )
    return f'<ol class="tracks">{rows}</ol>'


@dataclass(frozen=True)
class Source:
    url: str
    mime: str
    route: str  # for the log: why this stream


# What Safari on iOS (9 onwards) and older Macs play as it is. Anything else
# goes through the server: video as HLS (Apple's own format; progressive
# streams need byte ranges, which a live conversion can't give), audio as an
# MP3 stream.
APPLE_VIDEO_EXTS = {".mp4", ".m4v", ".mov"}
APPLE_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".mp4"}
APPLE_AUDIO = {"aac", "mp3", "alac"}
_APPLE_UA = re.compile(r"iPhone|iPad|iPod|Macintosh")


def apple(ua: str) -> bool:
    """Safari on an Apple device (on iOS every browser is Safari underneath)."""
    return bool(_APPLE_UA.search(ua)) and "Chrome/" not in ua and "Firefox/" not in ua


def choose(it: dict, d: dict, start: float, is_apple: bool) -> Source:
    """``d``: what /api/items/{id}/play decided for a modern browser."""
    iid, ext = it["id"], PurePosixPath(it.get("file") or "").suffix.lower()
    acodec = next((st["codec"] for st in it.get("streams", []) if st["type"] == "audio"), None)
    if d["mime"].startswith("audio/"):
        if d["mode"] == "direct" and (not is_apple or (ext in APPLE_AUDIO_EXTS
                                                       and acodec in APPLE_AUDIO)):  # fmt: skip
            return Source(d["url"], d["mime"], "original file")
        return Source(f"/api/items/{iid}/stream.mp3?start={start:g}", "audio/mpeg", "MP3 stream")
    if not is_apple:
        if d["mode"] == "direct":
            return Source(d["url"], d["mime"], "original file")
        return Source(f"/api/items/{iid}/stream.mp4?start={start:g}", "video/mp4", "MP4 stream")
    v = it.get("video") or {}
    if (d["mode"] == "direct" and ext in APPLE_VIDEO_EXTS and v.get("codec") == "h264"
            and (v.get("height") or 0) <= 1080 and acodec in (None, "aac", "mp3")):  # fmt: skip
        return Source(d["url"], d["mime"], "original file")
    if it.get("duration"):
        return Source(f"/api/items/{iid}/hls.m3u8", "application/vnd.apple.mpegurl", "HLS")
    return Source(f"/api/items/{iid}/stream.mp4?start={start:g}", "video/mp4", "MP4 stream")


class LiteError(Exception):
    """A page that can't be shown (no such item, file offline...): rendered
    as a Lite page with the reason, not the API's JSON error."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def install(app, svc, api) -> None:
    """Add Lite's pages, and its error page, to the app."""
    app.include_router(router(svc, api, getattr(app.state, "profiles", None)))

    @app.middleware("http")
    async def lite_over_http(request, call_next):
        """Lite always runs over plain http: on an old device without the
        certificate, Safari lets you past the warning for pages, but its media
        player refuses the https stream (crossed-out play button)."""
        if request.url.scheme == "https" and request.url.path.startswith("/lite"):
            port = svc.settings.server.http_port
            return RedirectResponse(str(request.url.replace(scheme="http", port=port)), 302)
        return await call_next(request)

    @app.exception_handler(LiteError)
    async def lite_error(_request, ex: LiteError):
        body = (f'<h1>Not available</h1><p class="msg">{e(ex.detail)}</p>'
                '<p><a class="btn" href="/lite/">Home</a></p>')  # fmt: skip
        return page(svc.settings.server.friendly_name, "Not available", body, status=ex.status)
