from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

import fixture_library
from picturehouse.paths import ENV_CONFIG, ENV_DATA_DIR

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """No test may touch the real data dir or inherit a developer's config."""
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "data"))
    # A fresh transcode limiter per test (unlimited unless a test or app sets one).
    from picturehouse.streaming import procs

    monkeypatch.setattr(procs, "slots", procs.TranscodeSlots(0))
    yield
    # setup_logging installs handlers on the root logger; close them so the
    # tmp dir can be removed on Windows (open files can't be deleted there).
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_picturehouse_handler", False):
            root.removeHandler(h)
            h.close()


@pytest.fixture
def no_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """FFmpeg nowhere: not on PATH, nor where Windows package managers put it.

    Returns the fake LOCALAPPDATA, for tests that then "install" it there.
    """
    gone = tmp_path / "no-ffmpeg"
    monkeypatch.setenv("PATH", "")
    for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramData"):
        monkeypatch.setenv(env, str(gone / env))
    monkeypatch.setattr(Path, "home", lambda: gone / "home")
    return gone / "LOCALAPPDATA"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture(scope="session")
def ffprobe() -> Path:
    found = shutil.which("ffprobe")
    if not found:
        pytest.skip("ffprobe not on PATH")
    return Path(found)


@pytest.fixture(scope="session")
def media_library() -> Path:
    """The generated fixture library (built once, reused while current)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    root = Path(os.environ.get("PICTUREHOUSE_FIXTURE_DIR") or REPO / "tests" / ".fixture-media")
    fixture_library.build(root)
    return root
