"""Who may change things.

* **This PC** (requests from loopback with a local Host header) may do
  anything: it is the person who installed PictureHouse.
* **Other devices** on the LAN may browse, play and save their own watch
  progress. Everything else under /api that changes state (libraries,
  settings, scans, metadata fixes...) or exposes internals (logs, metadata
  searches that spend API quota) needs the admin PIN, which can only be set
  from the PC. With no PIN set, those actions are PC-only.

Also guarded here, because trusting "this PC" opens two browser attacks:
  * CSRF: a web page open on the PC posting to http://127.0.0.1:9595. State-
    changing requests whose Origin isn't this server are refused.
  * DNS rebinding: a hostile name resolving to 127.0.0.1. Loopback trust only
    applies when the Host header is a loopback name/address.

The PIN is stored as a salted PBKDF2 hash; a PIN login sets an HttpOnly,
SameSite=Strict cookie signed with a per-install secret. Wrong PINs are
rate-limited per client address.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

COOKIE = "ph_admin"
COOKIE_DAYS = 30
PBKDF2_ROUNDS = 200_000
MAX_FAILURES = 5  # wrong PINs per client...
FAILURE_WINDOW = 15 * 60  # ...per 15 minutes

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Writes any device may make: its own watch progress and its own HLS sessions.
OPEN_WRITES = [
    ("PUT", re.compile(r"^/api/items/\d+/progress$")),
    ("DELETE", re.compile(r"^/api/hls/[^/]+$")),
    ("POST", re.compile(r"^/api/admin/(login|logout)$")),
]
# Reads that are admin-only: they expose internals or spend API quota.
ADMIN_READS = [
    re.compile(r"^/api/browse$"),  # the server's folder names are private
    re.compile(r"^/api/logs$"),
    re.compile(r"^/api/devices$"),
    re.compile(r"^/api/settings/"),
    re.compile(r"^/api/items/\d+/candidates$"),
    re.compile(r"^/api/shows/[^/]+/candidates$"),
]

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def is_loopback(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return addr == "localhost"


def host_is_loopback(host_header: str | None) -> bool:
    """Host header names this machine by a loopback name (DNS-rebinding guard)."""
    if not host_header:
        return False
    host = (
        host_header.rsplit(":", 1)[0]
        if not host_header.startswith("[")
        else host_header.split("]")[0] + "]"
    )
    return host.lower() in _LOOPBACK_HOSTS or is_loopback(host.strip("[]"))


def needs_admin(method: str, path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if method in SAFE_METHODS:
        return any(r.match(path) for r in ADMIN_READS)
    return not any(m == method and r.match(path) for m, r in OPEN_WRITES)


def origin_ok(method: str, origin: str | None, referer: str | None, host: str | None) -> bool:
    """CSRF guard for state-changing requests: a browser sends Origin (or at
    least Referer); it must be this server. Non-browser clients send neither."""
    if method in SAFE_METHODS:
        return True
    src = origin or referer
    if not src or src == "null":
        return origin is None  # "null" Origin (sandboxed/file pages) is refused
    try:
        return urlsplit(src).netloc.lower() == (host or "").lower()
    except ValueError:
        return False


def hash_pin(pin: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def valid_pin(pin: str) -> bool:
    return bool(re.fullmatch(r"\d{4,12}", pin or ""))


@dataclass
class AdminGate:
    """Holds the PIN hash, the cookie-signing secret and the wrong-PIN counters.

    Both live as small files in the data directory (``admin-pin``,
    ``admin.key``), not in config.toml, so editing the config can't expose them.
    """

    data_dir: Path
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def secret_file(self) -> Path:
        return self.data_dir / "admin.key"

    @property
    def pin_file(self) -> Path:
        return self.data_dir / "admin-pin"

    def pin_hash(self) -> str | None:
        try:
            return self.pin_file.read_text(encoding="ascii").strip() or None
        except OSError:
            return None

    @property
    def pin_set(self) -> bool:
        return self.pin_hash() is not None

    def set_pin(self, pin: str | None) -> None:
        """Set (or with None, remove) the PIN. Signs out every device."""
        if pin is None:
            self.pin_file.unlink(missing_ok=True)
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.pin_file.write_text(hash_pin(pin), encoding="ascii")
        self.rotate()

    def check_pin(self, pin: str) -> bool:
        return verify_pin(pin, self.pin_hash())

    def _secret(self) -> bytes:
        try:
            data = self.secret_file.read_bytes()
            if len(data) >= 32:
                return data
        except OSError:
            pass
        return self.rotate()

    def rotate(self) -> bytes:
        """New secret: every existing admin cookie stops working."""
        data = secrets.token_bytes(32)
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_bytes(data)
        return data

    def make_cookie(self, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + COOKIE_DAYS * 86400)
        msg = str(expires).encode()
        sig = hmac.new(self._secret(), msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(msg + b"." + sig).decode().rstrip("=")

    def check_cookie(self, value: str | None, now: float | None = None) -> bool:
        if not value:
            return False
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            msg, sig = raw.split(b".", 1)
            expected = hmac.new(self._secret(), msg, hashlib.sha256).digest()
            return hmac.compare_digest(sig, expected) and int(msg) > (
                time.time() if now is None else now
            )
        except (ValueError, TypeError):
            return False

    def locked_out(self, client: str) -> int:
        """Seconds until this client may try again (0 = may try now)."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW]
            self._failures[client] = recent
            if len(recent) < MAX_FAILURES:
                return 0
            return int(FAILURE_WINDOW - (now - recent[0])) + 1

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(time.monotonic())

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)
