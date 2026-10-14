from __future__ import annotations

import os
import shutil

import pytest
from sqlalchemy import select

import fixture_library
from conftest_services import make_services
from picturehouse.db.models import LibraryRootRow, MediaItem
from picturehouse.library.naming import derive, is_ignored_dir, is_media_file

# ------------------------------------------------------------------ naming


@pytest.mark.parametrize(
    ("rel", "kind", "expect"),
    [
        ("Old Classic (1975).avi", "films", {"title": "Old Classic", "year": 1975}),
        ("Web Rip (2021).webm", "films", {"title": "Web Rip", "year": 2021}),
        ("Odd.Codecs.2020.2160p.HEVC.10bit-GRP.mkv", "films", {"title": "Odd Codecs", "year": 2020}),
        ("The Grand Test (2019)/The Grand Test (2019).mkv", "films",
         {"title": "The Grand Test", "year": 2019}),
        ("Test Show/Season 02/Test.Show.S02E01.720p.WEB-DL.mkv", "tv",
         {"show": "Test Show", "season": 2, "episode": 1}),
        ("Another Show (2015)/Season 1/another.show.1x03.mkv", "tv",
         {"show": "Another Show", "year": 2015, "season": 1, "episode": 3}),
        ("Another Show (2015)/Season 1/Another Show - S01E01-E02 - Double.mkv", "tv",
         {"episode": 1, "episode_end": 2, "title": "Double"}),
        ("Test Show/Specials/Test Show - S00E01 - Behind the Scenes.mkv", "tv",
         {"season": 0, "episode": 1}),
        ("Some Show/Season 3/episode five.mkv", "tv", {"show": "Some Show", "season": 3}),
        ("Artist/Album (1999)/03 - Song.mp3", "music",
         {"kind": "track", "artist": "Artist", "album": "Album", "year": 1999, "track_no": 3,
          "title": "Song"}),
        ("2023/IMG_0001.MOV", "home_videos", {"kind": "home_video", "year": 2023}),
    ],
)  # fmt: skip
def test_derive(rel, kind, expect):
    _check(rel, kind, expect)


# Real-world names from a flat "everything in one folder" collection, where each
# file is labelled with its category. Found while testing against user media.
@pytest.mark.parametrize(
    ("rel", "expect"),
    [
        ("1917.mp4", {"kind": "film", "title": "1917", "year": None}),
        ("Film - KENOBI - A Star Wars Story (6GB).mp4",
         {"kind": "film", "title": "KENOBI - A Star Wars Story"}),
        ("Music Video - Running On Empty - V4 - H264 25mbps.mp4",
         {"kind": "home_video", "title": "Running On Empty"}),
        ("Short Film - RTF x Golf  - V1.mp4", {"kind": "home_video", "title": "RTF x Golf"}),
        ("TV Show - South Park - S07E10 - Grey Dawn.mp4",
         {"kind": "episode", "show": "South Park", "season": 7, "episode": 10,
          "title": "Grey Dawn"}),
        ("Audio Book - Saber Squadron - Chapter 1 - V1.wav",
         {"kind": "track", "album": "Saber Squadron", "title": "Chapter 1"}),
        ("Album - 2013 - AM/01 Do I Wanna Know_.mp3",
         {"kind": "track", "album": "AM", "year": 2013, "track_no": 1}),
        ("TV Shows/South.Park.S07E10.mkv", {"kind": "episode", "show": "South Park"}),
        ("TV Shows/South Park/Season 7/South Park - S07E10.mkv",
         {"show": "South Park", "season": 7}),
    ],
)  # fmt: skip
def test_derive_labelled_flat_folder(rel, expect):
    _check(rel, "mixed", expect)


def _check(rel, kind, expect):
    n = derive(rel, kind, None)
    for k, v in expect.items():
        assert getattr(n, k) == v, (k, getattr(n, k))


def test_ignore_rules():
    assert not is_media_file("Thumbs.db")
    assert not is_media_file(".hidden.mkv")
    assert not is_media_file("Film (2019) sample.mkv")
    assert not is_media_file("notes.txt")
    assert is_media_file("Film.MKV")
    assert is_ignored_dir("Sample") and is_ignored_dir(".git") and is_ignored_dir("$RECYCLE.BIN")
    assert not is_ignored_dir("Season 1")


# ------------------------------------------------------------------ scanner


@pytest.fixture
def svc(media_library, tmp_path):
    s = make_services(media_library, tmp_path / "data")
    yield s
    s.close()


