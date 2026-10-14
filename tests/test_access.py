"""Admin access: this PC may change things, other devices need the PIN."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest_services import LAN, LOCAL, make_services
from picturehouse.web import access
from picturehouse.web.app import create_app


@pytest.fixture
def app(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    yield create_app(svc)
    svc.close()


def test_rules():
    assert not access.needs_admin("GET", "/api/films")
    assert not access.needs_admin("GET", "/api/items/3/file")
    assert not access.needs_admin("PUT", "/api/items/3/progress")
    assert not access.needs_admin("DELETE", "/api/hls/abc")
    assert not access.needs_admin("POST", "/dlna/control/ContentDirectory")
    assert access.needs_admin("POST", "/api/libraries")
    assert access.needs_admin("DELETE", "/api/libraries/Films")
    assert access.needs_admin("PUT", "/api/settings/metadata")
    assert access.needs_admin("GET", "/api/settings/metadata")
    assert access.needs_admin("GET", "/api/logs")
    assert access.needs_admin("POST", "/api/scan")
    assert access.needs_admin("POST", "/api/admin/shutdown")


def test_host_and_origin_checks():
    assert access.host_is_loopback("127.0.0.1:9595")
    assert access.host_is_loopback("localhost:9595")
    assert access.host_is_loopback("[::1]:9595")
    assert not access.host_is_loopback("evil.example:9595")  # DNS rebinding
    assert not access.host_is_loopback("192.168.1.2:9595")
    host = "127.0.0.1:9595"
    assert access.origin_ok("POST", "http://127.0.0.1:9595", None, host)
    assert access.origin_ok("POST", None, None, host)  # curl, the desktop app
    assert not access.origin_ok("POST", "https://evil.example", None, host)
    assert not access.origin_ok("POST", "null", None, host)
    assert not access.origin_ok("POST", None, "https://evil.example/page", host)
    assert access.origin_ok("GET", "https://evil.example", None, host)


def test_pin_hashing():
    stored = access.hash_pin("2468")
    assert "2468" not in stored
    assert access.verify_pin("2468", stored)
    assert not access.verify_pin("1357", stored)
    assert not access.verify_pin("2468", None)
    assert not access.verify_pin("2468", "garbage")
    assert access.valid_pin("1234") and not access.valid_pin("12a4") and not access.valid_pin("1")


def test_this_pc_is_admin(app):
    with TestClient(app, **LOCAL) as c:
        assert c.get("/api/admin").json() == {"admin": True, "this_pc": True, "pin_set": False}
        assert c.post("/api/scan", json={}).status_code == 202


def test_lan_device_can_browse_and_play_but_not_change(app):
    app.state.svc.scanner.scan()
    with TestClient(app, **LAN) as c:
        assert c.get("/api/admin").json()["admin"] is False
        films = c.get("/api/films").json()["items"]
        assert films
        item = films[0]["id"]
        assert c.get(f"/api/items/{item}/file").status_code == 200
        assert c.put(f"/api/items/{item}/progress", json={"position": 12}).status_code == 204
        r = c.post("/api/scan", json={})
        assert r.status_code == 403
        assert r.json()["admin_required"] and "PC" in r.json()["detail"]
        assert c.post("/api/libraries", json={"name": "x", "path": "C:/"}).status_code == 403
        assert c.get("/api/logs").status_code == 403
        assert c.get("/api/settings/metadata").status_code == 403
        # No PIN set yet: logging in can't work.
        assert c.post("/api/admin/login", json={"pin": "1234"}).status_code == 403


def test_loopback_address_with_foreign_host_is_not_trusted(app):
    """DNS rebinding: a hostile name that resolves to 127.0.0.1."""
    with TestClient(app, client=("127.0.0.1", 5000), base_url="http://evil.example:9595") as c:
        assert c.post("/api/scan", json={}).status_code == 403


def test_cross_site_post_refused_even_on_this_pc(app):
    with TestClient(app, **LOCAL) as c:
        r = c.post("/api/scan", json={}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r = c.post("/api/scan", json={}, headers={"Origin": "http://127.0.0.1:9595"})
        assert r.status_code == 202


def test_pin_lets_another_device_in(app):
    with TestClient(app, **LOCAL) as pc:
        assert pc.put("/api/admin/pin", json={"pin": "12"}).status_code == 400
        assert pc.put("/api/admin/pin", json={"pin": "2468"}).status_code == 204
    with TestClient(app, **LAN) as phone:
        r = phone.post("/api/scan", json={})
        assert r.status_code == 403 and "PIN" in r.json()["detail"]
        assert phone.post("/api/admin/login", json={"pin": "1111"}).status_code == 403
        r = phone.post("/api/admin/login", json={"pin": "2468"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert phone.get("/api/admin").json()["admin"] is True
        assert phone.post("/api/scan", json={}).status_code == 202
        # ...but the PIN itself can only be changed on the PC.
        assert phone.put("/api/admin/pin", json={"pin": "9999"}).status_code == 403
        phone.post("/api/admin/logout")
        assert phone.get("/api/admin").json()["admin"] is False


def test_changing_pin_signs_everyone_out(app):
    with TestClient(app, **LOCAL) as pc:
        pc.put("/api/admin/pin", json={"pin": "2468"})
    with TestClient(app, **LAN) as phone:
        phone.post("/api/admin/login", json={"pin": "2468"})
        assert phone.get("/api/admin").json()["admin"] is True
        with TestClient(app, **LOCAL) as pc:
            assert pc.put("/api/admin/pin", json={"pin": None}).status_code == 204
        assert phone.get("/api/admin").json() == {"admin": False, "this_pc": False,
                                                  "pin_set": False}  # fmt: skip


def test_wrong_pins_are_rate_limited(app):
    with TestClient(app, **LOCAL) as pc:
        pc.put("/api/admin/pin", json={"pin": "2468"})
    with TestClient(app, **LAN) as phone:
        for _ in range(access.MAX_FAILURES):
            assert phone.post("/api/admin/login", json={"pin": "0000"}).status_code == 403
        r = phone.post("/api/admin/login", json={"pin": "2468"})
        assert r.status_code == 429


def test_tampered_cookie_rejected(tmp_path):
    gate = access.AdminGate(tmp_path)
    good = gate.make_cookie()
    assert gate.check_cookie(good)
    assert not gate.check_cookie(good[:-2] + ("AA" if not good.endswith("AA") else "BB"))
    assert not gate.check_cookie(gate.make_cookie(now=0))  # expired
    assert not gate.check_cookie("not-a-cookie")


def test_admin_pin_from_cli_and_environment(tmp_path, monkeypatch):
    """Containers and NAS boxes: no browser is ever "this PC", so the PIN is
    set from the command line or PICTUREHOUSE_ADMIN_PIN at startup."""
    from typer.testing import CliRunner

    from picturehouse.cli import _apply_env_pin, app
    from picturehouse.paths import DataLayout

    data = tmp_path / "d"
    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")
    run = lambda *a: CliRunner().invoke(app, ["--config", str(cfg), "--data-dir", str(data), *a])  # noqa: E731
    assert "no admin PIN" in run("admin-pin").output
    assert run("admin-pin", "12").exit_code != 0
    assert run("admin-pin", "2468").exit_code == 0
    gate = access.AdminGate(data)
    assert gate.check_pin("2468")
    assert run("admin-pin", "--remove").exit_code == 0 and not gate.pin_set

    layout = DataLayout(data)
    monkeypatch.setenv("PICTUREHOUSE_ADMIN_PIN", "13579")
    _apply_env_pin(layout)
    assert gate.check_pin("13579")
    cookie = gate.make_cookie()
    _apply_env_pin(layout)  # unchanged: devices stay signed in
    assert gate.check_cookie(cookie)
    monkeypatch.setenv("PICTUREHOUSE_ADMIN_PIN", "abc")
    _apply_env_pin(layout)  # invalid: ignored, the old PIN stays
    assert gate.check_pin("13579")


def test_blank_admin_pin_in_the_app_file_warns(tmp_path, monkeypatch, caplog):
    """The ZimaOS app file ships with the PIN blank: say clearly that it's
    needed, rather than leave the console silently locked."""
    from picturehouse.cli import _apply_env_pin
    from picturehouse.paths import DataLayout

    layout = DataLayout(tmp_path / "d")
    monkeypatch.setenv("PICTUREHOUSE_ADMIN_PIN", "  ")
    with caplog.at_level("WARNING"):
        _apply_env_pin(layout)
    assert "no admin PIN" in caplog.text
    assert not access.AdminGate(layout.root).pin_set

    caplog.clear()
    access.AdminGate(layout.root).set_pin("2468")  # set later with the CLI
    with caplog.at_level("WARNING"):
        _apply_env_pin(layout)
    assert "no admin PIN" not in caplog.text
    assert access.AdminGate(layout.root).check_pin("2468")
