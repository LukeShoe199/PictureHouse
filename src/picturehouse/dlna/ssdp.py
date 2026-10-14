"""SSDP: answer M-SEARCH discovery and announce ourselves with NOTIFY.

Runs on plain threads (one receiver, one announcer) so it doesn't depend on
the event loop's datagram support. Multicast group 239.255.255.250:1900 is
shared with the OS's own SSDP service and other media servers, so the socket
uses SO_REUSEADDR.
"""

from __future__ import annotations

import contextlib
import email.utils
import logging
import random
import socket
import struct
import threading
import time
from collections.abc import Callable

from picturehouse import __version__

log = logging.getLogger(__name__)

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
MAX_AGE = 1800
ANNOUNCE_EVERY = 60.0
# Seconds after start at which to announce. TVs (seen with LG webOS) drop a
# server after its ssdp:byebye and won't re-add it from search replies, only
# from an ssdp:alive, so a restart is announced several times early on.
STARTUP_ANNOUNCE = (0.0, 1.0, 3.0, 10.0, 30.0, 60.0)

DEVICE_TYPE = "urn:schemas-upnp-org:device:MediaServer:1"
SERVICE_TYPES = (
    "urn:schemas-upnp-org:service:ContentDirectory:1",
    "urn:schemas-upnp-org:service:ConnectionManager:1",
)


def local_ipv4_addresses() -> list[str]:
    """IPv4 addresses of this host worth announcing on (no loopback/link-local)."""
    found: list[str] = []
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    # Linux often resolves the hostname to 127.0.1.1 only: add the default-route IP.
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET, nothing is sent
        found.append(s.getsockname()[0])
    out = []
    for ip in found:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in out:
            continue
        out.append(ip)
    return out


def ip_facing(peer: str) -> str | None:
    """Our address on the interface used to reach ``peer``."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((peer, 9))
            return s.getsockname()[0]
    except OSError:
        return None


def _headers(lines: list[str]) -> dict[str, str]:
    out = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().upper()] = v.strip()
    return out


class SSDPServer:
    def __init__(self, uuid: str, http_port: int, location_path: str = "/dlna/description.xml",
                 addresses: Callable[[], list[str]] = local_ipv4_addresses) -> None:  # fmt: skip
        self.uuid = uuid
        self.http_port = http_port
        self.location_path = location_path
        self._addresses = addresses
        self.server_header = f"{_os_token()} UPnP/1.0 DLNADOC/1.50 picturehouse/{__version__}"
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self.joined: list[str] = []
        self._seen: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------- types

    def notification_types(self) -> list[tuple[str, str]]:
        """(NT/ST, USN) pairs for everything we advertise."""
        u = f"uuid:{self.uuid}"
        pairs = [("upnp:rootdevice", f"{u}::upnp:rootdevice"), (u, u),
                 (DEVICE_TYPE, f"{u}::{DEVICE_TYPE}")]  # fmt: skip
        pairs += [(st, f"{u}::{st}") for st in SERVICE_TYPES]
        return pairs

    def location(self, ip: str) -> str:
        return f"http://{ip}:{self.http_port}{self.location_path}"

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", SSDP_PORT))
        sock.settimeout(1.0)
        for ip in self._addresses():
            mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(ip))
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.joined.append(ip)
            except OSError as e:
                log.warning("could not join SSDP multicast on %s: %s", ip, e)
        if not self.joined:  # last resort: let the OS pick the interface
            mreq = struct.pack("4sl", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self._sock = sock
        for target, name in ((self._receive, "ssdp-recv"), (self._announce, "ssdp-notify")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("SSDP started", extra={"interfaces": self.joined, "uuid": self.uuid})

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            self._send_notify("ssdp:byebye")
        for t in self._threads:
            t.join(timeout=3)
        if self._sock:
            self._sock.close()
        log.info("SSDP stopped")

    # ------------------------------------------------------------- receive

    def _receive(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(8192)
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            try:
                self._handle(data, addr)
            except Exception:
                log.exception("SSDP packet handling failed")

    def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode("utf-8", "replace")
        lines = text.split("\r\n")
        if not lines or not lines[0].upper().startswith("M-SEARCH"):
            return
        h = _headers(lines)
        if h.get("MAN", "").strip('"') != "ssdp:discover":
            return
        st = h.get("ST", "")
        if st == "ssdp:all":
            matches = self.notification_types()
        else:
            matches = [(nt, usn) for nt, usn in self.notification_types() if nt == st]
        ip = ip_facing(addr[0])
        self._log_search(addr[0], st, h.get("USER-AGENT", ""), bool(matches), ip)
        if not matches or not ip:
            return
        try:
            mx = max(0.0, min(float(h.get("MX", "1")), 3.0))
        except ValueError:
            mx = 1.0
        delay = random.uniform(0, min(mx, 1.0))
        threading.Timer(delay, self._respond, args=(addr, ip, matches)).start()

    def _log_search(self, source: str, st: str, ua: str, answered: bool, via: str | None) -> None:
        """One INFO line per device and search type per minute: enough to see
        whether a TV's discovery packets reach us, without flooding the log."""
        if source.startswith("127."):
            return
        key, now = (source, st), time.monotonic()
        if now - self._seen.get(key, -1e9) < 60:
            return
        self._seen[key] = now
        log.info("M-SEARCH from %s for %s: %s", source, st or "(none)",
                 f"answered via {via}" if answered and via else "not for us",
                 extra={"ua": ua})  # fmt: skip

    def _respond(self, addr: tuple[str, int], ip: str, matches: list[tuple[str, str]]) -> None:
        if self._sock is None or self._stop.is_set():
            return
        for st, usn in matches:
            msg = (
                "HTTP/1.1 200 OK\r\n"
                f"CACHE-CONTROL: max-age={MAX_AGE}\r\n"
                f"DATE: {email.utils.formatdate(usegmt=True)}\r\n"
                "EXT:\r\n"
                f"LOCATION: {self.location(ip)}\r\n"
                f"SERVER: {self.server_header}\r\n"
                f"ST: {st}\r\n"
                f"USN: {usn}\r\n"
                "Content-Length: 0\r\n\r\n"
            )
            with contextlib.suppress(OSError):
                self._sock.sendto(msg.encode(), addr)

    # ------------------------------------------------------------- announce

    def _announce(self) -> None:
        start = time.monotonic()
        for at in STARTUP_ANNOUNCE:  # UDP is lossy and TVs listen intermittently
            if self._stop.wait(max(0.0, at - (time.monotonic() - start))):
                return
            self._send_notify("ssdp:alive")
        while not self._stop.wait(ANNOUNCE_EVERY):
            self._send_notify("ssdp:alive")

    def _send_notify(self, nts: str) -> None:
        for ip in self.joined or self._addresses():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                s.bind((ip, 0))
            except OSError as e:
                log.debug("cannot announce on %s: %s", ip, e)
                continue
            with s:
                for nt, usn in self.notification_types():
                    msg = (
                        "NOTIFY * HTTP/1.1\r\n"
                        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                        f"NT: {nt}\r\n"
                        f"NTS: {nts}\r\n"
                        f"USN: {usn}\r\n"
                    )
                    if nts == "ssdp:alive":
                        msg += (
                            f"CACHE-CONTROL: max-age={MAX_AGE}\r\n"
                            f"LOCATION: {self.location(ip)}\r\n"
                            f"SERVER: {self.server_header}\r\n"
                        )
                    msg += "\r\n"
                    with contextlib.suppress(OSError):
                        s.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))


def _os_token() -> str:
    import platform

    return f"{platform.system()}/{platform.release()}"
