from __future__ import annotations

import socket
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from conftest_services import make_services
from picturehouse.dlna import ssdp
from picturehouse.dlna.server import DLNAServer, _npt_seconds
from picturehouse.web.app import create_app

NS = {"d": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/", "dc": "http://purl.org/dc/elements/1.1/",
      "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/"}  # fmt: skip


@pytest.fixture(scope="module")
def client(media_library, tmp_path_factory):
    svc = make_services(media_library, tmp_path_factory.mktemp("dlna") / "data")
    svc.scanner.scan()
    dlna = DLNAServer(svc)  # SSDP not started: routes only
    with TestClient(create_app(svc, dlna=dlna), base_url="http://192.0.2.10:9595") as c:
        c.dlna = dlna
        yield c
    svc.close()


def browse(client, oid, flag="BrowseDirectChildren", start=0, count=0):
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        '<s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        f"<ObjectID>{oid}</ObjectID><BrowseFlag>{flag}</BrowseFlag><Filter>*</Filter>"
        f"<StartingIndex>{start}</StartingIndex><RequestedCount>{count}</RequestedCount>"
        "<SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>"
    )
    r = client.post("/dlna/cds/control", content=body, headers={
        "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"',
        "Content-Type": "text/xml"})  # fmt: skip
    return r


def didl(r):
    env = ET.fromstring(r.content)
    return ET.fromstring(env.find(".//Result").text), env


def test_description(client):
    r = client.get("/dlna/description.xml")
    assert r.status_code == 200
    assert f"uuid:{client.dlna.uuid}" in r.text
    assert "urn:schemas-upnp-org:device:MediaServer:1" in r.text
    assert "http://192.0.2.10:9595/" in r.text
    ET.fromstring(r.content)  # well-formed
    for path in ("/dlna/cds.xml", "/dlna/cms.xml"):
        ET.fromstring(client.get(path).content)
    assert client.get("/dlna/icon-120.png").content[:4] == b"\x89PNG"


def test_root_and_tree(client):
    root, env = didl(browse(client, "0"))
    titles = [c.find("dc:title", NS).text for c in root.findall("d:container", NS)]
    assert titles == ["Films", "TV Shows", "Music", "Home Videos"]
    assert env.find(".//TotalMatches").text == "4"

    films, _ = didl(browse(client, "films"))
    items = films.findall("d:item", NS)
    assert len(items) == 9
    res = items[0].find("d:res", NS)
    assert res.text.startswith("http://192.0.2.10:9595/dlna/media/")  # host the TV used

    shows, _ = didl(browse(client, "tv"))
    test_show = next(c for c in shows.findall("d:container", NS)
                     if c.find("dc:title", NS).text == "Test Show")  # fmt: skip
    seasons, _ = didl(browse(client, test_show.get("id")))
    assert [c.find("dc:title", NS).text for c in seasons.findall("d:container", NS)] == [
        "Season 1", "Season 2", "Specials"]  # fmt: skip
    eps, _ = didl(browse(client, seasons.findall("d:container", NS)[0].get("id")))
    assert [e.find("dc:title", NS).text for e in eps.findall("d:item", NS)] == [
        "01. Pilot", "02. The Second One"]  # fmt: skip


def test_paging(client):
    page, env = didl(browse(client, "films", start=2, count=3))
    assert len(page.findall("d:item", NS)) == 3
    assert env.find(".//NumberReturned").text == "3" and env.find(".//TotalMatches").text == "9"


def test_metadata_and_errors(client):
    films, _ = didl(browse(client, "films"))
    oid = films.find("d:item", NS).get("id")
    meta, _ = didl(browse(client, oid, "BrowseMetadata"))
    assert meta.find("d:item", NS).get("id") == oid
    container, _ = didl(browse(client, "films", "BrowseMetadata"))
    assert container.find("d:container", NS).get("parentID") == "0"
    bad = browse(client, "show.nope")
    assert bad.status_code == 500 and b"<errorCode>701</errorCode>" in bad.content


def test_webm_offers_transcode_first(client):
    films, _ = didl(browse(client, "films"))
    web = next(i for i in films.findall("d:item", NS)
               if i.find("dc:title", NS).text.startswith("Web Rip"))  # fmt: skip
    first = web.find("d:res", NS)
    assert first.text.endswith("/transcode.ts")
    assert "DLNA.ORG_OP=10" in first.get("protocolInfo")


def test_media_headers_and_range(client):
    films, _ = didl(browse(client, "films"))
    url = films.find("d:item/d:res", NS).text.replace("http://192.0.2.10:9595", "")
    head = client.head(url)
    assert head.headers["transferMode.dlna.org"] == "Streaming"
    assert "DLNA.ORG_OP=01" in head.headers["contentFeatures.dlna.org"]
    r = client.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and len(r.content) == 10


def test_transcode_ts_stream(client):
    films, _ = didl(browse(client, "films"))
    web = next(i for i in films.findall("d:item", NS)
               if i.find("dc:title", NS).text.startswith("Web Rip"))  # fmt: skip
    url = web.find("d:res", NS).text.replace("http://192.0.2.10:9595", "")
    r = client.get(url, headers={"TimeSeekRange.dlna.org": "npt=0.5-"})
    assert r.status_code == 200 and r.content[:1] == b"\x47"  # MPEG-TS sync byte
    assert r.headers["TimeSeekRange.dlna.org"].startswith("npt=0.500-")


