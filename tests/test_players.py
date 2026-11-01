"""Player details that are easy to lose: next-episode in the console (phones,
browsers, the Android app) and the TV's video controls."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src/picturehouse/web/static"


def test_console_plays_the_next_episode():
    """Bug: the console just stopped at the end of an episode (the TV app
    already counted down to the next one)."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    ended = next(line for line in js.splitlines() if line.startswith("V.addEventListener('ended'"))
    assert "upNext(P)" in ended
    assert (
        "async function upNext(p)" in js
        and "/api/shows/" in js.split("async function upNext")[1][:600]
    )
    cast_end = js.split("function castEnded()", 1)[1].split("\n}\n", 1)[0]
    assert "upNext(p)" in cast_end  # a Chromecast finishing an episode too


def test_tv_video_controls_are_centred_and_drawn():
    """Bug: the row sat to the left, and Next showed an emoji: webOS draws
    symbols like ⏩ (and sometimes ◀ ▶) as colour emoji."""
    page = (STATIC / "tv/index.html").read_text(encoding="utf-8")
    controls = page.split('id="vctl"', 1)[1].split("</div>", 1)[0]
    assert not re.search("[←-⇿⌀-⏿■-◿☀-➿\U0001f300-\U0001faff]", controls)
    for path in re.findall(r'<path d="([^"]+)"', controls):  # every icon has filled area
        assert "z" in path.lower(), path
    css = (STATIC / "tv/tv.css").read_text(encoding="utf-8")
    vctl = re.search(r"\.vctl \{([^}]*)\}", css)[1]
    assert "flex-start" not in vctl
    assert "justify-content: center" in re.search(r"\.ctl \{([^}]*)\}", css)[1]
