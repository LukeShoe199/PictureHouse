"""The theme engine, its safety rules, the HTTP routes and the CLI (docs/themes.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest_services import LAN, LOCAL, make_services
from picturehouse.cli import app as cli_app
from picturehouse.themes import (
    TOKENS,
    ThemeError,
    ThemeStore,
    install_theme,
    load_theme,
    render_css,
)
from picturehouse.themes.engine import BUILTIN_DIR
from picturehouse.web.app import create_app

REPO = Path(__file__).resolve().parent.parent


def make_theme(root: Path, tid: str = "teal", css: str | None = None, **fields) -> Path:
    folder = root / tid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"schema": 1, "id": tid, "name": tid.title(),
            "tokens": {"color.accent": "#2dd4bf", "radius.card": "12px"}}  # fmt: skip
    data.update(fields)
    if css is not None:
        (folder / "theme.css").write_text(css, encoding="utf-8")
        data["stylesheet"] = "theme.css"
    (folder / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


# ---------------------------------------------------------------- built-ins


def test_builtin_themes_are_valid_and_picturehouse_changes_nothing():
    store = ThemeStore(None)
    assert set(store.themes()) == {"picturehouse", "istream"} and store.problems == []
    ph = store.get("picturehouse")
    assert ph.tokens == {} and ph.layout == {"nav": "rail"} and not ph.stylesheet
    css = render_css(ph)
    assert "--" not in css.split("color-scheme")[1]  # no token overrides at all


def test_istream_is_a_full_theme():
    ist = ThemeStore(None).get("istream")
    assert ist.layout == {"nav": "top"} and ist.value("color.accent") == "#f5f5f7"
    css = render_css(ist)
    assert "--accent: #f5f5f7;" in css and "backdrop-filter" in css
    assert 'Layout option "nav": "top"' in css  # the engine's top-bar layout is included
    tv = render_css(ist, "tv")
    assert "--accent: #f5f5f7;" in tv and "--rail" not in tv and "backdrop" not in tv


def test_token_defaults_match_app_css():
    """A token's default is the built-in look: app.css must say the same."""
    css = (REPO / "src/picturehouse/web/static/app.css").read_text(encoding="utf-8")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    for name, t in TOKENS.items():
        assert f"{t.var}: {t.default};" in root, (
            f"{name}: app.css :root should set {t.var}: {t.default}"
        )


def test_docs_list_every_token():
    docs = (REPO / "docs/themes.md").read_text(encoding="utf-8")
    missing = [n for n in TOKENS if f"| `{n}` |" not in docs]
    assert not missing, f"docs/themes.md doesn't document {missing}"


# ---------------------------------------------------------------- the language


def test_a_minimal_theme(tmp_path):
    t = load_theme(make_theme(tmp_path))
    assert t.tokens == {"color.accent": "#2dd4bf", "radius.card": "12px"}
    css = render_css(t)
    assert "--accent: #2dd4bf;" in css and "--r-card: 12px;" in css


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"schema": 2}, "schema"),
        ({"id": "Other"}, "id"),
        ({"name": ""}, "name"),
        ({"scheme": "blue"}, "scheme"),
        ({"layout": {"nav": "left"}}, "layout.nav"),
        ({"layout": {"sidebar": "x"}}, "layout option"),
        ({"colour": "x"}, "unknown field"),
        ({"tokens": {"color.accnt": "#fff"}}, "unknown token"),
        ({"tokens": {"accent": "#fff"}}, "did you mean color.accent"),
        ({"tokens": {"color.accent": "#fff; background: red"}}, "isn't a valid color"),
        ({"tokens": {"color.accent": "url(https://evil.example/x.png)"}}, "isn't a valid color"),
        ({"tokens": {"font.body": "Arial} body { display: none"}}, "isn't a valid font"),
        ({"tokens": {"radius.card": "big"}}, "isn't a valid length"),
        ({"tv": {"tokens": {"radius.card": "4px"}}}, "TV interface doesn't use"),
        ({"stylesheet": "other.css"}, "stylesheet"),
    ],
)
def test_bad_themes_are_refused_with_a_reason(tmp_path, fields, message):
    with pytest.raises(ThemeError, match=message):
        load_theme(make_theme(tmp_path, **fields))


def test_folder_must_match_id(tmp_path):
    folder = make_theme(tmp_path, "teal")
    folder.rename(tmp_path / "other")
    with pytest.raises(ThemeError, match="folder name"):
        load_theme(tmp_path / "other")


# ---------------------------------------------------------------- stylesheet safety


