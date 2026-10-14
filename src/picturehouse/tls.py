"""Local HTTPS: a private certificate authority and a server certificate.

Browsers only allow casting (Google Cast web sender), service workers and
"install app" on secure origins. A LAN address over plain HTTP isn't one, so
PictureHouse can also listen on HTTPS (``[server] https_port``).

There's no public certificate for a LAN IP, so we make our own:

* a **CA** (10 years) generated once into ``<data>/tls/``. Install
  ``ca.crt`` on each phone/tablet once (the console's Library page links to
  it) and the browser trusts the server;
* a **server certificate** (397 days, the most Apple accepts) signed by that
  CA, naming every local IP, the host name and ``localhost``. It's re-issued
  automatically when the IPs change or it nears expiry, without touching the
  CA, so nothing needs reinstalling.

The CA key never leaves the data directory. DLNA stays on plain HTTP (TVs
don't do HTTPS).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import logging
import socket
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)

CA_DAYS = 3650
CERT_DAYS = 397  # Apple rejects TLS server certificates valid for longer
RENEW_BEFORE = dt.timedelta(days=30)


@dataclass(frozen=True)
class TlsFiles:
    dir: Path

    @property
    def ca_cert(self) -> Path:
        return self.dir / "ca.crt"

    @property
    def ca_key(self) -> Path:
        return self.dir / "ca.key"

    @property
    def cert(self) -> Path:
        return self.dir / "server.crt"

    @property
    def key(self) -> Path:
        return self.dir / "server.key"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))  # fmt: skip
    with contextlib.suppress(OSError):  # Windows ignores most mode bits
        path.chmod(0o600)


def _ensure_ca(files: TlsFiles) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    if files.ca_cert.exists() and files.ca_key.exists():
        cert = x509.load_pem_x509_certificate(files.ca_cert.read_bytes())
        key = serialization.load_pem_private_key(files.ca_key.read_bytes(), password=None)
        return cert, key
    key = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"PictureHouse local CA ({host})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PictureHouse"),
    ])  # fmt: skip
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(minutes=5))
        .not_valid_after(_now() + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )  # fmt: skip
    files.dir.mkdir(parents=True, exist_ok=True)
    _write_key(files.ca_key, key)
    files.ca_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("created local certificate authority", extra={"path": str(files.ca_cert)})
    return cert, key


def _names(ips: list[str]) -> tuple[list[str], list[str]]:
    host = socket.gethostname()
    dns = sorted({host, f"{host}.local", f"{host.lower()}.local", "localhost"})
    addrs = sorted({*ips, "127.0.0.1"})
    return dns, addrs


def _covers(cert: x509.Certificate, dns: list[str], addrs: list[str]) -> bool:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have_dns = set(san.get_values_for_type(x509.DNSName))
    have_ip = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    return set(dns) <= have_dns and set(addrs) <= have_ip


def ensure_certificates(tls_dir: Path, ips: list[str]) -> TlsFiles:
    """Make sure a CA and a current server certificate exist. Idempotent."""
    files = TlsFiles(tls_dir)
    ca_cert, ca_key = _ensure_ca(files)
    dns, addrs = _names(ips)
    if files.cert.exists() and files.key.exists():
        cert = x509.load_pem_x509_certificate(files.cert.read_bytes())
        fresh = cert.not_valid_after_utc - _now() > RENEW_BEFORE
        if fresh and _covers(cert, dns, addrs) and cert.issuer == ca_cert.subject:
            return files
    key = ec.generate_private_key(ec.SECP256R1())
    sans = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(a)) for a in addrs]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[0])]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - dt.timedelta(minutes=5))
        .not_valid_after(_now() + dt.timedelta(days=CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )  # fmt: skip
    _write_key(files.key, key)
    files.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    log.info("issued HTTPS certificate", extra={"names": dns + addrs})
    return files


def ca_fingerprint(files: TlsFiles) -> str:
    """SHA-256 fingerprint of the CA, for checking the right one was installed."""
    cert = x509.load_pem_x509_certificate(files.ca_cert.read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()
