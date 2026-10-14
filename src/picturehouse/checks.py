"""Startup checks: FFmpeg present, data directory writable, folders readable, port free.

Each check returns a result rather than raising, so ``config check`` can show
everything wrong at once. A missing library folder is a warning, not a
failure: per the robustness rules its items are marked unavailable and come
back when the folder does.
"""

from __future__ import annotations

import os
import socket
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum

from picturehouse import ffmpeg
from picturehouse.config import LibraryRoot, Settings
from picturehouse.errors import FFmpegNotFound
from picturehouse.paths import DataLayout


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


def check_tool(name: str, settings: Settings) -> CheckResult:
    configured = getattr(settings.ffmpeg, f"{name}_path")
    try:
        info = ffmpeg.identify(name, configured)
    except FFmpegNotFound as e:
        return CheckResult(name, Status.FAIL, str(e))
    return CheckResult(name, Status.OK, f"{info.version} ({info.path})")


def check_data_dir(layout: DataLayout) -> CheckResult:
    try:
        layout.ensure()
        probe = layout.root / f".write-test-{uuid.uuid4().hex}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        return CheckResult("data dir", Status.FAIL, f"{layout.root} is not writable: {e}")
    return CheckResult("data dir", Status.OK, str(layout.root))


def check_library(root: LibraryRoot) -> CheckResult:
    name = f"library '{root.name}'"
    path = root.path
    try:
        if not path.exists():
            return CheckResult(
                name, Status.WARN, f"{path} not found — items will show as unavailable"
            )
        if not path.is_dir():
            return CheckResult(name, Status.FAIL, f"{path} is not a folder")
        with os.scandir(path) as it:
            next(it, None)
    except OSError as e:
        return CheckResult(name, Status.WARN, f"{path} not readable: {e}")
    return CheckResult(name, Status.OK, f"{path} ({root.kind})")


def check_port(host: str, port: int) -> CheckResult:
    name = f"port {port}"
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if sys.platform == "win32":
            # Without this Windows lets a second socket share a port that a
            # SO_REUSEADDR listener holds, so the check would pass wrongly.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # As uvicorn does: connections from a server that just stopped
            # linger in TIME_WAIT for a minute and would fail a plain bind,
            # though the port is free to listen on (a restart after a crash).
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            return CheckResult(name, Status.FAIL, f"cannot bind {host}:{port}: {e.strerror or e}")
    return CheckResult(name, Status.OK, f"{host}:{port} is free")


def run_startup_checks(
    settings: Settings, layout: DataLayout, *, include_port: bool = True
) -> list[CheckResult]:
    results = [
        check_tool("ffmpeg", settings),
        check_tool("ffprobe", settings),
        check_data_dir(layout),
    ]
    if settings.libraries:
        results.extend(check_library(r) for r in settings.libraries)
    else:
        results.append(CheckResult("libraries", Status.WARN, "no library folders configured yet"))
    if include_port:
        results.append(check_port(settings.server.host, settings.server.http_port))
    return results


def worst(results: list[CheckResult]) -> Status:
    statuses = {r.status for r in results}
    for s in (Status.FAIL, Status.WARN):
        if s in statuses:
            return s
    return Status.OK
