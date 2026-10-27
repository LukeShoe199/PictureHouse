"""Themes: how the web console (and the TV interface) look.

A theme is a folder: ``theme.json`` (the theme language: name, design
tokens, layout options), an optional ``theme.css`` that restyles anything
else, and optional ``assets/`` (images, fonts) that the CSS refers to.
Built-in themes live in ``themes/builtin/``; installed ones in the data
directory (``DataLayout.themes_dir``). See docs/themes.md.
"""

from picturehouse.themes.engine import (
    DEFAULT_THEME,
    TOKENS,
    Theme,
    ThemeError,
    ThemeStore,
    install_theme,
    load_theme,
    render_css,
)

__all__ = [
    "DEFAULT_THEME",
    "TOKENS",
    "Theme",
    "ThemeError",
    "ThemeStore",
    "install_theme",
    "load_theme",
    "render_css",
]
