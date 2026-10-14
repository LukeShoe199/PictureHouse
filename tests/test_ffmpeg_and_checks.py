from __future__ import annotations

import socket
import sys

import pytest

from picturehouse import ffmpeg
from picturehouse.checks import (
    Status,
    check_data_dir,
    check_library,
    check_port,
    run_startup_checks,
    worst,
)
from picturehouse.config import LibraryRoot, Settings, parse_config
from picturehouse.errors import ExitCode, FFmpegNotFound
from picturehouse.paths import DataLayout

needs_ffmpeg = pytest.mark.usefixtures("ffprobe")


@needs_ffmpeg
def test_locate_on_path_and_version():
    info = ffmpeg.identify("ffprobe")
    assert info.path.is_file()
    assert info.version


@needs_ffmpeg
def test_configured_folder_or_file_both_work():
    exe = ffmpeg.locate_tool("ffprobe")
    assert ffmpeg.locate_tool("ffprobe", exe) == exe
    assert ffmpeg.locate_tool("ffprobe", exe.parent).parent == exe.parent


def test_configured_path_missing_does_not_fall_back(tmp_path):
    with pytest.raises(FFmpegNotFound) as ei:
        ffmpeg.locate_tool("ffmpeg", tmp_path / "nope" / "ffmpeg.exe")
    assert ei.value.exit_code is ExitCode.FFMPEG


def test_not_on_path(no_ffmpeg):
    with pytest.raises(FFmpegNotFound, match="not found on PATH"):
        ffmpeg.locate_tool("ffmpeg")


@pytest.mark.skipif(sys.platform != "win32", reason="winget is Windows-only")
def test_found_where_winget_installed_it_before_path_updates(no_ffmpeg):
    # Installed a moment ago (e.g. by the PictureHouse installer): not on this
    # process's PATH until the next sign-in, but in winget's package folder.
    pkgs = no_ffmpeg / "Microsoft" / "WinGet" / "Packages"
    for version in ("7.1-full_build", "8.1-full_build"):
        bin_dir = pkgs / "Gyan.FFmpeg_Microsoft.Winget.Source_x" / f"ffmpeg-{version}" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "ffmpeg.exe").write_bytes(b"")
    assert ffmpeg.locate_tool("ffmpeg").parent.parent.name == "ffmpeg-8.1-full_build"


def test_non_ffmpeg_binary_rejected():
    # The Python interpreter runs, but isn't an FFmpeg tool.
    with pytest.raises(FFmpegNotFound):
        ffmpeg.tool_version(__import__("pathlib").Path(sys.executable))


def test_data_dir_check(tmp_path):
    assert check_data_dir(DataLayout(tmp_path / "d")).status is Status.OK


def test_data_dir_unwritable(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where the data dir should be
    assert check_data_dir(DataLayout(blocker / "d")).status is Status.FAIL


def test_library_checks(tmp_path):
    (tmp_path / "Films").mkdir()
    (tmp_path / "file.txt").write_text("x")
    ok = check_library(LibraryRoot(name="F", path=tmp_path / "Films"))
    missing = check_library(LibraryRoot(name="M", path=tmp_path / "Unplugged"))
    notdir = check_library(LibraryRoot(name="T", path=tmp_path / "file.txt"))
    assert ok.status is Status.OK
    assert missing.status is Status.WARN and "unavailable" in missing.detail
    assert notdir.status is Status.FAIL


def test_port_in_use_detected():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert check_port("127.0.0.1", port).status is Status.FAIL
    assert check_port("127.0.0.1", port).status is Status.OK


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/macOS TIME_WAIT behaviour")
def test_port_free_while_old_connections_linger_after_a_crash():
    # A server that stopped (or crashed) right after serving leaves its side
    # of each connection in TIME_WAIT; the port is still free to listen on.
    with socket.socket() as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn's is
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        cli = socket.create_connection(("127.0.0.1", port))
        conn, _ = srv.accept()
        conn.close()  # server closes first: its socket goes to TIME_WAIT
        cli.close()
    assert check_port("127.0.0.1", port).status is Status.OK


@needs_ffmpeg
def test_full_run(tmp_path):
    (tmp_path / "Films").mkdir()
    s = parse_config(f"[[libraries]]\nname='Films'\npath='{tmp_path / 'Films'}'\n")
    results = run_startup_checks(s, DataLayout(tmp_path / "d"), include_port=False)
    assert worst(results) is Status.OK
    assert [r.name for r in results] == ["ffmpeg", "ffprobe", "data dir", "library 'Films'"]


def test_no_libraries_warns(tmp_path, no_ffmpeg):
    results = run_startup_checks(Settings(), DataLayout(tmp_path / "d"), include_port=False)
    assert worst(results) is Status.FAIL  # no ffmpeg
    assert any(r.name == "libraries" and r.status is Status.WARN for r in results)