@pytest.mark.parametrize(
    ("css", "message"),
    [
        ('@import "https://evil.example/x.css";', "@import"),
        ("body { background: url(https://evil.example/track.png) }", "://"),
        ("body { background: url(//evil.example/track.png) }", "//"),
        ('body { background: image-set("https://evil.example/a.png" 1x) }', "://"),
        ("body { background: url(/static/app.js) }", "outside the theme"),
        ("body { background: url(../../secret.png) }", "no such image"),
        ("body { background: u" + "\\" + "72l(x.png) }", "can't use"),  # CSS escape
        ("body { background: url(assets/logo.svg) }", "no such image"),  # SVG isn't allowed
        ("body { x: expression(alert(1)) }", "expression"),
    ],
)
def test_stylesheets_cant_reach_outside_the_theme(tmp_path, css, message):
    folder = make_theme(tmp_path, css=css)
    (folder / "assets").mkdir()
    (folder / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    with pytest.raises(ThemeError, match=message):
        load_theme(folder)


def test_own_assets_are_served_from_the_theme(tmp_path):
    folder = make_theme(tmp_path, css="body { background: url('assets/bg.jpg') }")
    (folder / "assets").mkdir()
    (folder / "assets" / "bg.jpg").write_bytes(b"\xff\xd8jpeg")
    t = load_theme(folder)
    assert 'url("/themes/teal/assets/bg.jpg")' in render_css(t)
    assert t.assets == {"assets/bg.jpg"}


# ---------------------------------------------------------------- store and install


def test_store_rereads_changed_themes_and_reports_broken_ones(tmp_path):
    user = tmp_path / "themes"
    make_theme(user, "teal")
    bad = make_theme(user, "broken")
    (bad / "theme.json").write_text("{not json", encoding="utf-8")
    store = ThemeStore(user)
    assert "teal" in store.themes() and store.problems[0]["folder"] == "broken"
    assert store.get("nope").id == "picturehouse"  # unknown: the default look
    make_theme(user, "teal", tokens={"color.accent": "#ff0000"})
    assert store.get("teal").value("color.accent") == "#ff0000"


def test_install_checks_first_and_wont_shadow_builtins(tmp_path):
    user = tmp_path / "data" / "themes"
    t = install_theme(make_theme(tmp_path / "src"), user)
    assert (user / "teal" / "theme.json").is_file() and t.id == "teal"
    with pytest.raises(ThemeError, match="already installed"):
        install_theme(make_theme(tmp_path / "src"), user)
    install_theme(make_theme(tmp_path / "src"), user, replace=True)
    with pytest.raises(ThemeError, match="built-in"):
        install_theme(make_theme(tmp_path / "src2", "istream"), user)
    with pytest.raises(ThemeError):
        install_theme(make_theme(tmp_path / "src3", "bad", tokens={"x": "y"}), user)
    assert not (user / "bad").exists()  # refused themes aren't copied
    assert (BUILTIN_DIR / "istream").is_dir()


# ---------------------------------------------------------------- HTTP


def test_http_routes(media_library, tmp_path):
    svc = make_services(media_library, tmp_path / "data")
    try:
        folder = make_theme(svc.layout.themes_dir, css="body { background: url(assets/bg.png) }")
        (folder / "assets").mkdir()
        (folder / "assets" / "bg.png").write_bytes(b"\x89PNG")
        with TestClient(create_app(svc), **LAN) as lan:
            d = lan.get("/api/themes").json()  # anyone may see the list
            assert d["default"] == "picturehouse"
            assert {t["id"] for t in d["themes"]} == {"picturehouse", "istream", "teal"}
            r = lan.get("/theme.css", params={"id": "istream"})
            assert (
                r.headers["content-type"].startswith("text/css") and "--accent: #f5f5f7" in r.text
            )
            assert "--accent" not in lan.get("/theme.css").text  # default: the built-in look
            assert lan.get("/theme.css", params={"id": "nope"}).status_code == 200  # falls back
            assert lan.get("/themes/teal/assets/bg.png").content == b"\x89PNG"
            assert lan.get("/themes/teal/theme.json").status_code == 404  # only images/fonts
            assert lan.get("/themes/teal/../../config.toml").status_code == 404
            assert lan.put("/api/settings/theme", json={"default": "istream"}).status_code == 403
        with TestClient(create_app(svc), **LOCAL) as pc:
            assert pc.put("/api/settings/theme", json={"default": "nope"}).status_code == 404
            assert (
                pc.put("/api/settings/theme", json={"default": "istream"}).json()["default"]
                == "istream"
            )
            assert "--accent: #f5f5f7" in pc.get("/theme.css").text  # now everyone's default
            assert "--accent: #f5f5f7" in pc.get("/theme.css", params={"target": "tv"}).text
        assert 'theme = "istream"' in svc.config_file.read_text(encoding="utf-8")
    finally:
        svc.close()


# ---------------------------------------------------------------- CLI


def test_cli(tmp_path):
    data, run = tmp_path / "d", CliRunner()

    def cli(*args):
        return run.invoke(cli_app, ["--data-dir", str(data), *args])

    src = make_theme(tmp_path / "src")
    assert "ok: Teal" in cli("theme", "check", str(src)).output
    assert cli("theme", "install", str(src)).exit_code == 0
    assert "teal" in cli("theme", "list").output
    assert cli("theme", "default", "teal").exit_code == 0
    assert cli("theme", "default").output.strip() == "teal"
    r = cli("theme", "remove", "teal")
    assert r.exit_code == 0 and "the default is now picturehouse" in r.output
    assert cli("theme", "remove", "istream").exit_code != 0  # built-ins stay
    bad = make_theme(tmp_path / "src2", "bad", tokens={"color.accent": "#fff;x"})
    r = cli("theme", "check", str(bad))
    assert r.exit_code != 0 and "isn't a valid color" in r.output
