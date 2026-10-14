from __future__ import annotations

from pathlib import Path

import pytest

from picturehouse.config import Settings, load_config, parse_config, template_text
from picturehouse.errors import ConfigError, ExitCode

# An absolute path on whichever OS runs the tests ('/x' is not absolute on Windows).
ABS = str(Path(Path.cwd().anchor) / "x")


def test_missing_file_gives_defaults(tmp_path):
    s = load_config(tmp_path / "nope.toml")
    assert s == Settings()
    assert s.server.http_port == 9595
    assert s.libraries == []


def test_template_is_valid_and_matches_defaults():
    assert parse_config(template_text()) == Settings()


def test_windows_literal_paths_and_libraries(tmp_path):
    films = tmp_path / "Films"
    text = f"""
[server]
http_port = 9000

[ffmpeg]
ffmpeg_path = ''

[[libraries]]
name = "Films"
path = '{films}'
kind = "films"

[[libraries]]
name = "Música"
path = '{tmp_path / "Música"}'
kind = "music"
"""
    s = parse_config(text)
    assert s.server.http_port == 9000
    assert s.ffmpeg.ffmpeg_path is None  # "" means unset
    assert s.library("films").path == films  # lookup is case-insensitive
    assert s.library("Música").kind == "music"
    assert s.library("missing") is None


def test_bom_is_tolerated(tmp_path):
    p = tmp_path / "c.toml"
    p.write_bytes("\ufeff[server]\nhttp_port = 9100\n".encode())
    assert load_config(p).server.http_port == 9100


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("[server]\nhttp_port = 70000\n", "server.http_port"),
        ("[server]\nprot = 1\n", "server.prot"),  # typo -> unknown key rejected
        ("[logging]\nlevel = 'LOUD'\n", "logging.level"),
        ("[[libraries]]\nname='A'\npath='relative/dir'\n", "must be absolute"),
        (f"[[libraries]]\nname='A'\npath='{ABS}'\nkind='podcasts'\n", "libraries.0.kind"),
        (
            f"[[libraries]]\nname='A'\npath='{ABS}'\n[[libraries]]\nname='a'\npath='{ABS}y'\n",
            "duplicate library name",
        ),
        (f"[[libraries]]\nname='../x'\npath='{ABS}'\n", "libraries.0.name"),
    ],
)
def test_invalid_values_are_reported(text, fragment):
    with pytest.raises(ConfigError) as ei:
        parse_config(text)
    assert fragment in str(ei.value)
    assert ei.value.exit_code is ExitCode.CONFIG


def test_bad_toml_gives_hint():
    # The classic mistake: double quotes around a Windows path.
    with pytest.raises(ConfigError) as ei:
        parse_config('[[libraries]]\nname="A"\npath="D:\\Media\\Films"\n')
    assert "single quotes" in str(ei.value)


def test_logging_levels_case_insensitive():
    assert parse_config("[logging]\nlevel='debug'\n").logging.level == "DEBUG"


def test_unreadable_config_is_config_error(tmp_path):
    d = tmp_path / "dir.toml"
    d.mkdir()  # a directory where the file should be
    with pytest.raises(ConfigError):
        load_config(Path(d))
