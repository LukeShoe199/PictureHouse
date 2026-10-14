# HTTP API (web console contract)

Served by `picturehouse serve` (default port 9595). All JSON. Times are ISO-8601
UTC strings. The web console lives at `/` (static files under `/static/`).
FastAPI's generated docs are at `/docs`.

## Shared shapes

**Item** (any playable file):

```json
{
  "id": 12, "kind": "film|episode|track|home_video",
  "title": "Pilot", "year": 2019,
  "show": "Test Show", "season": 1, "episode": 1, "episode_end": null,
  "artist": null, "album": null, "track_no": null,
  "duration": 2.0, "added_at": "2026-10-09T17:04:00+00:00", "available": true,
  "quality": "4K|1080p|720p|SD|null", "hdr": false,
  "art": {"poster": "/api/items/12/art/poster", "thumb": "/api/items/12/art/thumb",
          "backdrop": "/api/items/12/art/backdrop"},
  "progress": {"position": 31.5, "completed": false}
}
```

Art URLs are always present. The server returns **404** when it has no image, so
the client must fall back (the console paints generated artwork on `onerror`).
Artwork for tracks is `art.poster` (square album cover). `progress` is `null`
when the item has never been played.

**Show**: `{"key", "title", "year", "seasons": n, "episodes": n, "added_at", "art": {"poster", "backdrop"}}`

**Album**: `{"key", "title", "artist", "year", "tracks": n, "duration", "added_at", "art": {"poster"}}`

`key` values are opaque URL-safe strings; pass them back unchanged.

## Browse

| Method & path | Returns |
| --- | --- |
| `GET /api/status` | `{"server": {"name", "version", "started_at"}, "scan": ScanState, "counts": {"films","shows","episodes","albums","tracks","home_videos","failed","unavailable"}, "total_bytes", "dlna": {"enabled", "addresses": [url]}}` |
| `GET /api/films?sort=added\|az\|year` | `{"items": [Item]}` |
| `GET /api/home-videos?sort=added\|az\|year` | `{"items": [Item]}` |
| `GET /api/shows?sort=…` | `{"shows": [Show]}` |
| `GET /api/shows/{key}` | `Show + {"season_list": [{"season": n, "episodes": [Item]}], "next_up": Item\|null}` |
| `GET /api/albums?sort=…` | `{"albums": [Album]}` |
| `GET /api/albums/{key}` | `Album + {"track_list": [Item]}` |
| `GET /api/recent?limit=20` | `{"items": [Item]}`: films, episodes and home videos, newest first |
| `GET /api/continue` | `{"items": [Item]}`: started, not completed, most recent first |
| `GET /api/search?q=` | `{"films", "episodes", "home_videos", "tracks": [Item], "shows": [Show], "albums": [Album]}` |
| `GET /api/items/{id}` | Item + detail fields (below) |

Item detail adds:

```json
{
  "library": "Films", "file": "The Grand Test (2019)/The Grand Test (2019).mkv",
  "size": 123456, "container": "matroska,webm", "bitrate": 400000,
  "video": {"codec": "h264", "profile": "High", "width": 320, "height": 180, "pix_fmt": "yuv420p", "fps": 25.0},
  "streams": [{"index": 0, "type": "video|audio|subtitle", "codec", "language", "title", "channels", "default", "forced"}],
  "playback": {"browser": {"mode": "direct|remux|transcode", "reason": "…"},
               "dlna":    {"mode": "direct|transcode", "reason": "…"}},
  "probe": {"ok": true, "error": null}
}
```

## Playback

| Method & path | Notes |
| --- | --- |
| `GET /api/items/{id}/play?start=0` | `{"mode", "reason", "url", "mime", "duration", "seekable"}`. When `seekable` is true (direct), the media element seeks natively. When false (remux/transcode), the stream starts at `start` seconds: to seek, request `/play?start=N` again and swap the src. The displayed position is `start + element.currentTime`. |
| `GET /api/items/{id}/file` | Original file with HTTP Range support |
| `GET /api/items/{id}/stream.mp4?start=N` | Fragmented MP4 from FFmpeg (remux or H.264/AAC transcode). The FFmpeg process is killed when the client disconnects. |
| `PUT /api/items/{id}/progress` | Body `{"position": s, "completed": bool}`, returns 204 |