def _items(svc, **where):
    with svc.db.session() as s:
        q = select(MediaItem)
        for k, v in where.items():
            q = q.where(getattr(MediaItem, k) == v)
        return list(s.scalars(q))


def test_scan_matches_manifest(svc, media_library):
    results = svc.scanner.scan()
    assert all(r.available for r in results)
    # The Photos folder isn't a configured library here (test_photos.py has it).
    manifest = {e.path: e for e in fixture_library.load_manifest(media_library)
                if not e.skipped and e.category != "photos"}  # fmt: skip
    prefix = {"Films": "Films/", "TV": "TV/", "Music": "Music/", "Home": "Home Videos/"}
    with svc.db.session() as s:
        rows = s.execute(select(MediaItem, LibraryRootRow.name).join(LibraryRootRow)).all()
    seen = {prefix[name] + i.rel_path: i for i, name in rows}
    expected_media = {p for p, e in manifest.items() if e.kind in ("media", "broken")}
    # Every media and broken file was recorded; noise and sidecars were not.
    assert set(seen) == expected_media
    for path, item in seen.items():
        e = manifest[path]
        if e.kind == "broken":
            assert not item.probe_ok and item.probe_error and item.retry_count == 1
            continue
        assert item.probe_ok, (path, item.probe_error)
        for key in ("vcodec", "acodec", "title", "year", "show", "season", "artist", "album"):
            if key in e.expect:
                assert getattr(item, key) == e.expect[key], (path, key)
        if isinstance(e.expect.get("episode"), list):
            assert [item.episode, item.episode_end] == e.expect["episode"]
        elif "episode" in e.expect:
            assert item.episode == e.expect["episode"]


def test_rescan_is_incremental_and_retries_are_bounded(svc):
    svc.scanner.scan()
    second = {r.library: r for r in svc.scanner.scan()}
    assert second["TV"].unchanged == 6 and second["TV"].new == second["TV"].changed == 0
    # broken files are retried, but only up to MAX_RETRIES
    for _ in range(4):
        svc.scanner.scan()
    assert all(i.retry_count == 3 for i in _items(svc, probe_ok=False))
    assert {r.library: r.failed for r in svc.scanner.scan()}["Films"] == 0


def test_changed_file_is_reprobed(svc, media_library, tmp_path):
    svc.scanner.scan()
    target = media_library / "Films" / "Amélie (2001).mp4"
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    try:
        res = {r.library: r for r in svc.scanner.scan(["Films"])}
        assert res["Films"].changed == 1
    finally:
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_missing_root_marks_unavailable_and_recovers(media_library, tmp_path):
    lib = tmp_path / "lib"
    shutil.copytree(media_library / "TV", lib / "TV")
    from picturehouse.config import parse_config
    from picturehouse.paths import DataLayout
    from picturehouse.services import open_services

    toml = f"[[libraries]]\nname='TV'\npath='{lib / 'TV'}'\nkind='tv'\n"
    svc = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    try:
        svc.scanner.scan()
        assert len(_items(svc, available=True)) == 6
        (lib / "TV").rename(lib / "TV-unplugged")
        (r,) = svc.scanner.scan()
        assert not r.available and r.missing == 6
        assert len(_items(svc)) == 6  # nothing deleted
        assert not _items(svc, available=True)
        (lib / "TV-unplugged").rename(lib / "TV")
        (r,) = svc.scanner.scan()
        assert r.available and r.unchanged == 6
        assert len(_items(svc, available=True)) == 6
    finally:
        svc.close()


def test_deleted_file_marked_unavailable(media_library, tmp_path):
    lib = tmp_path / "Music"
    shutil.copytree(media_library / "Music", lib)
    from picturehouse.config import parse_config
    from picturehouse.paths import DataLayout
    from picturehouse.services import open_services

    toml = f"[[libraries]]\nname='M'\npath='{lib}'\nkind='music'\n"
    svc = open_services(parse_config(toml), DataLayout(tmp_path / "d"), tmp_path / "d" / "c.toml")
    try:
        svc.scanner.scan()
        (lib / "Various" / "Untagged Track.m4a").unlink()
        (r,) = svc.scanner.scan()
        assert r.missing == 1
        gone = _items(svc, title="Untagged Track")
        assert len(gone) == 1 and not gone[0].available
    finally:
        svc.close()


