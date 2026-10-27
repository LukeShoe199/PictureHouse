from __future__ import annotations

import json
import ssl

from cryptography import x509
from fastapi.testclient import TestClient

from conftest_services import LOCAL, make_services
from picturehouse.tls import ca_fingerprint, ensure_certificates
from picturehouse.web.app import create_app


def _sans(path):
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return cert, {str(i) for i in san.get_values_for_type(x509.IPAddress)}


def test_certificates_issue_and_verify(tmp_path):
    files = ensure_certificates(tmp_path / "tls", ["192.168.1.40"])
    cert, ips = _sans(files.cert)
    assert {"192.168.1.40", "127.0.0.1"} <= ips
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 398  # Apple limit
    # The server certificate chains to our CA (what a phone checks once the CA is installed).
    ctx = ssl.create_default_context(cafile=str(files.ca_cert))
    ctx.load_cert_chain(files.cert, files.key)  # key matches cert
    ca = x509.load_pem_x509_certificate(files.ca_cert.read_bytes())
    cert.verify_directly_issued_by(ca)


def test_reissue_on_ip_change_keeps_ca(tmp_path):
    files = ensure_certificates(tmp_path / "tls", ["192.168.1.40"])
    fp, first = ca_fingerprint(files), files.cert.read_bytes()
    ensure_certificates(tmp_path / "tls", ["192.168.1.40"])
    assert files.cert.read_bytes() == first  # nothing changed: nothing reissued
    ensure_certificates(tmp_path / "tls", ["10.0.0.5"])
    _, ips = _sans(files.cert)
    assert "10.0.0.5" in ips and files.cert.read_bytes() != first
    assert ca_fingerprint(files) == fp  # phones don't need the CA reinstalled


def test_pwa_routes_and_https_status(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "d")
    files = ensure_certificates(tmp_path / "d" / "tls", ["192.168.1.40"])
    with TestClient(create_app(svc, https={"port": 9596, "files": files}), **LOCAL) as c:
        m = c.get("/manifest.webmanifest")
        assert m.headers["content-type"].startswith("application/manifest+json")
        body = json.loads(m.text)
        assert body["display"] == "standalone" and body["start_url"] == "/"
        for icon in body["icons"]:
            r = c.get(icon["src"])
            assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
        assert c.get("/icons/icon-999.png").status_code == 404
        st = c.get("/api/status").json()["https"]
        assert st["enabled"] and st["port"] == 9596 and st["ca_fingerprint"].count(":") == 31
        ca = c.get("/ca.crt")
        assert ca.status_code == 200 and b"BEGIN CERTIFICATE" in ca.content
        assert "ca.key" not in ca.text and b"PRIVATE" not in ca.content
        # Opened, not saved: Safari before iOS 13 can't download files, only
        # open a certificate (which starts the profile installer).
        assert ca.headers["content-type"] == "application/x-x509-ca-cert"
        assert ca.headers["content-disposition"].startswith("inline;")
        assert 'filename="PictureHouse-CA.crt"' in ca.headers["content-disposition"]
    with TestClient(create_app(svc), **LOCAL) as c:
        assert c.get("/api/status").json()["https"] == {"enabled": False}
        assert c.get("/ca.crt").status_code == 404
    svc.close()