## Library management

| Method & path | Returns |
| --- | --- |
| `GET /api/libraries` | `{"libraries": [{"name", "path", "kind", "available", "unavailable_since", "items", "bytes", "last_scan_at"}]}` |
| `POST /api/libraries` | Body `{"name", "path", "kind"}`. Returns 201 with the library, or 400 `{"detail": msg}` |
| `DELETE /api/libraries/{name}` | 204. Items are kept but hidden. |
| `POST /api/scan` | Body `{"libraries": [names] or null, "full": false}`. Returns 202 `{"started": bool}` (false if a scan is already running) |
| `GET /api/scan` | ScanState: `{"running", "library", "done", "total", "current", "finished_at", "results": [{"library", "available", "new", "changed", "unchanged", "failed", "missing", "seconds"}]}` |
| `GET /api/problems` | `{"files": [{"id", "library", "file", "error", "failed_at", "retry_count"}], "libraries": [{"name", "path", "unavailable_since", "items"}]}` |
| `POST /api/items/{id}/retry` | 202. Resets the retry count and rescans that library in the background. |
| `GET /api/devices` | `{"devices": [{"address", "user_agent", "name", "via": "dlna\|web", "last_seen"}]}` |
| `GET /api/logs?lines=200` | `{"lines": [log entry objects]}`: the newest entries from the JSON log |

## Metadata (Phase 2)

Items and shows now carry metadata from `.nfo` files or TMDb. Precedence:
manual edits (locked) > .nfo > TMDb > file name.

**Item** gains `overview`, `genres` (comma-separated string), `certification`
(e.g. "12A") and `rating` (0–10). All may be null.

**Item detail** and **Show** gain `metadata`:

```json
{"source": "file|nfo|tmdb|manual",
 "status": "pending|matched|ambiguous|unmatched|skipped",
 "confidence": 0.93, "tmdb_id": 194, "imdb_id": "tt0211915",
 "locked": ["title"],
 "candidates": [{"tmdb_id": 1, "title": "…", "year": 2001, "score": 0.81, "overview": "…", "poster": "https://…|null"}],
 "people": {"director": ["…"], "cast": ["…"]},
 "matched_at": "…"}
```

`candidates` is filled when `status` is `ambiguous` or `unmatched`. For TV
`people.director` holds the show's creators. **Show** also gains `overview`,
`genres`, `certification` and `rating`; `metadata` is null until the show has
been looked at. `skipped` means the kind isn't matched online (home videos,
music).

Artwork URLs are unchanged. The server now returns TMDb posters, backdrops
and episode stills when there's no local artwork.

