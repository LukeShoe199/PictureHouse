"""At most N video conversions at once; more viewers are told the server is busy."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest_services import LOCAL, make_services
from picturehouse.streaming import procs
from picturehouse.streaming.hls import HlsManager
from picturehouse.web.app import create_app


def test_cap_and_same_viewer_shares_a_slot():
    s = procs.TranscodeSlots(2)
    s.acquire("tv", "TV: Film A")
    s.acquire("phone", "Phone: Film B")
    s.acquire("tv", "TV: Film A")  # the TV seeking: shares its slot
    with pytest.raises(procs.TranscodeBusy) as e:
        s.acquire("laptop", "Laptop: Film C", wait=0.2)
    assert e.value.holders == ["TV: Film A", "Phone: Film B"]
    assert "already converting 2 videos" in str(e.value)
    s.release("tv")
    with pytest.raises(procs.TranscodeBusy):  # the TV still holds one
        s.acquire("laptop", "Laptop: Film C", wait=0.1)
    s.release("tv")
    s.acquire("laptop", "Laptop: Film C", wait=0.1)
    assert s.holders() == ["Phone: Film B", "Laptop: Film C"]


def test_queued_viewer_gets_the_next_free_slot():
    s = procs.TranscodeSlots(1)
    s.acquire("a", "A")
    threading.Timer(0.3, s.release, args=("a",)).start()
    t0 = time.monotonic()
    s.acquire("b", "B", wait=5)
    assert 0.2 < time.monotonic() - t0 < 3


def test_zero_means_unlimited():
    s = procs.TranscodeSlots(0)
    for k in range(10):
        s.acquire(str(k), str(k), wait=0)


def test_slot_never_streamed_from_is_reclaimed(monkeypatch):
    monkeypatch.setattr(procs, "UNCLAIMED_LEASE", 0.1)
    s = procs.TranscodeSlots(1)
    s.acquire("a", "A")  # response created but never sent
    time.sleep(0.2)
    s.acquire("b", "B", wait=0)
    s.started("b")
    time.sleep(0.2)
    with pytest.raises(procs.TranscodeBusy):  # a started stream keeps its slot
        s.acquire("c", "C", wait=0)


def test_lease_releases_once():
    procs.slots.limit = 1
    lease = procs.Lease("a", "A")
    lease.release()
    lease.release()
    procs.Lease("b", "B", wait=0).release()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_hls_sessions_hold_slots(tmp_path):
    procs.slots.limit = 1
    mgr = HlsManager(Path(shutil.which("ffmpeg")), tmp_path / "hls")
    try:
        src = tmp_path / "x.mp4"
        src.write_bytes(b"")
        a = mgr.open(1, src, 60, client="player-1", who="Chrome: Film A")
        # The same player moving on to another item keeps its one slot.
        b = mgr.open(2, src, 60, client="player-1", who="Chrome: Film B")
        with pytest.raises(procs.TranscodeBusy):
            procs.slots.acquire("other", "Phone", wait=0)
        mgr.close(a.id)
        mgr.close(b.id)
        mgr.open(3, src, 60, client="player-2", who="Phone: Film C")
        assert procs.slots.holders() == ["Phone: Film C"]
    finally:
        mgr.shutdown()


def test_api_says_busy(media_library, tmp_path, monkeypatch):
    monkeypatch.setattr(procs, "QUEUE_WAIT", 0.1)
    svc = make_services(media_library, tmp_path / "data")
    svc.scanner.scan()
    try:
        with TestClient(create_app(svc), **LOCAL) as c:
            procs.slots.limit = 1
            procs.slots.acquire("someone", "LG TV: Another film")
            film = next(i for i in c.get("/api/films").json()["items"])
            r = c.get(f"/api/items/{film['id']}/hls.m3u8?c=me")
            assert r.status_code == 503
            assert r.json()["busy"] and "LG TV: Another film" in r.json()["detail"]
            assert r.headers["retry-after"] == "30"
            st = c.get("/api/status").json()["transcodes"]
            assert st == {"limit": 1, "active": ["LG TV: Another film"]}
            procs.slots.release("someone")
            assert c.get(f"/api/items/{film['id']}/hls.m3u8?c=me").status_code == 200
    finally:
        c.app.state.hls.shutdown()
        svc.close()


def test_streaming_settings_saved_and_applied(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        with TestClient(create_app(svc), **LOCAL) as c:
            assert c.get("/api/settings/streaming").json() == {
                "tv_max_bitrate_mbps": 0, "max_transcodes": 2}  # fmt: skip
            r = c.put(
                "/api/settings/streaming", json={"tv_max_bitrate_mbps": 12, "max_transcodes": 3}
            )
            assert r.json() == {"tv_max_bitrate_mbps": 12, "max_transcodes": 3}
            assert procs.slots.limit == 3
            assert svc.settings.dlna.max_bitrate_mbps == 12
            assert "max_bitrate_mbps = 12" in svc.config_file.read_text(encoding="utf-8")
            assert c.put("/api/settings/streaming", json={"max_transcodes": 99}).status_code == 422
    finally:
        svc.close()
