"""Generate the fixture media library with FFmpeg.

Media is generated rather than committed: the files are small (2 s, 320x180)
but binary, and generating them proves the FFmpeg toolchain works on each CI
platform. Every file is described in ``manifest.json`` with what a correct
scanner/prober should conclude about it, so later phases can assert against
the manifest instead of hand-written expectations.

The library deliberately includes trouble: odd codecs and containers, broken
and empty files, non-ASCII and emoji names, a path longer than Windows'
260-character limit, nested and oddly numbered TV folders, tagged and untagged
music, sidecars (.nfo, .srt, artwork) and junk files a scanner must ignore.

Build it with ``uv run python scripts/build_fixtures.py [target_dir]``; tests
build it automatically into ``tests/.fixture-media`` and reuse it while
``GENERATOR_VERSION`` is unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from picturehouse.ffmpeg import locate_tool, no_window_flags
from picturehouse.paths import long_path

# Bump whenever specs change so cached libraries are rebuilt.
GENERATOR_VERSION = 2  # 2: Photos/
MANIFEST = "manifest.json"
DURATION = 2  # seconds

LONG_DIR = (
    "A Deliberately Very Long Film Title Used To Exceed The Classic Windows MAX_PATH "
    "Limit Of Two Hundred And Sixty Characters (2018)"
)


@dataclass
class Entry:
    path: str  # POSIX-style, relative to the library root
    category: str  # films | tv | music | home_videos | photos
    kind: str  # media | broken | sidecar | noise
    expect: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    skipped: str | None = None  # reason, if this platform couldn't build it


# ---------------------------------------------------------------- ffmpeg args

H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "64k"]


def _video_in(rate: int = 25) -> list[str]:
    return ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={rate}:duration={DURATION}"]


def _audio_in(freq: int = 440) -> list[str]:
    return [
        "-f", "lavfi", "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={DURATION}",
    ]  # fmt: skip


def _av(vcodec: list[str], acodec: list[str], *extra: str) -> list[str]:
    return [*_video_in(), *_audio_in(), "-map", "0:v", "-map", "1:a",
            *vcodec, *acodec, "-shortest", *extra]  # fmt: skip


@dataclass
class MediaSpec:
    entry: Entry
    args: list[str]
    encoders: tuple[str, ...]
    post: Callable[[Path], None] | None = None  # runs after encoding
    long: bool = False  # build in a short temp dir, then move into place


def _meta(**tags: str) -> list[str]:
    out: list[str] = []
    for k, v in tags.items():
        out += ["-metadata", f"{k}={v}"]
    return out


def _truncate(fraction: float) -> Callable[[Path], None]:
    def post(p: Path) -> None:
        data = p.read_bytes()
        p.write_bytes(data[: int(len(data) * fraction)])

    return post


def _media_specs() -> list[MediaSpec]:
    v = lambda vc, ac: {"vcodec": vc, "acodec": ac, "duration": DURATION}  # noqa: E731
    specs = [
        # ---------------- Films
        MediaSpec(
            Entry(
                "Films/The Grand Test (2019)/The Grand Test (2019).mkv",
                "films",
                "media",
                {**v("h264", "aac"), "title": "The Grand Test", "year": 2019},
                "Clean film in its own folder with .nfo, poster and external subs.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
        ),
        MediaSpec(
            Entry(
                "Films/Amélie (2001).mp4",
                "films",
                "media",
                {**v("h264", "aac"), "title": "Amélie", "year": 2001},
                "Non-ASCII (Latin-1) name, loose in the root, faststart MP4.",
            ),
            _av(H264, AAC, "-movflags", "+faststart"),
            ("libx264", "aac"),
        ),
        MediaSpec(
            Entry(
                "Films/東京物語 (1953)/東京物語 (1953).mkv",
                "films",
                "media",
                {**v("h264", "ac3"), "title": "東京物語", "year": 1953},
                "CJK name, AC-3 audio.",
            ),
            _av(H264, ["-c:a", "ac3", "-b:a", "96k"]),
            ("libx264", "ac3"),
        ),
        MediaSpec(
            Entry(
                "Films/Emoji 🎬 Night (2022).mp4",
                "films",
                "media",
                {**v("h264", "aac"), "title": "Emoji 🎬 Night", "year": 2022},
                "Astral-plane character (surrogate pair in UTF-16) in the name.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
        ),
        MediaSpec(
            Entry(
                "Films/Odd.Codecs.2020.2160p.HEVC.10bit-GRP.mkv",
                "films",
                "media",
                {
                    "vcodec": "hevc",
                    "acodec": "ac3",
                    "duration": DURATION,
                    "pix_fmt": "yuv420p10le",
                    "audio_streams": 2,
                    "subtitle_streams": 1,
                    "audio_languages": ["eng", "fra"],
                    "title": "Odd Codecs",
                    "year": 2020,
                },
                "Scene-style name; HEVC 10-bit, two audio languages, embedded subs.",
            ),
            [
                *_video_in(),
                *_audio_in(440),
                *_audio_in(660),
                "-f",
                "srt",
                "-i",
                "{SRT}",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:a",
                "-map",
                "3:s",
                "-c:v",
                "libx265",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p10le",
                "-x265-params",
                "log-level=error",
                "-c:a",
                "ac3",
                "-b:a",
                "96k",
                "-c:s",
                "srt",
                "-metadata:s:a:0",
                "language=eng",
                "-metadata:s:a:1",
                "language=fra",
                "-metadata:s:s:0",
                "language=eng",
                "-shortest",
            ],
            ("libx265", "ac3", "srt"),
        ),
        MediaSpec(
            Entry(
                "Films/Old Classic (1975).avi",
                "films",
                "media",
                {**v("mpeg4", "mp3"), "title": "Old Classic", "year": 1975},
                "AVI with MPEG-4 Part 2 (Xvid-style) and MP3 — needs transcode for most TVs.",
            ),
            _av(
                ["-c:v", "mpeg4", "-q:v", "5", "-vtag", "XVID"],
                ["-c:a", "libmp3lame", "-b:a", "96k"],
            ),
            ("mpeg4", "libmp3lame"),
        ),
        MediaSpec(
            Entry(
                "Films/Broadcast Capture (2010).ts",
                "films",
                "media",
                {**v("mpeg2video", "mp2"), "title": "Broadcast Capture", "year": 2010},
                "MPEG-TS with MPEG-2 video, as from a DVB recording.",
            ),
            _av(["-c:v", "mpeg2video", "-q:v", "5"], ["-c:a", "mp2", "-b:a", "128k"]),
            ("mpeg2video", "mp2"),
        ),
        MediaSpec(
            Entry(
                "Films/Web Rip (2021).webm",
                "films",
                "media",
                {**v("vp9", "opus"), "title": "Web Rip", "year": 2021},
                "WebM with VP9/Opus.",
            ),
            _av(
                ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k"],
                ["-c:a", "libopus", "-b:a", "48k"],
            ),
            ("libvpx-vp9", "libopus"),
        ),
        MediaSpec(
            Entry(
                f"Films/{LONG_DIR}/{LONG_DIR}/{LONG_DIR[:60]}.mp4",
                "films",
                "media",
                {**v("h264", "aac"), "long_path": True},
                "Relative path alone is >300 characters.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
            long=True,
        ),
        MediaSpec(
            Entry(
                "Films/sample/The Grand Test (2019) sample.mkv",
                "films",
                "noise",
                {**v("h264", "aac"), "ignore": True},
                "Release 'sample' clip — a scanner should skip sample folders.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
        ),
        # ---------------- Broken
        MediaSpec(
            Entry(
                "Films/Broken/Truncated Film (2015).mp4",
                "films",
                "broken",
                {"probe_fails": True},
                "MP4 with moov atom at the end, then cut to 40% — unreadable index.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
            post=_truncate(0.4),
        ),
        # ---------------- TV
        *[
            MediaSpec(
                Entry(
                    f"TV/Test Show/{rel}",
                    "tv",
                    "media",
                    {**v("h264", "aac"), "show": "Test Show", "season": s, "episode": e},
                    note,
                ),
                _av(H264, AAC),
                ("libx264", "aac"),
            )
            for rel, s, e, note in [
                ("Season 01/Test Show - S01E01 - Pilot.mkv", 1, 1, "Canonical naming."),
                ("Season 01/Test Show - S01E02 - The Second One.mkv", 1, 2, "Canonical naming."),
                ("Season 02/Test.Show.S02E01.720p.WEB-DL.mkv", 2, 1, "Scene-style naming."),
                ("Specials/Test Show - S00E01 - Behind the Scenes.mkv", 0, 1, "Specials folder."),
            ]
        ],
        MediaSpec(
            Entry(
                "TV/Another Show (2015)/Season 1/another.show.1x03.mkv",
                "tv",
                "media",
                {**v("h264", "aac"), "show": "Another Show", "season": 1, "episode": 3},
                "Lower-case NxNN numbering, unpadded season folder, year in show folder.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
        ),
        MediaSpec(
            Entry(
                "TV/Another Show (2015)/Season 1/Another Show - S01E01-E02 - Double.mkv",
                "tv",
                "media",
                {**v("h264", "aac"), "show": "Another Show", "season": 1, "episode": [1, 2]},
                "Multi-episode file.",
            ),
            _av(H264, AAC),
            ("libx264", "aac"),
        ),
        # ---------------- Music
        MediaSpec(
            Entry(
                "Music/Test Artist/First Album (2020)/01 - Opening.flac",
                "music",
                "media",
                {
                    "acodec": "flac",
                    "duration": DURATION,
                    "artist": "Test Artist",
                    "album": "First Album",
                    "title": "Opening",
                    "track": 1,
                    "year": 2020,
                },
                "Fully tagged FLAC; folder has cover.jpg.",
            ),
            [
                *_audio_in(440),
                "-c:a",
                "flac",
                *_meta(
                    title="Opening",
                    artist="Test Artist",
                    album_artist="Test Artist",
                    album="First Album",
                    track="1/2",
                    date="2020",
                    genre="Test",
                ),
            ],
            ("flac",),
        ),
        MediaSpec(
            Entry(
                "Music/Test Artist/First Album (2020)/02 - Second Song.flac",
                "music",
                "media",
                {
                    "acodec": "flac",
                    "duration": DURATION,
                    "artist": "Test Artist",
                    "album": "First Album",
                    "title": "Second Song",
                    "track": 2,
                    "year": 2020,
                },
                "Fully tagged FLAC.",
            ),
            [
                *_audio_in(550),
                "-c:a",
                "flac",
                *_meta(
                    title="Second Song",
                    artist="Test Artist",
                    album_artist="Test Artist",
                    album="First Album",
                    track="2/2",
                    date="2020",
                    genre="Test",
                ),
            ],
            ("flac",),
        ),
        MediaSpec(
            Entry(
                "Music/Björk-ish Ensemble/Ünïcode Album/01 - Café.mp3",
                "music",
                "media",
                {
                    "acodec": "mp3",
                    "duration": DURATION,
                    "artist": "Björk-ish Ensemble",
                    "album": "Ünïcode Album",
                    "title": "Café",
                    "track": 1,
                    "embedded_art": True,
                },
                "ID3v2 tags with non-ASCII text and embedded cover art.",
            ),
            [
                *_audio_in(330),
                "-i",
                "{COVER}",
                "-map",
                "0:a",
                "-map",
                "1:v",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
                "-c:v",
                "mjpeg",
                "-disposition:v:0",
                "attached_pic",
                "-id3v2_version",
                "3",
                *_meta(title="Café", artist="Björk-ish Ensemble", album="Ünïcode Album", track="1"),
            ],
            ("libmp3lame", "mjpeg"),
        ),
        MediaSpec(
            Entry(
                "Music/Various/Untagged Track.m4a",
                "music",
                "media",
                {"acodec": "aac", "duration": DURATION, "untagged": True},
                "No tags at all — metadata must come from the file name or lookup.",
            ),
            [*_audio_in(220), *AAC, "-map_metadata", "-1"],
            ("aac",),
        ),
        # ---------------- Home videos
        MediaSpec(
            Entry(
                "Home Videos/2023/IMG_0001.MOV",
                "home_videos",
                "media",
                {**v("h264", "aac"), "creation_time": "2023-07-04T12:00:00"},
                "Phone-style QuickTime with creation_time, upper-case extension.",
            ),
            _av(H264, AAC, "-metadata", "creation_time=2023-07-04T12:00:00Z", "-f", "mov"),
            ("libx264", "aac"),
        ),
        MediaSpec(
            Entry(
                "Home Videos/2023/VID_20230812_093000.mp4",
                "home_videos",
                "media",
                {**v("h264", "aac"), "fps": 50},
                "50 fps, date-in-name.",
            ),
            [*_video_in(50), *_audio_in(), "-map", "0:v", "-map", "1:a", *H264, *AAC, "-shortest"],
            ("libx264", "aac"),
        ),
    ]
    return specs


# ------------------------------------------------------------ static files

SRT = """1
00:00:00,000 --> 00:00:01,000
Hello from the fixture library.