| Method & path | Notes |
| --- | --- |
| `GET /api/metadata` | `{"tmdb": bool (key configured), "running", "pending", "needs_attention"}`, also included in `/api/status` as `metadata` |
| `POST /api/metadata/match` | Body `{"force": false}`. Returns 202 `{"started"}`. Matches pending items in the background (`force` re-searches everything except manual matches) |
| `POST /api/metadata/refresh` | 202. Re-fetches details for matched items by their existing ids |
| `GET /api/unmatched` | `{"films": [Item + {"file", "library", "metadata"}], "shows": [Show], "tmdb": bool}`: things needing the user's help |
| `GET /api/items/{id}/candidates?q=&year=` | `{"candidates": [...]}` from a TMDb search (defaults to the parsed title/year; episodes search their show). 503 `{"detail"}` without a key or when TMDb is down |
| `GET /api/shows/{key}/candidates?q=&year=` | The same, for a show |
| `POST /api/items/{id}/match` | Body `{"tmdb_id": n}`: a manual match (film, or an episode's whole show). Returns the item detail |
| `POST /api/shows/{key}/match` | Body `{"tmdb_id": n}`. Returns `{"key"}`, the show's **new** key (its title may change) |
| `PUT /api/items/{id}/metadata` | Body with any of `title`, `year`, `overview`, `genres`, `certification`. Edited fields are locked against future matching. Returns the item detail |
| `PUT /api/shows/{key}/metadata` | The same for a show; returns `{"key"}` (new key if the title changed) |
| `POST /api/items/{id}/unlock`, `POST /api/shows/{key}/unlock` | Forget manual edits/matches and re-match from scratch (202) |
| `GET /api/settings/metadata` | `{"tmdb_api_key_set": bool, "tmdb_api_key_hint": "last 4 chars"|null, "language", "region", "match_on_scan"}` (the key itself is never returned) |
| `PUT /api/settings/metadata` | Body with any of `tmdb_api_key`, `language`, `region`, `match_on_scan`, `save_local` (writes .nfo + artwork next to media; turning it on saves everything matched so far). Saved to config.toml; setting a key starts matching. 400 `{"detail"}` on invalid values |

## Phones, casting and HLS (Phase 6)

| Method & path | Notes |
| --- | --- |
| `GET /api/items/{id}/play` | Also returns `hls_url` (`/api/items/{id}/hls.m3u8`) for video that isn't direct play, otherwise `null` |
| `GET /api/items/{id}/hls.m3u8?audio=N` | VOD playlist covering the whole file in 6 s segments; seeking works natively. `audio` is a stream `index` from item detail. The response header `X-HLS-Session` carries the session id; segment URLs are `/api/hls/{sid}/{k}.ts` |
| `DELETE /api/hls/{sid}` | Stop the encoder and delete segments (call this when the player closes). Idle sessions are cleaned up anyway |
| `GET /api/status` | `server.http_port`, and `https`: `{"enabled": false}` or `{"enabled": true, "port", "urls": ["https://ip:port/"], "ca_url": "/ca.crt", "ca_fingerprint"}` |
| `GET /ca.crt` | The local CA certificate, to install on phones and tablets (404 when HTTPS is off) |
| `GET /manifest.webmanifest`, `GET /icons/icon-{48,120,152,167,180,192,512}.png` | PWA manifest and icons |
| `GET /sw.js` | Service worker, served from `static/sw.js` at the root scope (404 if the file is missing) |

Media URLs (`/file`, `/stream.mp4`, `/hls.m3u8`, `/api/hls/...`) send
`Access-Control-Allow-Origin: *` so a Chromecast receiver can fetch them.
hls.js 1.7.3 is vendored at `/static/vendor/hls-1.7.3.min.js` (Apache-2.0,
licence alongside).

Secure-context facts the client must respect:

- The Google Cast web sender and service workers only work on HTTPS (or
  localhost). On `http://<lan-ip>:9595` casting and PWA install are
  unavailable. The client should offer the HTTPS address instead
  (`status.https.urls`).
- Chromecast's Default Media Receiver is an HTTPS page that won't trust our
  local CA. Give it **plain-http** media URLs on the LAN
  (`http://<host>:<server.http_port>/...`): progressive MP4 only (passive
  mixed content is allowed; HLS via XHR is not). Use `/file` when the item's
  browser decision is `direct` and its MIME type is `video/mp4` or
  `video/webm`, otherwise `/stream.mp4?start=N`, re-loading with a new
  `start` to seek.

## OMDb fallback

With `[metadata] omdb_api_key` set, OMDb (IMDb data) is used when no TMDb key
is configured, or when TMDb has no candidates at all for a title.

- `metadata.source` can be `omdb`.
- `metadata.imdb_id` is set; `tmdb_id` stays `null`.
- Candidates gain `"imdb_id"` and `"provider": "tmdb" | "omdb"`. OMDb
  candidates have `tmdb_id: null`.
- `POST /api/items/{id}/match` and `POST /api/shows/{key}/match` accept
  `{"imdb_id": "tt…"}` as well as `{"tmdb_id": n}`. With neither they return
  400; with an id whose provider has no key they return 503.
- `GET /api/metadata` and `GET /api/unmatched` include `"omdb": bool`.
- `GET /api/settings/metadata` adds `omdb_api_key_set` and
  `omdb_api_key_hint`; `PUT` accepts `omdb_api_key`.

OMDb has no backdrops, episode plots or stills, and the free key allows
about 1,000 requests a day. When the quota runs out, lookups pause for the
rest of that run and the items stay pending.

## Admin access and streaming limits (session 7)

Requests from this PC (loopback address and loopback `Host`) are admin.
Other devices may use every `GET` except `/api/logs`, `/api/devices`,
`/api/settings/*` and `.../candidates`. Their only allowed writes are
`PUT /api/items/{id}/progress`, `DELETE /api/hls/{sid}` and the
login/logout calls below. Anything else returns
**403** `{"detail", "admin_required": true, "pin_set": bool}`. A write whose
`Origin` (or `Referer`) is another site is refused with 403 even from this PC.

| Endpoint | Notes |
|---|---|
| `GET /api/admin` | `{"admin": bool, "this_pc": bool, "pin_set": bool}`; also in `/api/status` as `access` |
| `POST /api/admin/login` | `{"pin"}`. 200 sets the `ph_admin` cookie (HttpOnly, SameSite=Strict, 30 days). 403 for a wrong PIN or no PIN set, 429 after 5 wrong PINs in 15 min |
| `POST /api/admin/logout` | Clears the cookie |
| `PUT /api/admin/pin` | `{"pin": "4-12 digits" or null}`. **This PC only**, even for admins. 204. Signs every device out |
| `GET /api/settings/streaming` | `{"tv_max_bitrate_mbps": float (0 = off), "max_transcodes": int (0 = no limit)}` |
| `PUT /api/settings/streaming` | Either field. Saved to config.toml (`[dlna] max_bitrate_mbps`, `[ffmpeg] max_transcodes`) and applied at once |

**Busy:** when every transcode slot is taken, `hls.m3u8`, HLS segments,
`stream.mp4` and DLNA `transcode.ts` return **503** with `Retry-After: 30`.
The JSON body is `{"detail", "busy": true, "limit"}`; DLNA gets plain text.
`/api/status` includes `transcodes: {"limit", "active": [labels]}`.

## LG TV app (session 9)

| Endpoint | Notes |
|---|---|
| `GET /api/hello` | `{"app": "picturehouse", "name", "version", "tv": "/tv/"}` with `Access-Control-Allow-Origin: *`, so the TV launcher (file:// origin) can find servers. Nothing private. |
| `GET /api/items/{id}/play?client=tv` | TV profile: `url` is `/file` (`mode` `direct`) when the DLNA profile and the TV bitrate limit allow, else the HLS playlist (`mode` `transcode`). `hls_url` is always set for video, as the fallback. |
| `GET /tv/` | The big-screen interface (`static/tv/`). |

## Photos (session 10)

Libraries of kind `photos` hold pictures. Each is an Item with `kind: "photo"`; its `art` URLs point at the picture's renditions.

| Endpoint | Notes |
|---|---|
| `GET /api/photos` | `{"albums": [{"key", "title", "path", "photos": n, "from", "to", "art": {"poster", "backdrop"}}]}`, one per folder, newest first |
| `GET /api/photos/recent?limit=60` | `{"photos": [Photo]}`, newest taken first |
| `GET /api/photos/albums/{key}` | `{"key", "title", "path", "photos": [Photo]}`, in the order taken |
| `GET /api/items/{id}/photo?size=thumb\|screen\|original` | An upright JPEG at most 480 / 1920 px (cached; HEIC converted), or the original file when browsers can show it (JPEG, PNG, WebP, GIF). The `v=` parameter in the URLs makes them safe to cache forever. 415 if the picture can't be read. |

**Photo:** `{"id", "kind": "photo", "title", "album", "album_key", "taken_at", "width", "height", "thumb", "screen", "original"}`

Also new: tracks carry `album_key` (the `/api/albums/{key}` key). `GET /api/continue?audiobooks=1` includes tracks over 20 minutes. `counts` has `photos` and `photo_albums`.

