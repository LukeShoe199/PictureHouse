"""Basic metadata from paths and embedded tags.

This is the offline baseline: what can be known from the folder layout, the
file name (via guessit) and the file's own tags. Online matching (TMDb,
MusicBrainz) in Phase 2 refines it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from guessit import guessit

from picturehouse.library.probe import ProbeResult

VIDEO_EXTS = {
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".mts", ".mpg",
    ".mpeg", ".webm", ".flv", ".vob", ".ogv", ".3gp", ".divx", ".asf",
}  # fmt: skip
AUDIO_EXTS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff", ".aif",
    ".ape", ".wv", ".alac", ".mka",
}  # fmt: skip
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

IGNORED_DIRS = {
    "sample", "samples", "extrafanart", "extrathumbs", "$recycle.bin",
    "system volume information", "@eadir", ".trickplay", "lost+found",
}  # fmt: skip
IGNORED_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}

_YEAR_SUFFIX = re.compile(r"^(?P<name>.*?)\s*[\(\[](?P<year>(19|20)\d{2})[\)\]]\s*$")
_SEASON_DIR = re.compile(r"^(season|series|s)\s*0*(?P<n>\d{1,3})$", re.IGNORECASE)
_SPECIALS_DIR = re.compile(r"^(specials?|extras?)$", re.IGNORECASE)
_SAMPLE_FILE = re.compile(r"[\s._-]sample$", re.IGNORECASE)
_TRACK_FILE = re.compile(r"^(?:(?P<disc>\d)[-.])?(?P<n>\d{1,3})\s*[-._ ]\s*(?P<title>.+)$")
_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def is_ignored_dir(name: str) -> bool:
    return name.startswith(".") or name.casefold() in IGNORED_DIRS


def is_media_file(name: str) -> bool:
    if name.startswith(".") or name.casefold() in IGNORED_FILES:
        return False
    p = PurePosixPath(name)
    return p.suffix.lower() in MEDIA_EXTS and not _SAMPLE_FILE.search(p.stem)


def split_year(name: str) -> tuple[str, int | None]:
    m = _YEAR_SUFFIX.match(name)
    return (m["name"].strip(), int(m["year"])) if m else (name.strip(), None)


def sort_title(title: str) -> str:
    return _ARTICLES.sub("", title).casefold()


def _int(v) -> int | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    m = re.match(r"\s*(\d+)", str(v))
    return int(m.group(1)) if m else None


@dataclass
class Naming:
    kind: str  # film | episode | track | home_video | other
    title: str
    year: int | None = None
    show: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    track_no: int | None = None
    disc_no: int | None = None
    recorded_at: datetime | None = None


_CATEGORY_PREFIX = re.compile(
    r"^(?P<cat>films?|movies?|documentar(?:y|ies)|tv shows?|tv|series|episodes?|music videos?|"
    r"short films?|shorts?|videos?|clips?|home videos?|audio ?books?|podcasts?|albums?)"
    r"\s+-\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_YEAR_PREFIX = re.compile(r"^(?P<year>(19|20)\d{2})\s*-\s*(?P<name>.+)$")
_VERSION_SUFFIX = re.compile(r"\s*-\s*v\d+$", re.IGNORECASE)
_GENERIC_DIRS = {"tv", "tv shows", "tv show", "shows", "series", "television", "episodes",
                 "videos", "media"}  # fmt: skip


def strip_category(name: str) -> tuple[str | None, str]:
    """Split a leading "Film - ", "TV Show - ", "Audio Book - " ... label off a name.

    Returns (kind hint, remaining name). Hints: film | episode | home_video |
    audiobook | track. People who keep everything in one folder often label
    files this way; the label is a better signal than guessit's guess.
    """
    m = _CATEGORY_PREFIX.match(name)
    if not m:
        return None, name
    c = m["cat"].lower().replace(" ", "")
    rest = m["rest"].strip()
    if c.startswith(("film", "movie", "documentar")):
        return "film", rest
    if c.startswith(("tv", "series", "episode")):
        return "episode", rest
    if c.startswith(("audiobook", "podcast")):
        return "audiobook", rest
    if c.startswith("album"):
        return "track", rest
    return "home_video", rest  # music video, short film, clip, video, home video


def derive(rel_path: str, root_kind: str, probe: ProbeResult | None) -> Naming:
    """Work out what an item is from where it lives and what it contains."""
    p = PurePosixPath(rel_path)
    hint, stem = strip_category(p.stem)
    is_audio = (probe.media_type == "audio") if probe else p.suffix.lower() in AUDIO_EXTS
    if is_audio or root_kind == "music":
        return _track(p, stem, hint, probe)
    # Phone videos sitting among photos are home videos too.
    if root_kind in ("home_videos", "photos") or (root_kind == "mixed" and hint == "home_video"):
        return _home_video(p, stem, hint, probe)
    guess = guessit(stem + p.suffix)
    if root_kind == "tv" or (
        root_kind == "mixed"
        and (hint == "episode" or (hint is None and guess.get("type") == "episode"))
    ):
        return _episode(p, guess)
    return _film(p, stem, guess)


def _guess_title(stem: str, guess) -> tuple[str, int | None]:
    title = str(guess.get("title") or "").strip()
    year = _int(guess.get("year"))
    alt = guess.get("alternative_title")
    if title and alt and " - " in stem:
        # "KENOBI - A Star Wars Story": guessit splits the subtitle off.
        alts = alt if isinstance(alt, list) else [alt]
        title = " - ".join([title, *map(str, alts)])
    if not title and year:
        # A film whose title is a year (1917): guessit reads it as the year.
        title, year = str(year), None
    return title, year


# Quality/edition tags in a hand-named file ("Dune 1080p", "Alien (Director's Cut)").
_TECH_TAGS = re.compile(
    r"\b(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dolby ?vision|blu-?ray|bdrip|brrip|"
    r"bdremux|remux|web-?dl|web-?rip|dvdrip|hdrip|x264|x265|h\.?26[45]|hevc|avc|aac|ac3|"
    r"dts(?:-hd)?|truehd|atmos|10bit|extended(?: edition| cut)?|unrated|uncut|remastered|imax|"
    r"director'?s cut|theatrical(?: cut)?)\b",
    re.IGNORECASE,
)
_TRAILING_YEAR = re.compile(r"^(?P<name>.+?)\s+(?P<year>(?:19|20)\d{2})$")


def _literal_film_title(stem: str) -> tuple[str, int | None] | None:
    """A hand-named file ("The Darjeeling Limited", "Mickey 17") is meant
    literally: guessit reads "Limited" as a release tag and "17" as an
    episode. Scene-style names (dots/underscores) are left to guessit."""
    if "." in stem or "_" in stem or " " not in stem.strip():
        return None
    t = re.sub(r"[\[(][^\])]*[\])]", " ", stem)  # "(6GB)", "[Remux]"
    t = _TECH_TAGS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    if not t:
        return None
    year = None
    if (m := _TRAILING_YEAR.match(t)) and int(m["year"]) <= datetime.now().year + 1:
        t, year = m["name"].strip(" -"), int(m["year"])  # "Heat 1995"; not "Blade Runner 2049"
    return t, year


def _film(p: PurePosixPath, stem: str, guess) -> Naming:
    # A clean "Title (Year)" name is taken literally: guessit would read words
    # like "Classic" or "Web Rip" as release tags and drop them.
    clean_title, clean_year = split_year(stem)
    if clean_year:
        return Naming(kind="film", title=clean_title, year=clean_year)
    title, year = _literal_film_title(stem) or _guess_title(stem, guess)
    parent = p.parent.name if len(p.parts) > 1 else ""
    if parent:
        ptitle, pyear = split_year(strip_category(parent)[1])
        # A film in its own "Title (Year)" folder: the folder is usually cleaner.
        if pyear and (not title or not year or title.casefold() in ptitle.casefold()):
            title, year = ptitle, pyear
    return Naming(kind="film", title=title or stem, year=year)


def _is_season_dir(d: str) -> int | None:
    if m := _SEASON_DIR.match(d):
        return int(m["n"])
    return 0 if _SPECIALS_DIR.match(d) else None


def _episode(p: PurePosixPath, guess) -> Naming:
    dirs = list(p.parts[:-1])
    # The show is the nearest folder above any season folders, unless that's a
    # generic bucket like "TV Shows" - then the file name has to say.
    show_dir = next((d for d in reversed(dirs) if _is_season_dir(d) is None), None)
    if show_dir is not None:
        show_dir = strip_category(show_dir)[1]
        if show_dir.casefold() in _GENERIC_DIRS:
            show_dir = None
    if show_dir:
        show, year = split_year(show_dir)
    else:
        show, year = str(guess.get("title") or p.stem), _int(guess.get("year"))
    season = _int(guess.get("season"))
    if season is None:
        season = next((n for d in reversed(dirs) if (n := _is_season_dir(d)) is not None), None)
    eps = guess.get("episode")
    ep_list = eps if isinstance(eps, list) else [eps] if eps is not None else []
    episode = _int(ep_list[0]) if ep_list else None
    episode_end = _int(ep_list[-1]) if len(ep_list) > 1 else None
    title = str(guess.get("episode_title") or "").strip()
    if not title:
        title = f"Episode {episode}" if episode is not None else p.stem
    return Naming(
        kind="episode", title=title, year=year, show=show,
        season=season, episode=episode, episode_end=episode_end,
    )  # fmt: skip


def _track(p: PurePosixPath, stem: str, hint: str | None, probe: ProbeResult | None) -> Naming:
    tag = probe.tag if probe else (lambda *_: None)
    parts = p.parts[:-1]
    folder_album, folder_year = None, None
    if parts:
        folder = strip_category(parts[-1])[1]  # "Album - 2013 - AM" -> "2013 - AM"
        if m := _YEAR_PREFIX.match(folder):
            folder_album, folder_year = m["name"].strip(), int(m["year"])
        else:
            folder_album, folder_year = split_year(folder)
    folder_artist = parts[-2] if len(parts) >= 2 else None

    m = _TRACK_FILE.match(stem)
    file_title = m["title"].strip() if m else stem
    if hint == "audiobook" and not tag("album") and " - " in stem:
        # "Saber Squadron - Chapter 1 - V1": book, then chapter.
        book, _, chapter = stem.partition(" - ")
        folder_album, file_title = book.strip(), _VERSION_SUFFIX.sub("", chapter).strip()
    title = tag("title") or file_title
    track_no = _int(tag("track")) or (int(m["n"]) if m else None)
    disc_no = _int(tag("disc")) or (int(m["disc"]) if m and m["disc"] else None)
    artist = tag("artist", "album_artist") or folder_artist
    album_artist = tag("album_artist", "albumartist", "album artist") or artist
    album = tag("album") or folder_album
    year = _int(tag("date", "year", "originaldate")) or folder_year
    return Naming(
        kind="track", title=title, year=year, artist=artist, album_artist=album_artist,
        album=album, track_no=track_no, disc_no=disc_no,
    )  # fmt: skip


def _home_video(p: PurePosixPath, stem: str, hint: str | None, probe: ProbeResult | None) -> Naming:
    recorded = probe.creation_time() if probe else None
    year = recorded.year if recorded else None
    if year is None:
        for d in reversed(p.parts[:-1]):
            if re.fullmatch(r"(19|20)\d{2}", d):
                year = int(d)
                break
    title = stem
    if hint:
        # Labelled clips ("Music Video - X - V4 - H264 25mbps") carry release
        # noise guessit can strip; camera names like IMG_0001 are kept as-is.
        title = str(guessit(stem + p.suffix).get("title") or stem).strip()
    return Naming(kind="home_video", title=title, year=year, recorded_at=recorded)