def test_cms_and_subscribe(client):
    r = client.post(
        "/dlna/cms/control",
        content=b"<x/>",
        headers={
            "SOAPACTION": '"urn:schemas-upnp-org:service:ConnectionManager:1#GetProtocolInfo"'
        },
    )
    assert r.status_code == 200 and b"video/x-mkv" in r.content
    r = client.request("SUBSCRIBE", "/dlna/cds/event", headers={"NT": "upnp:event"})
    assert r.status_code == 200 and r.headers["SID"].startswith("uuid:")


@pytest.mark.parametrize(("hdr", "secs"), [
    ("npt=0-", 0.0), ("npt=12.5-", 12.5), ("npt=1:02:03.5-", 3723.5), (None, 0.0), ("bytes=0-", 0.0),
])  # fmt: skip
def test_npt(hdr, secs):
    assert _npt_seconds(hdr) == secs


# ------------------------------------------------------------------ SSDP


def test_msearch_response():
    srv = ssdp.SSDPServer("1234-uuid", 9595, addresses=lambda: ["127.0.0.1"])
    sent = []

    class FakeSock:
        def sendto(self, data, addr):
            sent.append(data.decode())

    srv._sock = FakeSock()
    srv._respond(("127.0.0.1", 5000), "192.0.2.10",
                 [m for m in srv.notification_types() if m[0].endswith("MediaServer:1")])  # fmt: skip
    (msg,) = sent
    assert msg.startswith("HTTP/1.1 200 OK")
    assert "LOCATION: http://192.0.2.10:9595/dlna/description.xml" in msg
    assert "USN: uuid:1234-uuid::urn:schemas-upnp-org:device:MediaServer:1" in msg


def test_ssdp_live_discovery():
    """Real multicast round trip on this machine (skipped where multicast is blocked)."""
    ips = ssdp.local_ipv4_addresses()
    if not ips:
        pytest.skip("no non-loopback IPv4 interface")
    srv = ssdp.SSDPServer("live-test-uuid", 9595)
    try:
        srv.start()
    except OSError as e:
        pytest.skip(f"cannot bind SSDP: {e}")
    try:
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
               "MX: 1\r\nST: uuid:live-test-uuid\r\n\r\n")  # fmt: skip
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ips[0]))
            s.bind((ips[0], 0))
            s.settimeout(3)
            s.sendto(msg.encode(), ("239.255.255.250", 1900))
            try:
                while True:
                    data, _ = s.recvfrom(4096)
                    if b"live-test-uuid" in data:
                        break
            except TimeoutError:
                pytest.skip("no multicast loopback on this host")
        assert b"LOCATION: http://" in data
    finally:
        srv.stop()


def test_announces_repeatedly_after_start(monkeypatch):
    """Regression: after a restart the LG TV ignored search replies and only
    re-added the server on an ssdp:alive; two quick notifies then a 2-minute
    gap left it invisible."""
    monkeypatch.setattr(ssdp, "STARTUP_ANNOUNCE", (0.0, 0.05, 0.1, 0.2))
    monkeypatch.setattr(ssdp, "ANNOUNCE_EVERY", 0.2)
    srv = ssdp.SSDPServer("u", 9595, addresses=lambda: ["127.0.0.1"])
    sent = []
    srv._send_notify = lambda nts: sent.append(nts)
    import threading
    import time

    t = threading.Thread(target=srv._announce)
    t.start()
    time.sleep(0.5)
    srv._stop.set()
    t.join(2)
    assert len(sent) >= 5 and set(sent) == {"ssdp:alive"}


def test_tv_bitrate_limit_offers_conversion_first(client):
    svc = client.app.state.svc
    original = svc.settings
    svc.settings = original.model_copy(
        update={"dlna": original.dlna.model_copy(update={"max_bitrate_mbps": 0.01})}
    )
    try:
        films, _ = didl(browse(client, "films"))
        assert all(i.find("d:res", NS).text.endswith("/transcode.ts")
                   for i in films.findall("d:item", NS))  # fmt: skip
    finally:
        svc.settings = original
    films, _ = didl(browse(client, "films"))
    assert any(not i.find("d:res", NS).text.endswith("/transcode.ts")
               for i in films.findall("d:item", NS))  # fmt: skip


def test_transcode_refused_when_server_busy(client, monkeypatch):
    import time

    from picturehouse.streaming import procs

    monkeypatch.setattr(procs, "QUEUE_WAIT", 0.5)
    procs.slots.limit = 1
    procs.slots.acquire("someone-else", "Phone: Some film")
    films, _ = didl(browse(client, "films"))
    web = next(i for i in films.findall("d:item", NS)
               if i.find("dc:title", NS).text.startswith("Web Rip"))  # fmt: skip
    url = web.find("d:res", NS).text.replace("http://192.0.2.10:9595", "")
    t0 = time.monotonic()
    r = client.get(url)
    assert r.status_code == 503 and "already converting" in r.text
    assert time.monotonic() - t0 >= 0.4  # it queued first
    procs.slots.release("someone-else")