2
00:00:01,000 --> 00:00:02,000
Ünïcödé subtitles work too.
"""

MOVIE_NFO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>The Grand Test</title>
  <year>2019</year>
  <uniqueid type="tmdb" default="true">999001</uniqueid>
  <plot>A film that exists only to be scanned.</plot>
</movie>
"""

TVSHOW_NFO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
  <title>Test Show</title>
  <uniqueid type="tmdb" default="true">999002</uniqueid>
</tvshow>
"""


def _static_entries() -> list[tuple[Entry, bytes | None]]:
    """(entry, content). ``None`` content means an image generated by ffmpeg."""
    return [
        (
            Entry(
                "Films/The Grand Test (2019)/The Grand Test (2019).en.srt",
                "films",
                "sidecar",
                {"subtitle_language": "en"},
            ),
            SRT.encode("utf-8"),
        ),
        (
            Entry("Films/The Grand Test (2019)/movie.nfo", "films", "sidecar", {"tmdb_id": 999001}),
            MOVIE_NFO.encode("utf-8"),
        ),
        (
            Entry(
                "Films/The Grand Test (2019)/poster.jpg", "films", "sidecar", {"artwork": "poster"}
            ),
            None,
        ),
        (
            Entry("TV/Test Show/tvshow.nfo", "tv", "sidecar", {"tmdb_id": 999002}),
            TVSHOW_NFO.encode("utf-8"),
        ),
        (
            Entry(
                "Music/Test Artist/First Album (2020)/cover.jpg",
                "music",
                "sidecar",
                {"artwork": "cover"},
            ),
            None,
        ),
        (
            Entry(
                "Films/Broken/Empty File (2016).mkv",
                "films",
                "broken",
                {"probe_fails": True},
                "Zero bytes.",
            ),
            b"",
        ),
        (
            Entry(
                "Films/Broken/Not A Video (2017).mkv",
                "films",
                "broken",
                {"probe_fails": True},
                "Plain text with a video extension.",
            ),
            b"This is not a Matroska file.\n" * 20,
        ),
        (
            Entry(
                "Music/Broken/corrupt.mp3",
                "music",
                "broken",
                {"probe_fails": True},
                "Random bytes.",
            ),
            os.urandom(4096),
        ),
        (Entry("Films/Thumbs.db", "films", "noise", {"ignore": True}), b"\x00" * 64),
        (Entry("Films/desktop.ini", "films", "noise", {"ignore": True}), b"[.ShellClassInfo]\r\n"),
        (Entry("Films/.DS_Store", "films", "noise", {"ignore": True}), b"\x00\x00\x00\x01Bud1"),
        (
            Entry("Films/The Grand Test (2019)/notes.txt", "films", "noise", {"ignore": True}),
            b"Not media.\n",
        ),
    ]


# ------------------------------------------------------------------ build


class _Runner:
    def __init__(self, ffmpeg: Path) -> None:
        self.ffmpeg = ffmpeg
        out = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            check=True,
            creationflags=no_window_flags(),
        ).stdout.decode("utf-8", "replace")
        self.encoders = {
            line.split()[1]
            for line in out.splitlines()
            if len(line.split()) > 1 and len(line.split()[0]) == 6 and line.startswith(" ")
        }

    def run(self, args: list[str], out: Path) -> None:
        cmd = [str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args, str(out)]
        proc = subprocess.run(cmd, capture_output=True, creationflags=no_window_flags())
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg failed for {out.name}: {err}")


def _build_photos(root: Path, runner) -> list[Entry]:
    """Photos/: shapes a photo library meets (made with Pillow, plus one
    phone video made with ffmpeg)."""
    from PIL import Image

    out: list[Entry] = []
    base = root / "Photos"

    def put(rel: str, expect: dict, notes: str, kind: str = "media") -> Path:
        dest = base.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.append(Entry(f"Photos/{rel}", "photos", kind, expect, notes))
        return dest

    # Phone JPEG stored sideways: EXIF orientation 6 = rotate 90° to display.
    im = Image.new("RGB", (640, 480), (200, 80, 40))
    exif = im.getexif()
    exif[274] = 6  # Orientation
    exif.get_ifd(0x8769)[36867] = "2024:08:15 14:30:00"  # DateTimeOriginal
    im.save(put("2024 Cornwall/IMG_1001.JPG",
                {"width": 480, "height": 640, "taken_at": "2024-08-15T14:30:00", "album": "2024 Cornwall"},
                "Sideways phone photo (EXIF orientation 6) with a date taken; upper-case .JPG."),
            "JPEG", exif=exif)  # fmt: skip
    # iPhone HEIC: browsers and TVs can't show it, so it must be converted.
    heic = put("2024 Cornwall/IMG_1002.HEIC",
               {"width": 400, "height": 300, "taken_at": "2024-08-16T09:05:00"},
               "iPhone HEIC: served as JPEG.")  # fmt: skip
    heic_entry = out[-1]
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        h = Image.new("RGB", (400, 300), (40, 120, 200))
        hx = h.getexif()
        hx.get_ifd(0x8769)[36867] = "2024:08:16 09:05:00"
        h.save(heic, "HEIF", exif=hx)
    except Exception as e:  # pragma: no cover - platform without a HEIF encoder
        heic_entry.skipped = f"no HEIF encoder: {e}"
    # A phone video among the photos: a home video, not a photo.
    vid = put("2024 Cornwall/VID_20240815_150000.mp4", {"kind": "home_video"},
              "Phone video in a photo folder.")  # fmt: skip
    runner.run([*_video_in(), *H264, "-t", str(DURATION)], vid)
    # Transparent PNG, no EXIF: dated by the file's time; shown on the dark background.
    Image.new("RGBA", (200, 120), (255, 255, 255, 0)).save(
        put("Screenshots/logo.png", {"width": 200, "height": 120, "album": "Screenshots"},
            "Transparent PNG without EXIF."))  # fmt: skip
    put("Screenshots/broken.jpg", {"error": True}, "Not a picture at all.", "broken").write_bytes(
        b"this is not a jpeg")  # fmt: skip
    # Artwork beside films is never a photo: the Films library must ignore it.
    return out


def manifest_path(root: Path) -> Path:
    return root / MANIFEST


def is_current(root: Path) -> bool:
    try:
        data = json.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("generator_version") == GENERATOR_VERSION


def load_manifest(root: Path) -> list[Entry]:
    data = json.loads(manifest_path(root).read_text(encoding="utf-8"))
    return [Entry(**e) for e in data["entries"]]


def build(root: Path, *, ffmpeg: Path | None = None, force: bool = False) -> list[Entry]:
    """Build the library under ``root`` (reusing it if already current)."""
    if not force and is_current(root):
        return load_manifest(root)
    if root.exists():
        shutil.rmtree(long_path(root.absolute()))
    root.mkdir(parents=True)
    runner = _Runner(ffmpeg or locate_tool("ffmpeg"))
    entries: list[Entry] = []

    with tempfile.TemporaryDirectory(prefix="picturehouse-fx-") as tmp_s:
        tmp = Path(tmp_s)
        srt = tmp / "subs.srt"
        srt.write_text(SRT, encoding="utf-8")
        cover = tmp / "cover.jpg"
        runner.run(["-f", "lavfi", "-i", "color=c=0x2a6f97:s=300x300", "-frames:v", "1"], cover)
        poster = tmp / "poster.jpg"
        runner.run(["-f", "lavfi", "-i", "testsrc2=s=300x450", "-frames:v", "1"], poster)

        for entry, content in _static_entries():
            dest = root.joinpath(*entry.path.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                shutil.copyfile(poster if entry.expect.get("artwork") == "poster" else cover, dest)
            else:
                dest.write_bytes(content)
            entries.append(entry)

        entries += _build_photos(root, runner)

        for spec in _media_specs():
            entry = spec.entry
            missing = [e for e in spec.encoders if e not in runner.encoders]
            if missing:
                entry.skipped = f"ffmpeg lacks encoder(s): {', '.join(missing)}"
                entries.append(entry)
                continue
            dest = root.joinpath(*entry.path.split("/"))
            target = tmp / f"build{dest.suffix}" if spec.long else dest
            target.parent.mkdir(parents=True, exist_ok=True)
            args = [a.replace("{SRT}", str(srt)).replace("{COVER}", str(cover)) for a in spec.args]
            runner.run(args, target)
            if spec.post:
                spec.post(target)
            if spec.long:
                try:
                    long_path(dest.parent.absolute()).mkdir(parents=True, exist_ok=True)
                    shutil.move(target, long_path(dest.absolute()))
                except OSError as e:
                    entry.skipped = f"filesystem rejected long path: {e}"
            entries.append(entry)

    manifest_path(root).write_text(
        json.dumps(
            {"generator_version": GENERATOR_VERSION, "entries": [asdict(e) for e in entries]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return entries