def test_migration_backs_up_existing_db(tmp_path):
    from picturehouse.db import Database

    db = Database(tmp_path / "library.db", tmp_path / "backups")
    assert db.migrate() is True  # fresh database: nothing to back up
    assert not list(tmp_path.glob("backups/*.db"))
    assert db.migrate() is False  # already at head
    # Simulate an older schema: the next migrate must back the file up first.
    with db.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num = 'old'")
    backup = db._backup("old")
    assert backup is not None and backup.exists()
    db.dispose()


def test_migration_0002_backfills_existing_rows(tmp_path):
    """Regression: the 0002 backfill was missing, so migrated rows had empty parsed_* fields."""
    import sqlite3

    from alembic import command

    from picturehouse.db import Database
    from picturehouse.db.session import _alembic_config

    db = Database(tmp_path / "library.db", tmp_path / "backups")
    command.upgrade(_alembic_config(db.engine), "0001")
    with db.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO library_root (id, name, path, kind, configured, available) "
            "VALUES (1, 'TV', '/tv', 'tv', 1, 1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO media_item (root_id, rel_path, size, mtime_ns, media_type, kind, title, "
            "sort_title, year, show, has_embedded_art, probe_ok, retry_count, available, "
            "added_at, updated_at) VALUES (1, 'S/a.mkv', 1, 1, 'video', 'episode', 'Pilot', "
            "'pilot', 2019, 'Some Show', 0, 1, 0, 1, '2026-01-01', '2026-01-01')"
        )
        conn.exec_driver_sql(
            'INSERT INTO media_stream (item_id, "index", type, codec, is_default, is_forced, '
            "attached_pic) VALUES (1, 0, 'video', 'h264', 1, 0, 0)"
        )
        conn.exec_driver_sql(
            "INSERT INTO playback_state (item_id, position, completed, updated_at) "
            "VALUES (1, 123.5, 0, '2026-01-01')"
        )
    assert db.migrate() is True
    db.dispose()
    with sqlite3.connect(tmp_path / "library.db") as c:
        row = c.execute(
            "SELECT parsed_title, parsed_year, parsed_show, metadata_source, match_status, locked "
            "FROM media_item"
        ).fetchone()
        # Regression: rebuilding media_item with foreign keys ON cascaded and deleted these.
        streams = c.execute("SELECT codec FROM media_stream").fetchall()
        progress = c.execute("SELECT position FROM playback_state").fetchall()
    assert row == ("Pilot", 2019, "Some Show", "file", "pending", "[]")
    assert streams == [("h264",)] and progress == [(123.5,)]
    assert list((tmp_path / "backups").glob("library-*-0001.db"))  # backed up first


def test_every_migration_keeps_child_rows(tmp_path):
    """Each schema step must keep streams and playback positions (SQLite batch
    mode + foreign keys once deleted them) and 0003 must queue videos for a
    re-probe so HDR data gets filled in."""
    import sqlite3

    from picturehouse.db import Database

    db = Database(tmp_path / "library.db", tmp_path / "backups")
    db.migrate("0002")
    with db.engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO library_root (id, name, path, kind, configured, "
                             "available) VALUES (1, 'F', '/f', 'films', 1, 1)")  # fmt: skip
        conn.exec_driver_sql(
            "INSERT INTO media_item (root_id, rel_path, size, mtime_ns, media_type, kind, title, "
            "sort_title, has_embedded_art, probe_ok, retry_count, available, added_at, "
            "updated_at, parsed_title, metadata_source, match_status, locked) VALUES "
            "(1, 'a.mkv', 1, 999, 'video', 'film', 'A', 'a', 0, 1, 0, 1, '2026-01-01', "
            "'2026-01-01', 'A', 'tmdb', 'matched', '[]')"
        )
        conn.exec_driver_sql("INSERT INTO media_stream (item_id, \"index\", type, codec, "
                             "is_default, is_forced, attached_pic) VALUES (1, 0, 'video', "
                             "'hevc', 1, 0, 0)")  # fmt: skip
        conn.exec_driver_sql("INSERT INTO playback_state (item_id, position, completed, "
                             "updated_at) VALUES (1, 42.0, 0, '2026-01-01')")  # fmt: skip
    assert db.migrate() is True
    db.dispose()
    with sqlite3.connect(tmp_path / "library.db") as c:
        assert c.execute("SELECT codec FROM media_stream").fetchall() == [("hevc",)]
        assert c.execute("SELECT position FROM playback_state").fetchall() == [(42.0,)]
        assert c.execute("SELECT mtime_ns, match_status, color_transfer FROM media_item"
                         ).fetchone() == (0, "matched", None)  # fmt: skip
