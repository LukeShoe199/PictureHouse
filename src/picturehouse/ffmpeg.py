"""Finding and identifying the FFmpeg tools.

FFmpeg is found, not assumed: a configured path wins, otherwise the system
PATH, then (on Windows) where winget, Chocolatey or Scoop install it. This
module only locates the binaries and reads their versions.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from picturehouse.errors import FFmpegNotFound

_VERSION_RE = re.compile(r"^\S+ version (\S+)")


def no_window_flags() -> int:
    """creationflags that stop a console window flashing up on Windows.

    Matters once the server runs as a tray app or service.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass(frozen=True)
class ToolInfo:
    name: str
    path: Path
    version: str


def _exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _windows_install_dirs() -> list[Path]:
    """Where Windows package managers put FFmpeg, newest first.

    A just-installed FFmpeg (e.g. ticked in the PictureHouse installer, which
    runs ``winget install Gyan.FFmpeg``) is on PATH only for processes started
    after the next sign-in; these let the server find it straight away.
    """
    if sys.platform != "win32":
        return []
    dirs: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        dirs.append(winget / "Links")
        dirs += sorted(winget.glob("Packages/Gyan.FFmpeg*/*/bin"), reverse=True)
    for env in ("ProgramFiles", "ProgramData"):
        root = os.environ.get(env)
        if root:
            dirs.append(
                Path(root) / ("WinGet/Links" if env == "ProgramFiles" else "chocolatey/bin")
            )
    dirs.append(Path.home() / "scoop" / "shims")
    return dirs


def locate_tool(name: str, configured: Path | None = None) -> Path:
    """Return the path of ``name`` (``"ffmpeg"`` or ``"ffprobe"``).

    ``configured`` may be the executable itself or the folder containing it.
    A configured path that does not exist is an error — we never silently fall
    back to PATH, because that would hide a broken config.
    """
    if configured is not None:
        candidate = configured / _exe_name(name) if configured.is_dir() else configured
        if candidate.is_file():
            return candidate
        raise FFmpegNotFound(
            f"{name} not found at configured path {configured}",
            hint=f'fix [ffmpeg].{name}_path in the config, or set it to "" to use PATH',
        )
    found = shutil.which(name)
    if found:
        return Path(found)
    for folder in _windows_install_dirs():
        candidate = folder / _exe_name(name)
        if candidate.is_file():
            return candidate
    raise FFmpegNotFound(
        f"{name} not found on PATH",
        hint=f"install FFmpeg, or set [ffmpeg].{name}_path in the config",
    )


def tool_version(path: Path, *, timeout: float = 15.0) -> str:
    """Run ``<tool> -version`` and return the version token from line one."""
    try:
        proc = subprocess.run(
            [str(path), "-hide_banner", "-version"],
            capture_output=True,
            timeout=timeout,
            creationflags=no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FFmpegNotFound(f"{path} could not be run: {e}") from e
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegNotFound(f"{path} -version exited with code {proc.returncode}")
    first = out.splitlines()[0] if out else ""
    m = _VERSION_RE.match(first)
    if not m:
        raise FFmpegNotFound(f"{path} does not look like an FFmpeg tool: {first!r}")
    return m.group(1)


def identify(name: str, configured: Path | None = None) -> ToolInfo:
    path = locate_tool(name, configured)
    return ToolInfo(name=name, path=path, version=tool_version(path))
