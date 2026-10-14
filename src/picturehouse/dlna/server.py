"""DLNA media server: SSDP + UPnP description/control/eventing + media endpoints."""

from __future__ import annotations

import http.client
import logging
import re
import threading
import uuid as uuidlib
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from picturehouse.db.models import MediaItem
from picturehouse.dlna import xmldocs
from picturehouse.dlna.content import ContentDirectory, NoSuchObject, content_features
from picturehouse.dlna.ssdp import SSDPServer, local_ipv4_addresses
from picturehouse.paths import long_path, resolve_in_root
from picturehouse.services import Services
from picturehouse.streaming import procs
from picturehouse.streaming.decide import decide_dlna

log = logging.getLogger(__name__)

XML = "text/xml; charset=utf-8"
_NPT = re.compile(r"npt\s*=\s*([\d:.]+)?\s*-")


def _load_uuid(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
        uuidlib.UUID(value)
        return value
    except (OSError, ValueError):
        value = str(uuidlib.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return value


def _npt_seconds(header: str | None) -> float:
    """Parse 'npt=1:02:03.5-' or 'npt=3723.5-' into seconds."""
    if not header or not (m := _NPT.search(header)) or not m.group(1):
        return 0.0
    parts = [float(p) for p in m.group(1).split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


class MediaFileResponse(FileResponse):
    """Video files go out in 1 MiB reads instead of Starlette's 64 KiB: a
    25 Mbps film is then ~3 thread hand-offs a second instead of ~50."""

    chunk_size = 1024 * 1024


class DLNAServer:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.uuid = _load_uuid(svc.layout.root / "dlna-uuid.txt")
        self.port = svc.settings.server.http_port
        self.ssdp = SSDPServer(self.uuid, self.port)
        self.system_update_id = 1
        self._subs: dict[str, str] = {}  # SID -> callback URL
        svc.scanner.on_change.append(self.content_changed)
        svc.matcher.on_change.append(self.content_changed)

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        try:
            self.ssdp.start()
        except OSError as e:
            log.error("SSDP could not start (UDP 1900): %s — TVs won't discover the server", e)

    def stop(self) -> None:
        self.ssdp.stop()

    def description_urls(self) -> list[str]:
        ips = self.ssdp.joined or local_ipv4_addresses()
        return [self.ssdp.location(ip) for ip in ips]

    def content_changed(self) -> None:
        self.system_update_id += 1
        for sid, callback in list(self._subs.items()):
            self._send_event(sid, callback)

    # ---------------------------------------------------------------- eventing

    def _send_event(self, sid: str, callback: str, seq: int = 0) -> None:
        body = xmldocs.event_propertyset({"SystemUpdateID": self.system_update_id,
                                          "ContainerUpdateIDs": ""}).encode()  # fmt: skip

        def send() -> None:
            try:
                u = urlsplit(callback)
                conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
                conn.request("NOTIFY", u.path or "/", body, {
                    "Content-Type": XML, "NT": "upnp:event", "NTS": "upnp:propchange",
                    "SID": sid, "SEQ": str(seq)})  # fmt: skip
                conn.getresponse().read()
                conn.close()
            except OSError as e:
                log.debug("event delivery to %s failed: %s", callback, e)

        threading.Thread(target=send, daemon=True).start()

    # ---------------------------------------------------------------- routes

    def router(self) -> APIRouter:
        r = APIRouter(prefix="/dlna", include_in_schema=False)
        svc = self.svc

        def tv_max_bitrate() -> int:
            """[dlna] max_bitrate_mbps in bits/s, read live so a change applies at once."""
            return int(svc.settings.dlna.max_bitrate_mbps * 1_000_000)

        def base_url(request: Request) -> str:
            host = request.headers.get("host") or f"{request.url.hostname}:{self.port}"
            return f"http://{host}"

        @r.get("/description.xml")
        def description(request: Request):
            xml = xmldocs.device_description(svc.settings.server.friendly_name, self.uuid,
                                             base_url(request))  # fmt: skip
            return Response(xml, media_type=XML)

        @r.get("/cds.xml")
        def cds_scpd():
            return Response(xmldocs.CDS_SCPD, media_type=XML)

        @r.get("/cms.xml")
        def cms_scpd():
            return Response(xmldocs.CMS_SCPD, media_type=XML)

        @r.get("/icon-{size}.png")
        def icon(size: int):
            if size not in (48, 120):
                raise HTTPException(404)
            return Response(xmldocs.icon_png(size), media_type="image/png")

        async def parse_soap(request: Request) -> tuple[str, dict[str, str]]:
            action = request.headers.get("soapaction", "").strip('"').rpartition("#")[2]
            body = await request.body()
            args: dict[str, str] = {}
            try:
                root = ET.fromstring(body)
                for el in root.iter():
                    if el.tag.rpartition("}")[2] == action:
                        for child in el:
                            args[child.tag.rpartition("}")[2]] = (child.text or "").strip()
                        break
            except ET.ParseError:
                pass
            return action, args

        def fault(code: int, desc: str) -> Response:
            return Response(xmldocs.soap_fault(code, desc), status_code=500, media_type=XML)

        @r.post("/cds/control")
        async def cds_control(request: Request):
            action, args = await parse_soap(request)
            svc_type = xmldocs.CDS_TYPE
            if action == "Browse":
                cd = ContentDirectory(base_url(request), tv_max_bitrate())
                oid = args.get("ObjectID", "0") or "0"
                flag = args.get("BrowseFlag", "BrowseDirectChildren")
                try:
                    start = int(args.get("StartingIndex") or 0)
                    count = int(args.get("RequestedCount") or 0)
                except ValueError:
                    return fault(402, "Invalid Args")

                def run():
                    with svc.db.session() as s:
                        return cd.browse(s, oid, flag, start, count)

                import anyio

                try:
                    didl, n, total = await anyio.to_thread.run_sync(run)
                except NoSuchObject:
                    return fault(701, "No such object")
                log.info("Browse %s %s [%d+%d] -> %d/%d", oid, flag, start, count, n, total,
                         extra={"ua": request.headers.get("user-agent", "")})  # fmt: skip
                return Response(xmldocs.soap_response(svc_type, "Browse", {
                    "Result": didl, "NumberReturned": n, "TotalMatches": total,
                    "UpdateID": self.system_update_id}), media_type=XML)  # fmt: skip
            if action == "GetSystemUpdateID":
                return Response(xmldocs.soap_response(svc_type, action,
                                {"Id": self.system_update_id}), media_type=XML)  # fmt: skip
            if action == "GetSearchCapabilities":
                return Response(xmldocs.soap_response(svc_type, action, {"SearchCaps": ""}),
                                media_type=XML)  # fmt: skip
            if action == "GetSortCapabilities":
                return Response(xmldocs.soap_response(svc_type, action, {"SortCaps": ""}),
                                media_type=XML)  # fmt: skip
            log.info("unsupported ContentDirectory action %r", action)
            return fault(401, "Invalid Action")

        @r.post("/cms/control")
        async def cms_control(request: Request):
            action, args = await parse_soap(request)
            t = xmldocs.CMS_TYPE
            if action == "GetProtocolInfo":
                source = ",".join([
                    "http-get:*:video/x-mkv:*", "http-get:*:video/x-matroska:*",
                    "http-get:*:video/mp4:*", "http-get:*:video/mpeg:*",
                    "http-get:*:video/x-msvideo:*", "http-get:*:video/quicktime:*",
                    "http-get:*:audio/mpeg:*", "http-get:*:audio/x-flac:*",
                    "http-get:*:audio/mp4:*", "http-get:*:image/jpeg:*"])  # fmt: skip
                return Response(xmldocs.soap_response(t, action, {"Source": source, "Sink": ""}),
                                media_type=XML)  # fmt: skip
            if action == "GetCurrentConnectionIDs":
                return Response(xmldocs.soap_response(t, action, {"ConnectionIDs": "0"}),
                                media_type=XML)  # fmt: skip
            if action == "GetCurrentConnectionInfo":
                return Response(xmldocs.soap_response(t, action, {
                    "RcsID": -1, "AVTransportID": -1, "ProtocolInfo": "",
                    "PeerConnectionManager": "", "PeerConnectionID": -1,
                    "Direction": "Output", "Status": "OK"}), media_type=XML)  # fmt: skip
            return fault(401, "Invalid Action")

        @r.api_route("/{service}/event", methods=["SUBSCRIBE", "UNSUBSCRIBE"])
        def event(service: str, request: Request):
            if service not in ("cds", "cms"):
                raise HTTPException(404)
            if request.method == "UNSUBSCRIBE":
                self._subs.pop(request.headers.get("sid", ""), None)
                return Response(status_code=200)
            sid = request.headers.get("sid") or f"uuid:{uuidlib.uuid4()}"
            callback = request.headers.get("callback", "").strip("<> ").split("><")[0]
            if service == "cds" and callback:
                self._subs[sid] = callback
                self._send_event(sid, callback)
            return Response(status_code=200, headers={"SID": sid, "TIMEOUT": "Second-1800",
                                                      "Content-Length": "0"})  # fmt: skip

        def load(item_id: int) -> tuple[MediaItem, Path]:
            with svc.db.session() as s:
                item = s.get(MediaItem, item_id)
                if item is None or not item.available:
                    raise HTTPException(404)
                path = long_path(resolve_in_root(Path(item.root.path), item.rel_path).absolute())
                return item, path

        @r.api_route("/media/{item_id}", methods=["GET", "HEAD"])
        def media_direct(item_id: int):
            item, path = load(item_id)
            if not path.is_file():
                raise HTTPException(404)
            from picturehouse.dlna.content import _dlna_mime

            return MediaFileResponse(path, media_type=_dlna_mime(item), headers={
                "transferMode.dlna.org": "Streaming",
                "contentFeatures.dlna.org": content_features(True),
                "Accept-Ranges": "bytes"})  # fmt: skip

        @r.api_route("/media/{item_id}/transcode.{ext}", methods=["GET", "HEAD"])
        def media_transcode(item_id: int, ext: str, request: Request):
            if ext not in ("ts", "mp3"):
                raise HTTPException(404)
            item, path = load(item_id)
            if not path.is_file():
                raise HTTPException(404)
            start = _npt_seconds(request.headers.get("timeseekrange.dlna.org"))
            mime = "video/mpeg" if ext == "ts" else "audio/mpeg"
            headers = {"transferMode.dlna.org": "Streaming",
                       "contentFeatures.dlna.org": content_features(False),
                       "Accept-Ranges": "none"}  # fmt: skip
            if item.duration:
                headers["TimeSeekRange.dlna.org"] = (
                    f"npt={start:.3f}-{item.duration:.3f}/{item.duration:.3f}"
                )
            if request.method == "HEAD":
                return Response(status_code=200, media_type=mime, headers=headers)
            d = decide_dlna(item, tv_max_bitrate())
            ff = svc.ffmpeg.path
            cmd = (procs.mpegts_cmd(ff, path, start=start, copy_video=d.copy_video, hdr=d.hdr,
                                    max_bitrate=d.max_bitrate) if ext == "ts"
                   else procs.mp3_cmd(ff, path, start=start))  # fmt: skip
            lease = None
            if ext == "ts" and not d.copy_video:  # only video encodes are capped
                client = request.client.host if request.client else "?"
                try:
                    lease = procs.Lease(f"dlna:{client}:{item.id}", f"TV at {client}: {item.title}")
                except procs.TranscodeBusy as e:
                    return Response(str(e), status_code=503, media_type="text/plain",
                                    headers={"Retry-After": "30"})  # fmt: skip
            return StreamingResponse(
                procs.stream_with_lease(cmd, label=f"dlna:{item.id}:{ext}", lease=lease),
                media_type=mime, headers=headers)  # fmt: skip

        return r
