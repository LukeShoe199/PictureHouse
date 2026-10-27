"""The theme engine: reading, checking and rendering themes.

Safety rules (a theme is installed by the admin, but may come from anyone):
token values are checked against their type and can't break out of their
CSS declaration; a theme's stylesheet can't import other stylesheets, use
scripts or expressions, or load anything from another site: its url()s must
point at files inside the theme's own folder, of image/font types (no SVG,
which can carry scripts). So a theme can change how things look, nothing more.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path, PurePosixPath

from picturehouse.errors import PictureHouseError

log = logging.getLogger(__name__)

SCHEMA = 1
DEFAULT_THEME = "picturehouse"
BUILTIN_DIR = Path(str(files("picturehouse.themes").joinpath("builtin")))
LAYOUT_DIR = Path(str(files("picturehouse.themes").joinpath("layouts")))
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
MAX_CSS = 256 * 1024
ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
}  # fmt: skip


@dataclass(frozen=True)
class Token:
    var: str  # the CSS custom property
    kind: str  # color | font | length
    default: str  # the PictureHouse look
    about: str
    tv: bool = False  # also drives the TV interface (static/tv/)


# The theme language's vocabulary. Defaults are the built-in look, so the
# "picturehouse" theme sets nothing. docs/themes.md lists these (tested).
TOKENS: dict[str, Token] = {
    "color.background": Token("--bg", "color", "#0a0d11", "Page background", tv=True),
    "color.navigation": Token("--bg-rail", "color", "#080a0d", "Navigation rail / top bar"),
    "color.surface": Token("--surface", "color", "#121821", "Cards, panels, inputs", tv=True),
    "color.surface-raised": Token("--surface-2", "color", "#19212c", "Menus, dialogs, raised panels", tv=True),
    "color.line": Token("--line", "color", "#243041", "Borders and dividers", tv=True),
    "color.text": Token("--fg", "color", "#eef1f4", "Main text", tv=True),
    "color.text-muted": Token("--muted", "color", "#93a0b0", "Secondary text", tv=True),
    "color.text-faint": Token("--faint", "color", "#5f6b7a", "Hints, table headings"),
    "color.accent": Token("--accent", "color", "#f2b33d",
                          "Highlights: focus, progress, primary buttons, active tab", tv=True),
    "color.on-accent": Token("--accent-ink", "color", "#1b1305", "Text on the accent colour", tv=True),
    "color.good": Token("--good", "color", "#5fc48b", "OK / online / done"),
    "color.info": Token("--mid", "color", "#7fb2e5", "Informational (e.g. converting)"),
    "color.warning": Token("--warn", "color", "#ec8a4c", "Warnings, offline"),
    "color.error": Token("--bad", "color", "#e5605c", "Errors", tv=True),
    "font.display": Token("--display", "font", '"Archivo", "Arial Narrow", "Helvetica Neue", Arial, sans-serif',
                          "Headings and titles", tv=True),
    "font.body": Token("--body", "font", '"Figtree", "Segoe UI", system-ui, -apple-system, sans-serif',
                       "Everything else", tv=True),
    "font.mono": Token("--mono", "font", '"JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace',
                       "Paths, codes, technical details"),
    "size.gutter": Token("--gut", "length", "clamp(16px, 3vw, 40px)", "Page side margins"),
    "size.navigation": Token("--rail", "length", "88px",
                             "Width of the side rail (or height of the top bar)"),
    "radius.card": Token("--r-card", "length", "7px", "Artwork corners"),
    "radius.button": Token("--r-button", "length", "8px", "Button corners"),
    "radius.panel": Token("--r-panel", "length", "12px", "Panel, menu and dialog corners"),
    "radius.hero": Token("--r-hero", "length", "14px", "Big banner (hero) corners"),
}  # fmt: skip
# Layout options: each non-default value adds layouts/<option>-<value>.css.
LAYOUT = {
    "nav": ("rail", "top", "floating"),
    "hero": ("card", "full-bleed"),
}  # first value is the default

_SAFE = {
    "color": re.compile(r"^[#a-zA-Z0-9(),.%\s/+-]{1,120}$"),
    "font": re.compile(r"^[A-Za-z0-9 ,\"'._-]{1,240}$"),
    "length": re.compile(r"^(?=.*\d)[a-z0-9().,%\s*/+-]{1,120}$"),
}
_BANNED = re.compile(r"url\s*\(|expression|javascript:|@import|behavior|binding|\\|[;{}<>]", re.I)
# Backslashes are banned outright: CSS escapes could spell "url(" or "@import"
# in a way a text check can't see. "://" and quoted "//" catch web addresses
# given without url() (e.g. image-set("https://...")).
_CSS_BANNED = re.compile(
    r"@import|javascript:|expression\s*\(|behavior\s*:|-moz-binding|</\s*style|\\|://|[\"']\s*//",
    re.I,
)
_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]*)\1\s*\)", re.I)


class ThemeError(PictureHouseError):
    """A theme that can't be used, with what's wrong in plain words."""


@dataclass
class Theme:
    id: str
    name: str
    version: str
    author: str
    description: str
    scheme: str
    layout: dict[str, str]
    tokens: dict[str, str]  # token name -> value (only what the theme sets)
    tv_tokens: dict[str, str]
    folder: Path
    builtin: bool
    stylesheet: str = ""  # checked, url()s rewritten to /themes/<id>/...
    assets: set[str] = field(default_factory=set)

    def value(self, token: str) -> str:
        return self.tokens.get(token, TOKENS[token].default)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version, "author": self.author,
            "description": self.description, "scheme": self.scheme, "layout": self.layout,
            "builtin": self.builtin,
            "swatches": {k: self.value(f"color.{k}") for k in
                         ("background", "surface", "text", "accent", "on-accent")},
        }  # fmt: skip


def _check_value(name: str, value: object, where: str) -> str:
    if name not in TOKENS:
        close = [t for t in TOKENS if t.split(".")[-1] == name.split(".")[-1]]
        hint = f" (did you mean {close[0]}?)" if close else ""
        raise ThemeError(f"{where}: unknown token {name!r}{hint}", hint="see docs/themes.md")
    if not isinstance(value, str) or not value.strip():
        raise ThemeError(f"{where}: {name} must be a non-empty string")
    kind = TOKENS[name].kind
    value = value.strip()
    if _BANNED.search(value) or not _SAFE[kind].match(value):
        raise ThemeError(f"{where}: {name} = {value!r} isn't a valid {kind}")
    return value


def _check_stylesheet(text: str, folder: Path, theme_id: str, assets: set[str]) -> str:
    if len(text.encode("utf-8")) > MAX_CSS:
        raise ThemeError(f"theme.css is larger than {MAX_CSS // 1024} KB")
    m = _CSS_BANNED.search(text)
    if m:
        raise ThemeError(f"theme.css uses {m.group(0)!r}, which themes can't use")

    def rewrite(m: re.Match) -> str:
        ref = m.group(2).strip()
        if ":" in ref or ref.startswith(("/", "\\")):
            raise ThemeError(
                f"theme.css: url({ref}) points outside the theme; themes can only use "
                "their own files (relative paths like assets/bg.jpg)"
            )
        rel = PurePosixPath(ref.split("?", 1)[0].split("#", 1)[0])
        if ".." in rel.parts or str(rel) not in assets:
            raise ThemeError(f"theme.css: url({ref}): no such image/font in the theme's folder")
        return f'url("/themes/{theme_id}/{rel}")'

    return _URL.sub(rewrite, text)


def _theme_assets(folder: Path) -> set[str]:
    out = set()
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in ASSET_TYPES:
            out.add(f.relative_to(folder).as_posix())
    return out


def load_theme(folder: Path, *, builtin: bool = False) -> Theme:
    """Read and check one theme folder; ThemeError says what's wrong."""
    manifest = folder / "theme.json"
    if not manifest.is_file():
        raise ThemeError(f"{folder} has no theme.json")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ThemeError(f"{manifest}: not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise ThemeError(f"{manifest}: must be a JSON object")
    known = {"schema", "id", "name", "version", "author", "description", "scheme", "layout",
             "tokens", "tv", "stylesheet"}  # fmt: skip
    extra = sorted(set(data) - known)
    if extra:
        raise ThemeError(f"{manifest}: unknown field(s) {', '.join(extra)}")
    if data.get("schema") != SCHEMA:
        raise ThemeError(f'{manifest}: "schema" must be {SCHEMA}')
    tid = data.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise ThemeError(f'{manifest}: "id" must be 2-40 lowercase letters, digits or -')
    if folder.name != tid:
        raise ThemeError(f"{manifest}: id {tid!r} must match the folder name {folder.name!r}")
    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ThemeError(f'{manifest}: "name" must be 1-40 characters')
    texts = {}
    for key, limit in (("version", 20), ("author", 60), ("description", 300)):
        v = data.get(key, "")
        if not isinstance(v, str) or len(v) > limit:
            raise ThemeError(f'{manifest}: "{key}" must be text of at most {limit} characters')
        texts[key] = v.strip()
    scheme = data.get("scheme", "dark")
    if scheme not in ("dark", "light"):
        raise ThemeError(f'{manifest}: "scheme" must be "dark" or "light"')
    layout_in = data.get("layout", {})
    if not isinstance(layout_in, dict):
        raise ThemeError(f'{manifest}: "layout" must be an object')
    layout = {}
    for k, options in LAYOUT.items():
        v = layout_in.get(k, options[0])
        if v not in options:
            raise ThemeError(f"{manifest}: layout.{k} must be one of {', '.join(options)}")
        layout[k] = v
    if set(layout_in) - set(LAYOUT):
        raise ThemeError(
            f"{manifest}: unknown layout option(s) {sorted(set(layout_in) - set(LAYOUT))}"
        )

    def tokens_of(obj: object, where: str) -> dict[str, str]:
        if obj is None:
            return {}
        if not isinstance(obj, dict):
            raise ThemeError(f'{where}: "tokens" must be an object')
        return {k: _check_value(k, v, where) for k, v in obj.items()}

    tokens = tokens_of(data.get("tokens"), str(manifest))
    tv = data.get("tv", {})
    if not isinstance(tv, dict) or set(tv) - {"tokens"}:
        raise ThemeError(f'{manifest}: "tv" may only contain "tokens"')
    tv_tokens = tokens_of(tv.get("tokens"), f"{manifest} (tv)")
    not_tv = [k for k in tv_tokens if not TOKENS[k].tv]
    if not_tv:
        raise ThemeError(f"{manifest}: the TV interface doesn't use {', '.join(not_tv)}")

    assets = _theme_assets(folder)
    stylesheet = ""
    css_name = data.get("stylesheet")
    if css_name is not None:
        if css_name != "theme.css":
            raise ThemeError(f'{manifest}: "stylesheet" must be "theme.css"')
        css_file = folder / "theme.css"
        if not css_file.is_file():
            raise ThemeError(f"{manifest}: names theme.css, but there's no such file")
        stylesheet = _check_stylesheet(
            css_file.read_text(encoding="utf-8-sig"), folder, tid, assets
        )
    return Theme(
        id=tid, name=name.strip(), scheme=scheme, layout=layout, tokens=tokens,
        tv_tokens=tv_tokens, folder=folder, builtin=builtin, stylesheet=stylesheet,
        assets=assets, **texts,
    )  # fmt: skip


def _declarations(values: dict[str, str]) -> str:
    return "".join(f"  {TOKENS[k].var}: {v};\n" for k, v in values.items())


def render_css(theme: Theme, target: str = "console") -> str:
    """The stylesheet that turns the built-in look into ``theme``."""
    head = f"/* PictureHouse theme: {theme.name} {theme.version}".rstrip() + " */\n"
    if target == "tv":
        tv = {k: v for k, v in theme.tokens.items() if TOKENS[k].tv}
        tv.update(theme.tv_tokens)
        return head + f":root {{\n  color-scheme: {theme.scheme};\n{_declarations(tv)}}}\n"
    css = head + f":root {{\n  color-scheme: {theme.scheme};\n{_declarations(theme.tokens)}}}\n"
    for option, value in theme.layout.items():
        if value != LAYOUT[option][0]:
            css += "\n" + (LAYOUT_DIR / f"{option}-{value}.css").read_text(encoding="utf-8")
    if theme.stylesheet:
        css += f"\n/* ---- {theme.id}/theme.css ---- */\n{theme.stylesheet}"
    return css


class ThemeStore:
    """The built-in themes plus those installed in ``user_dir``.

    Re-read when a theme file changes, so editing a theme shows on reload."""

    def __init__(self, user_dir: Path | None) -> None:
        self.user_dir = user_dir
        self._sig: tuple | None = None
        self._themes: dict[str, Theme] = {}
        self.problems: list[dict[str, str]] = []

    def _folders(self) -> list[tuple[Path, bool]]:
        out = [(d, True) for d in sorted(BUILTIN_DIR.iterdir()) if d.is_dir()]
        if self.user_dir and self.user_dir.is_dir():
            out += [(d, False) for d in sorted(self.user_dir.iterdir()) if d.is_dir()]
        return out

    def _signature(self, folders: list[tuple[Path, bool]]) -> tuple:
        # By content, not modification time: an edit within the same clock
        # tick (or a copy that keeps old times) must still be noticed. Theme
        # files are small (theme.css is capped at 256 KB).
        sig = []
        for d, _ in folders:
            for f in (d / "theme.json", d / "theme.css"):
                try:
                    sig.append((str(f), hashlib.blake2b(f.read_bytes(), digest_size=16).digest()))
                except OSError:
                    sig.append((str(f), None))
        return tuple(sig)

    def themes(self) -> dict[str, Theme]:
        folders = self._folders()
        sig = self._signature(folders)
        if sig != self._sig:
            themes, problems = {}, []
            for d, builtin in folders:
                try:
                    t = load_theme(d, builtin=builtin)
                except ThemeError as e:
                    problems.append({"folder": d.name, "error": e.message})
                    log.warning("theme %s can't be used: %s", d.name, e.message)
                    continue
                if t.id in themes:
                    problems.append(
                        {"folder": d.name, "error": f"a built-in theme is already called {t.id!r}"}
                    )
                    continue
                themes[t.id] = t
            self._themes, self.problems, self._sig = themes, problems, sig
        return self._themes

    def get(self, theme_id: str | None) -> Theme:
        themes = self.themes()
        return themes.get(theme_id or DEFAULT_THEME) or themes[DEFAULT_THEME]


def install_theme(source: Path, user_dir: Path, *, replace: bool = False) -> Theme:
    """Check a theme folder, then copy it into ``user_dir`` (the data dir)."""
    theme = load_theme(source)
    if (BUILTIN_DIR / theme.id).is_dir():
        raise ThemeError(f"{theme.id!r} is a built-in theme's name; give yours another id")
    dest = user_dir / theme.id
    if dest.exists():
        if not replace:
            raise ThemeError(f"a theme called {theme.id!r} is already installed",
                             hint="use --replace to update it")  # fmt: skip
        shutil.rmtree(dest)
    user_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".*", "__pycache__"))
    return load_theme(dest)
