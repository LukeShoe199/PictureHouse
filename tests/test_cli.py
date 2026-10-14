from __future__ import annotations

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from picturehouse import __version__
from picturehouse.cli import app
from picturehouse.errors import ExitCode
from picturehouse.log import LOG_FILE_NAME

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_version():
    r = invoke("--version")
    assert r.exit_code == 0
    assert r.output.strip() == f"picturehouse {__version__}"


def test_version_via_module():
    """`python -m picturehouse --version` works as a real process, not just in-process."""
    out = subprocess.run(
        [sys.executable, "-m", "picturehouse", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == f"picturehouse {__version__}"


def test_config_path_uses_env_data_dir(data_dir):
    r = invoke("config", "path")
    assert r.exit_code == 0
    assert str(data_dir) in r.output
    assert "not created yet" in r.output


def test_data_dir_option_beats_env(tmp_path):
    r = invoke("--data-dir", str(tmp_path / "other"), "config", "path")
    assert str(tmp_path / "other") in r.output


def test_init_then_show(data_dir):
    assert invoke("config", "init").exit_code == 0
    assert (data_dir / "config.toml").is_file()
    again = invoke("config", "init")
    assert again.exit_code == 0 and "already exists" in again.output
    shown = json.loads(invoke("config", "show").output)
    assert shown["server"]["http_port"] == 9595


def test_explicit_config_file(tmp_path):
    cfg = tmp_path / "custom.toml"
    cfg.write_text("[server]\nhttp_port = 9123\n", encoding="utf-8")
    shown = json.loads(invoke("--config", str(cfg), "config", "show").output)
    assert shown["server"]["http_port"] == 9123


def test_invalid_config_exit_code(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text("[server]\nhttp_port = 'abc'\n", encoding="utf-8")
    r = runner.invoke(app, ["--config", str(cfg), "config", "check"])
    assert r.exit_code == ExitCode.CONFIG
    assert "server.http_port" in r.output


@pytest.mark.usefixtures("ffprobe")
def test_check_passes_and_logs(tmp_path, data_dir):
    (tmp_path / "Films").mkdir()
    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text(
        f"[[libraries]]\nname='Films'\npath='{tmp_path / 'Films'}'\n", encoding="utf-8"
    )
    r = invoke("config", "check", "--no-port")
    assert r.exit_code == 0, r.output
    assert "all checks passed" in r.output
    entries = [
        json.loads(line)
        for line in (data_dir / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()
    ]
    job_ids = {e.get("job_id") for e in entries}
    assert len(job_ids) == 1 and next(iter(job_ids)).startswith("check-")
    assert any(e.get("check") == "library 'Films'" for e in entries)


@pytest.mark.usefixtures("ffprobe")
def test_warnings_pass_unless_strict(tmp_path):
    # No libraries configured -> a warning.
    assert invoke("config", "check", "--no-port").exit_code == 0
    r = runner.invoke(app, ["config", "check", "--no-port", "--strict"])
    assert r.exit_code == ExitCode.STARTUP_CHECK


def test_missing_ffmpeg_fails_check(no_ffmpeg):
    r = runner.invoke(app, ["config", "check", "--no-port"])
    assert r.exit_code == ExitCode.STARTUP_CHECK
    assert "not found on PATH" in r.output
