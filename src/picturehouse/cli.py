"""Command-line interface. Every feature is usable from here before any UI exists."""

from __future__ import annotations

import functools
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import func, select

from picturehouse import __version__
from picturehouse.checks import Status, run_startup_checks, worst
from picturehouse.config import Settings, load_config, template_text
from picturehouse.db.models import LibraryRootRow, MediaItem
from picturehouse.errors import ExitCode, PictureHouseError, StartupCheckFailed
from picturehouse.library.roots import add_root, remove_root
from picturehouse.log import log_context, new_job_id, setup_logging
from picturehouse.metadata.api_client import MetadataApiError
from picturehouse.paths import (
    ENV_CONFIG,
    ENV_DATA_DIR,
    DataLayout,
    resolve_data_dir,
    resolve_in_root,
)
from picturehouse.services import Services, open_services

log = logging.getLogger("picturehouse.cli")

app = typer.Typer(
    name="picturehouse",
    help="PictureHouse: home media server (library, metadata, streaming, DLNA).",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(
    help="Inspect, create and validate the configuration.", no_args_is_help=True
)
app.add_typer(config_app, name="config")
library_app = typer.Typer(help="Add, remove and list library folders.", no_args_is_help=True)
app.add_typer(library_app, name="library")
theme_app = typer.Typer(
    help="List, check and install themes (docs/themes.md).", no_args_is_help=True
)
app.add_typer(theme_app, name="theme")
profile_app = typer.Typer(
    help="Profiles: who's watching, child profiles and the exit code.", no_args_is_help=True
)
app.add_typer(profile_app, name="profile")


@dataclass(frozen=True)
class AppState:
    layout: DataLayout
    config_file: Path

    def load(self) -> Settings:
        return load_config(self.config_file)

    def start_logging(self, settings: Settings, *, console: bool = True) -> Path | None:
        lc = settings.logging
        return setup_logging(
            self.layout.log_dir,
            level=lc.level,
            console_level=lc.console_level,
            max_bytes=lc.max_bytes,
            backup_count=lc.backup_count,
            console=console,
        )

    def services(self, *, console_log: bool = False) -> Services:
        settings = self.load()
        self.start_logging(settings, console=console_log)
        return open_services(settings, self.layout, self.config_file)


def handles_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn ``PictureHouseError`` into a clear message and its exit code."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except PictureHouseError as e:
            typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(int(e.exit_code)) from e

    return wrapper


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"picturehouse {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            envvar=ENV_DATA_DIR,
            help="Folder for config, database, cache and logs.",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            envvar=ENV_CONFIG,
            help="Config file (default: <data dir>/config.toml).",
            show_default=False,
        ),
    ] = None,
) -> None:
    layout = DataLayout(resolve_data_dir(data_dir))
    config_file = config.expanduser().absolute() if config else layout.config_file
    ctx.obj = AppState(layout=layout, config_file=config_file)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


@config_app.command("path")
def config_path(ctx: typer.Context) -> None:
    """Show where config, database, cache and logs live."""
    st: AppState = ctx.obj
    exists = (
        "" if st.config_file.exists() else "  (not created yet — run `picturehouse config init`)"
    )
    rows = [
        ("data dir", st.layout.root),
        ("config", f"{st.config_file}{exists}"),
        ("database", st.layout.db_file),
        ("cache", st.layout.cache_dir),
        ("logs", st.layout.log_dir),
    ]
    for label, value in rows:
        typer.echo(f"{label:<9} {value}")


@config_app.command("init")
@handles_errors
def config_init(
    ctx: typer.Context,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a commented default config file."""
    st: AppState = ctx.obj
    if st.config_file.exists() and not force:
        typer.echo(f"config already exists: {st.config_file} (use --force to overwrite)")
        raise typer.Exit(int(ExitCode.OK))
    st.config_file.parent.mkdir(parents=True, exist_ok=True)
    st.config_file.write_text(template_text(), encoding="utf-8")
    typer.echo(f"wrote {st.config_file}")


@config_app.command("show")
@handles_errors
def config_show(ctx: typer.Context) -> None:
    """Print the effective configuration (file values plus defaults) as JSON."""
    st: AppState = ctx.obj
    typer.echo(st.load().model_dump_json(indent=2))


_STATUS_STYLE = {
    Status.OK: ("  ok", typer.colors.GREEN),
    Status.WARN: ("warn", typer.colors.YELLOW),
    Status.FAIL: ("FAIL", typer.colors.RED),
}


@config_app.command("check")
@handles_errors
def config_check(
    ctx: typer.Context,
    port: Annotated[
        bool, typer.Option("--port/--no-port", help="Check the HTTP port is free.")
    ] = True,
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
) -> None:
    """Validate the config and run the startup checks.

    Exit code 0 when everything passes, 3 when a check fails (or warns, with
    --strict), 2 when the config itself is invalid.
    """
    st: AppState = ctx.obj
    settings = st.load()
    log_file = st.start_logging(settings, console=False)

    with log_context(job_id=new_job_id("check")):
        results = run_startup_checks(settings, st.layout, include_port=port)
        defaults = "" if st.config_file.exists() else " (defaults)"
        typer.echo(f"config   {st.config_file}{defaults}")
        typer.echo(f"log      {log_file}")
        for r in results:
            label, colour = _STATUS_STYLE[r.status]
            typer.echo(typer.style(label, fg=colour) + f"  {r.name:<18} {r.detail}")
            log.log(
                logging.INFO if r.status is Status.OK else logging.WARNING,
                "startup check %s: %s",
                r.name,
                r.status.value,
                extra={"check": r.name, "status": r.status.value, "detail": r.detail},
            )

        overall = worst(results)
        log.info("startup checks finished: %s", overall.value)
        if overall is Status.FAIL or (strict and overall is Status.WARN):
            raise StartupCheckFailed(f"startup checks did not pass ({overall.value})")
        typer.echo("all checks passed" if overall is Status.OK else "passed with warnings")


# --------------------------------------------------------------------------
# library / scan / list / info
# --------------------------------------------------------------------------

KindOpt = Annotated[
    str, typer.Option("--kind", "-k", help="films | tv | music | home_videos | photos | mixed")
]


@library_app.command("add")
@handles_errors
def library_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Unique name, e.g. Films")],
    path: Annotated[Path, typer.Argument(help="Folder to index (local or network path)")],
    kind: KindOpt = "mixed",
) -> None:
    """Add a library folder to the config. Run `picturehouse scan` afterwards."""
    st: AppState = ctx.obj
    add_root(st.config_file, name, path, kind)
    note = "" if path.is_dir() else "  (folder not reachable right now — kept anyway)"
    typer.echo(f"added library {name!r}: {path.absolute()} [{kind}]{note}")


@library_app.command("remove")
@handles_errors
def library_remove(ctx: typer.Context, name: str) -> None:
    """Remove a library folder from the config. Its items are kept but hidden."""
    st: AppState = ctx.obj
    remove_root(st.config_file, name)
    typer.echo(f"removed library {name!r}")


@theme_app.command("list")
@handles_errors
def theme_list(ctx: typer.Context) -> None:
    """Themes this server has; * marks the default."""
    from picturehouse.themes import ThemeStore

    st: AppState = ctx.obj
    store = ThemeStore(st.layout.themes_dir)
    default = store.get(st.load().ui.theme).id
    for t in sorted(store.themes().values(), key=lambda t: t.id):
        where = "built-in" if t.builtin else "installed"
        mark = "*" if t.id == default else " "
        typer.echo(f"{mark} {t.id:<20} {t.name:<20} {t.version:<8} {where:<9} {t.author}")
    for p in store.problems:
        typer.echo(f"  can't use {p['folder']}: {p['error']}", err=True)


@theme_app.command("check")
@handles_errors
def theme_check(path: Annotated[Path, typer.Argument(help="A theme folder")]) -> None:
    """Check a theme folder without installing it (what's wrong, if anything)."""
    from picturehouse.themes import load_theme, render_css

    theme = load_theme(path.resolve())
    css = render_css(theme)
    typer.echo(f"ok: {theme.name} ({theme.id}) {theme.version}: {len(theme.tokens)} tokens, "
               f"layout {theme.layout}, {'own stylesheet' if theme.stylesheet else 'no stylesheet'}, "
               f"{len(theme.assets)} asset file(s); {len(css)} bytes of CSS")  # fmt: skip


@theme_app.command("install")
@handles_errors
def theme_install(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="A theme folder")],
    replace: Annotated[bool, typer.Option("--replace", help="Update an installed theme")] = False,
) -> None:
    """Check a theme folder and copy it into the data directory."""
    from picturehouse.themes import install_theme

    st: AppState = ctx.obj
    theme = install_theme(path.resolve(), st.layout.themes_dir, replace=replace)
    typer.echo(f"installed {theme.name} ({theme.id}); choose it in the console (Library -> Appearance) "
               f"or make it the default: picturehouse theme default {theme.id}")  # fmt: skip


@theme_app.command("remove")
@handles_errors
def theme_remove(
    ctx: typer.Context, theme_id: Annotated[str, typer.Argument(help="Theme id")]
) -> None:
    """Remove an installed theme (built-in ones can't be removed)."""
    import shutil

    from picturehouse.library.roots import set_config_values
    from picturehouse.themes import DEFAULT_THEME, ThemeStore

    st: AppState = ctx.obj
    theme = ThemeStore(st.layout.themes_dir).themes().get(theme_id)
    if theme is None or theme.builtin:
        raise PictureHouseError(f"no installed theme called {theme_id!r}",
                                hint="see `picturehouse theme list`")  # fmt: skip
    shutil.rmtree(theme.folder)
    if st.load().ui.theme == theme_id:
        set_config_values(st.config_file, "ui", {"theme": DEFAULT_THEME})
        typer.echo(f"it was the default; the default is now {DEFAULT_THEME}")
    typer.echo(f"removed {theme_id}")


@theme_app.command("default")
@handles_errors
def theme_default(
    ctx: typer.Context, theme_id: Annotated[str | None, typer.Argument(help="Theme id")] = None
) -> None:
    """Show the theme every device starts with, or set it."""
    from picturehouse.library.roots import set_config_values
    from picturehouse.themes import ThemeStore

    st: AppState = ctx.obj
    store = ThemeStore(st.layout.themes_dir)
    if theme_id is None:
        typer.echo(store.get(st.load().ui.theme).id)
        return
    if theme_id not in store.themes():
        raise PictureHouseError(
            f"no theme called {theme_id!r}", hint="see `picturehouse theme list`"
        )
    set_config_values(st.config_file, "ui", {"theme": theme_id})
    typer.echo(
        f"default theme is now {theme_id} (a running server uses it after a restart; the console can change it live: Library -> Appearance)"
    )


@library_app.command("browse")
@handles_errors
def library_browse(
    path: Annotated[str | None, typer.Argument(help="Folder to look inside")] = None,
) -> None:
    """Show folders on this computer, to find a library folder's path.

    Without PATH: where to start (drives, media folders). In a container, the
    paths are as the container sees them, which is what `library add` needs."""
    from picturehouse.library import browse

    if path is None:
        for place in browse.roots():
            typer.echo(f"{place.name:<28} {place.path}")
        typer.echo("\nLook inside one with: picturehouse library browse PATH")
        return
    listing = browse.list_dir(path)
    typer.echo(listing.path)
    if not listing.dirs:
        typer.echo("  (no folders inside)")
    for d in listing.dirs:
        typer.echo(f"  {d.path}")
    if listing.truncated:
        typer.echo(f"  ... (first {browse.MAX_ENTRIES} shown)")


@library_app.command("list")
@handles_errors
def library_list(ctx: typer.Context) -> None:
    """List library folders with item counts and status."""
    st: AppState = ctx.obj
    svc = st.services()
    with svc.db.session() as s:
        rows = s.execute(
            select(LibraryRootRow, func.count(MediaItem.id))
            .outerjoin(MediaItem, (MediaItem.root_id == LibraryRootRow.id) & MediaItem.available)
            .where(LibraryRootRow.configured)
            .group_by(LibraryRootRow.id)
            .order_by(LibraryRootRow.name)
        ).all()
    if not rows:
        typer.echo(
            "no libraries yet — add one with `picturehouse library add NAME PATH --kind films`"
        )
    for root, count in rows:
        status = "ok" if root.available else "OFFLINE"
        last = root.last_scan_at.strftime("%Y-%m-%d %H:%M") if root.last_scan_at else "never"
        typer.echo(f"{root.name:<14} {root.kind:<12} {count:>6} items  {status:<8} "
                   f"scanned {last}  {root.path}")  # fmt: skip


@library_app.command("kids")
@handles_errors
def library_kids(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Library name")],
    off: Annotated[bool, typer.Option("--off", help="No longer a kids library")] = False,
) -> None:
    """Mark a library as for children: child profiles see only kids libraries."""
    from picturehouse.library.roots import set_root_kids

    st: AppState = ctx.obj
    set_root_kids(st.config_file, name, not off)
    typer.echo(f"{name}: {'not ' if off else ''}a kids library (a running server: after a restart, "
               "or change it live in Library -> Profiles)")  # fmt: skip


def _profiles(st: AppState):
    from picturehouse.profiles import ProfileStore

    return ProfileStore(st.services().db, st.layout.root)


@profile_app.command("list")
@handles_errors
def profile_list(ctx: typer.Context) -> None:
    """Profiles, the devices using each, and kids libraries."""
    st: AppState = ctx.obj
    store = _profiles(st)
    devices = store.devices()
    for p in store.profiles():
        on = sorted(a for a, pid in devices.items() if pid == p.id)
        kind = "child" if p.child else "adult"
        extra = "  (devices not set use this)" if p.id == 1 else ""
        typer.echo(f"{p.id:>3}  {p.name:<20} {kind:<6} {', '.join(on) or '-'}{extra}")
    kids = [r.name for r in st.load().libraries if r.kids]
    typer.echo(f"\nkids libraries: {', '.join(kids) or 'none (child profiles see nothing)'}")
    typer.echo(f"exit code: {'set' if store.code_set else 'not set'}")


@profile_app.command("add")
@handles_errors
def profile_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="e.g. Sam")],
    child: Annotated[bool, typer.Option("--child", help="Kids libraries only")] = False,
) -> None:
    """Add a profile."""
    store = _profiles(ctx.obj)
    if child and not store.code_set:
        raise PictureHouseError("set an exit code first", hint="picturehouse profile exit-code")
    p = store.add(name, child)
    typer.echo(f"added {'child ' if p.child else ''}profile {p.name!r} (id {p.id})")


@profile_app.command("edit")
@handles_errors
def profile_edit(
    ctx: typer.Context,
    profile: Annotated[str, typer.Argument(help="Name or id")],
    name: Annotated[str | None, typer.Option("--name", help="New name")] = None,
    child: Annotated[bool | None, typer.Option("--child/--adult", help="Child or adult")] = None,
) -> None:
    """Rename a profile, or make it a child or adult profile."""
    store = _profiles(ctx.obj)
    p = store.update(store.find(profile).id, name=name, child=child)
    typer.echo(f"{p.name}: {'child' if p.child else 'adult'} profile")


@profile_app.command("remove")
@handles_errors
def profile_remove(
    ctx: typer.Context, profile: Annotated[str, typer.Argument(help="Name or id")]
) -> None:
    """Remove a profile (its devices go back to the main profile; its watch history goes)."""
    store = _profiles(ctx.obj)
    p = store.find(profile)
    store.remove(p.id)
    typer.echo(f"removed profile {p.name!r}")


@profile_app.command("assign")
@handles_errors
def profile_assign(
    ctx: typer.Context,
    address: Annotated[
        str, typer.Argument(help="The device's IP address (see the console's device list)")
    ],
    profile: Annotated[str, typer.Argument(help="Name or id")],
) -> None:
    """Put a device in a profile, e.g. a child's tablet or TV."""
    store = _profiles(ctx.obj)
    p = store.find(profile)
    store.assign(address, p.id)
    typer.echo(f"{address} now uses {p.name}")


@profile_app.command("exit-code")
@handles_errors
def profile_exit_code(
    ctx: typer.Context,
    remove: Annotated[bool, typer.Option("--remove", help="Remove the exit code")] = False,
) -> None:
    """Set the code that leaves a child profile (asked for, not shown)."""
    store = _profiles(ctx.obj)
    if remove:
        store.set_code(None)
        typer.echo("exit code removed")
        return
    if sys.stdin.isatty():
        code = typer.prompt("New exit code (4 to 12 digits)", hide_input=True,
                            confirmation_prompt=True)  # fmt: skip
    else:  # piped (scripts, `docker exec` without -t): the first line of input
        code = sys.stdin.readline()
    store.set_code(code.strip())
    typer.echo("exit code set")


@app.command("scan")
@handles_errors
def scan_cmd(
    ctx: typer.Context,
    names: Annotated[
        list[str] | None, typer.Argument(help="Libraries to scan (default all)")
    ] = None,
    full: Annotated[bool, typer.Option("--full", help="Re-probe unchanged files too")] = False,
) -> None:
    """Scan library folders. Only new or changed files are probed."""
    st: AppState = ctx.obj
    svc = st.services(console_log=False)
    for r in svc.scanner.scan(names or None, full=full):
        if not r.available:
            typer.secho(f"{r.library}: folder not reachable — {r.missing} items marked "
                        "unavailable", fg=typer.colors.YELLOW)  # fmt: skip
            continue
        typer.echo(f"{r.library}: {r.new} new, {r.changed} changed, {r.unchanged} unchanged, "
                   f"{r.failed} failed, {r.missing} missing ({r.seconds}s)")  # fmt: skip
        for e in r.walk_errors:
            typer.secho(f"  could not read {e}", fg=typer.colors.YELLOW)


def _label(i: MediaItem) -> str:
    if i.kind == "episode":
        ep = f"S{i.season or 0:02d}E{i.episode or 0:02d}"
        if i.episode_end:
            ep += f"-E{i.episode_end:02d}"
        return f"{i.show} {ep} {i.title}"
    if i.kind == "track":
        return f"{i.album_artist or i.artist} — {i.album} — {i.track_no or '?'}. {i.title}"
    return f"{i.title}" + (f" ({i.year})" if i.year else "")


@app.command("list")
@handles_errors
def list_cmd(
    ctx: typer.Context,
    kind: Annotated[
        str | None, typer.Option("--kind", "-k", help="film | episode | track | home_video")
    ] = None,
    library: Annotated[str | None, typer.Option("--library", "-l")] = None,
    failed: Annotated[bool, typer.Option("--failed", help="Only files that failed")] = False,
    unavailable: Annotated[
        bool, typer.Option("--unavailable", help="Include unavailable items")
    ] = False,
) -> None:
    """List library items."""
    st: AppState = ctx.obj
    svc = st.services()
    q = select(MediaItem, LibraryRootRow.name).join(LibraryRootRow)
    if kind:
        q = q.where(MediaItem.kind == kind)
    if library:
        q = q.where(func.lower(LibraryRootRow.name) == library.lower())
    if failed:
        q = q.where(MediaItem.probe_ok.is_(False))
    if not unavailable:
        q = q.where(MediaItem.available)
    q = q.order_by(LibraryRootRow.name, MediaItem.show, MediaItem.season, MediaItem.episode,
                   MediaItem.album, MediaItem.track_no, MediaItem.sort_title)  # fmt: skip
    with svc.db.session() as s:
        rows = s.execute(q).all()
    for item, root_name in rows:
        flag = "" if item.probe_ok else f"  [FAILED: {item.probe_error}]"
        flag += "" if item.available else "  [unavailable]"
        typer.echo(f"{item.id:>6}  {root_name:<12} {item.kind:<10} {_label(item)}{flag}")
    typer.echo(f"{len(rows)} items")


@app.command("photos")
@handles_errors
def photos_cmd(
    ctx: typer.Context,
    album: Annotated[
        str | None, typer.Argument(help="Show the photos in this album (folder)")
    ] = None,
) -> None:
    """List photo albums (folders of photos in "photos" libraries), or one album's photos."""
    from picturehouse.web import queries as Q

    st: AppState = ctx.obj
    svc = st.services()
    with svc.db.session() as s:
        if album is None:
            albums = Q.photo_albums(s)
            if not albums:
                typer.echo("no photos yet: add a folder with `picturehouse library add NAME PATH "
                           "--kind photos`, then `picturehouse scan`")  # fmt: skip
            for a in albums:
                when = (a["from"] or "")[:10]
                until = (a["to"] or "")[:10]
                span = when if when == until else f"{when} to {until}"
                typer.echo(f"{a['photos']:>6}  {a['path'] or '(top level)'}  {span}")
            return
        items = Q.album_photos(s, album)
        if not items:
            raise PictureHouseError(
                f"no album {album!r}", hint="run `picturehouse photos` to list them"
            )
        for i in items:
            taken = Q.iso(i.recorded_at)[:16].replace("T", " ") if i.recorded_at else "?"
            typer.echo(f"{i.id:>6}  {taken}  {i.width}x{i.height}  {i.rel_path}")


@app.command("info")
@handles_errors
def info_cmd(ctx: typer.Context, item_id: int) -> None:
    """Show everything known about one item."""
    st: AppState = ctx.obj
    svc = st.services()
    with svc.db.session() as s:
        item = s.get(MediaItem, item_id)
        if item is None:
            raise PictureHouseError(f"no item with id {item_id}")
        root = item.root
        streams = list(item.streams)
    rows = [
        ("title", _label(item)),
        ("kind", f"{item.kind} ({item.media_type})"),
        ("file", f"{root.name}: {item.rel_path}"),
        ("path", resolve_in_root(Path(root.path), item.rel_path)),
        ("size", f"{item.size:,} bytes"),
        ("duration", f"{item.duration:.1f}s" if item.duration else "?"),
        ("container", item.container),
        ("video", f"{item.vcodec} {item.width}x{item.height} {item.pix_fmt}" if item.vcodec else "-"),
        ("audio", f"{item.acodec} {item.channels}ch" if item.acodec else "-"),
        ("status", "ok" if item.probe_ok else f"FAILED ({item.retry_count}x): {item.probe_error}"),
        ("available", item.available),
    ]  # fmt: skip
    for k, v in rows:
        typer.echo(f"{k:<10} {v}")
    for st_ in streams:
        bits = [st_.codec or "?", st_.language or "", st_.title or "",
                f"{st_.channels}ch" if st_.channels else "",
                f"{st_.width}x{st_.height}" if st_.width else "",
                "default" if st_.is_default else "", "cover art" if st_.attached_pic else ""]  # fmt: skip
        typer.echo(f"  #{st_.index} {st_.type:<9} " + " ".join(b for b in bits if b))


# --------------------------------------------------------------------------
# metadata: match / unmatched / fix / refresh
# --------------------------------------------------------------------------


def _summary_line(summary) -> str:
    fmt = lambda d: ", ".join(f"{n} {k}" for k, n in sorted(d.items())) or "none"  # noqa: E731
    line = f"films: {fmt(summary.films)} · shows: {fmt(summary.shows)}"
    if summary.episodes:
        line += f" ({summary.episodes} episodes updated)"
    return line


def _no_key_note(svc: Services) -> None:
    if svc.matcher.tmdb is None and svc.matcher.omdb is None:
        typer.secho("No TMDb or OMDb API key configured: only .nfo files were used. Add one:\n"
                    "  [metadata] tmdb_api_key = \"...\"   (free at themoviedb.org/settings/api)\n"
                    "  [metadata] omdb_api_key = \"...\"   (free at omdbapi.com/apikey.aspx)",
                    fg=typer.colors.YELLOW)  # fmt: skip
    elif svc.matcher.tmdb is None:
        typer.secho("Using OMDb only (no TMDb key): no backdrops or episode plots, and about\n"
                    "1,000 lookups a day; anything left over is picked up on the next run.",
                    fg=typer.colors.YELLOW)  # fmt: skip


@app.command("match")
@handles_errors
def match_cmd(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Re-search everything (not manual matches)")
    ] = False,
    library: Annotated[list[str] | None, typer.Option("--library", "-l")] = None,
) -> None:
    """Match films and series to .nfo files and TMDb."""
    st: AppState = ctx.obj
    svc = st.services()
    _no_key_note(svc)
    summary = svc.matcher.match(force=force, libraries=library or None)
    typer.echo(_summary_line(summary))
    for e in summary.errors:
        typer.secho(f"  {e}", fg=typer.colors.RED)
    _save_local_if_on(svc)
    if summary.films.get("ambiguous") or summary.films.get("unmatched") or \
            summary.shows.get("ambiguous") or summary.shows.get("unmatched"):  # fmt: skip
        typer.echo("see `picturehouse unmatched`, then `picturehouse fix ID --tmdb N`")


def _save_local_if_on(svc) -> None:
    """CLI runs end with the process, so save local copies now, not in the background."""
    if svc.settings.metadata.save_local and svc.saver is not None:
        sm = svc.saver.sync()
        typer.echo(f"local copies: {sm.written} written, {sm.unchanged} unchanged"
                   + (f", {sm.failed} failed" if sm.failed else ""))  # fmt: skip


ENV_ADMIN_PIN = "PICTUREHOUSE_ADMIN_PIN"


def _apply_env_pin(layout) -> str | None:
    """``PICTUREHOUSE_ADMIN_PIN``: set the admin PIN when the server starts.
    For servers no browser ever reaches as "this PC" (a Docker container, a
    NAS): without a PIN nothing could change their folders or settings.

    Returns why no PIN came of it ("blank" or "invalid"), for the console to
    explain, or None (PIN set, or the variable isn't used)."""
    from picturehouse.web.access import AdminGate, valid_pin

    pin = os.environ.get(ENV_ADMIN_PIN)
    gate = AdminGate(layout.root)
    if not (pin or "").strip():
        if pin is not None and not gate.pin_set:  # declared but left blank
            log.warning(
                "no admin PIN: set %s, or run `picturehouse admin-pin <digits>`;"
                " until then only this machine itself can change folders and settings",
                ENV_ADMIN_PIN,
            )
            return "blank"
        return None
    pin = pin.strip()
    if not valid_pin(pin):
        log.warning("%s must be 4 to 12 digits (numbers only); ignored", ENV_ADMIN_PIN)
        return None if gate.pin_set else "invalid"
    if not gate.check_pin(pin):  # new or changed: set it (signs other devices out)
        gate.set_pin(pin)
        log.info("admin PIN set from %s", ENV_ADMIN_PIN)
    return None


@app.command("admin-pin")
@handles_errors
def admin_pin_cmd(
    ctx: typer.Context,
    pin: Annotated[str | None, typer.Argument(help="4 to 12 digits")] = None,
    remove: Annotated[bool, typer.Option("--remove", help="Remove the PIN")] = False,
) -> None:
    """Set the admin PIN that lets other devices change folders and settings.

    Without a PIN only the computer running PictureHouse can. On a NAS or in
    Docker nothing is "that computer", so set one here (or with the
    PICTUREHOUSE_ADMIN_PIN environment variable).
    """
    from picturehouse.web.access import AdminGate, valid_pin

    st: AppState = ctx.obj
    gate = AdminGate(st.layout.root)
    if remove:
        gate.set_pin(None)
        typer.echo("admin PIN removed: only this computer can make changes now")
        return
    if pin is None:
        typer.echo("an admin PIN is set" if gate.pin_set else "no admin PIN is set")
        return
    if not valid_pin(pin):
        raise PictureHouseError("the PIN must be 4 to 12 digits")
    gate.set_pin(pin)
    typer.echo("admin PIN set; other devices were signed out")


@app.command("save-local")
@handles_errors
def save_local_cmd(
    ctx: typer.Context,
    library: Annotated[list[str] | None, typer.Option("--library", "-l")] = None,
) -> None:
    """Write .nfo files and artwork next to matched films and series now.

    Uses Kodi/Jellyfin names (<film>.nfo, <film>-poster.jpg, tvshow.nfo,
    poster.jpg...). Files PictureHouse didn't write are never replaced. To do
    this automatically after every match, turn on save_local in the metadata
    section of config.toml (or "Save a copy next to the files" in the console).
    """
    st: AppState = ctx.obj
    svc = st.services()
    sm = svc.saver.sync(library or None)
    typer.echo(f"{sm.written} files written, {sm.unchanged} already up to date")
    if sm.skipped_foreign:
        typer.echo(f"{sm.skipped_foreign} left alone: a file PictureHouse didn't write is there")
    for e in sm.errors:
        typer.secho(f"  {e}", fg=typer.colors.RED)
    if not svc.settings.metadata.save_local:
        typer.echo(
            "tip: turn on save_local (metadata section of config.toml) to keep these up to date"
        )


@app.command("unmatched")
@handles_errors
def unmatched_cmd(ctx: typer.Context) -> None:
    """List films and series that need a match, with candidates."""
    import json as _json

    from picturehouse.db.models import ShowMeta

    st: AppState = ctx.obj
    svc = st.services()
    with svc.db.session() as s:
        films = list(s.scalars(select(MediaItem).where(
            MediaItem.kind == "film", MediaItem.available, MediaItem.probe_ok,
            MediaItem.match_status.in_(("ambiguous", "unmatched"))).order_by(MediaItem.sort_title)))  # fmt: skip
        shows = list(s.scalars(select(ShowMeta).where(
            ShowMeta.match_status.in_(("ambiguous", "unmatched")))))  # fmt: skip
        show_ids = {sh.parsed_show: s.scalar(select(MediaItem.id).where(
            MediaItem.parsed_show == sh.parsed_show)) for sh in shows}  # fmt: skip
        pending = s.scalar(select(func.count(MediaItem.id)).where(
            MediaItem.match_status == "pending", MediaItem.kind.in_(("film", "episode")))) or 0  # fmt: skip
    if not films and not shows:
        typer.echo("nothing needs a match")
    for label, ident, status, raw in (
        [(f"film  {f.parsed_title}" + (f" ({f.parsed_year})" if f.parsed_year else ""), f.id,
          f.match_status, f.match_candidates) for f in films]
        + [(f"show  {sh.parsed_show}", show_ids[sh.parsed_show], sh.match_status,
            sh.match_candidates) for sh in shows]
    ):  # fmt: skip
        typer.echo(f"{ident:>6}  {label}  [{status}]")
        for c in _json.loads(raw or "[]")[:5]:
            year = f" ({c['year']})" if c.get("year") else ""
            typer.echo(f"          --tmdb {c['tmdb_id']:<8} {c['title']}{year}  {c['score']:.0%}")
    if pending:
        typer.echo(f"{pending} items not looked at yet — run `picturehouse match`")
    _no_key_note(svc)


@app.command("fix")
@handles_errors
def fix_cmd(
    ctx: typer.Context,
    item_id: Annotated[int, typer.Argument(help="Item id (an episode fixes its whole show)")],
    tmdb: Annotated[int | None, typer.Option("--tmdb", help="TMDb id to match to")] = None,
    imdb: Annotated[
        str | None, typer.Option("--imdb", help="IMDb id (tt…) to match to, via OMDb")
    ] = None,
    title: Annotated[str | None, typer.Option(help="Set the title (locked)")] = None,
    year: Annotated[int | None, typer.Option(help="Set the year (locked)")] = None,
    unlock: Annotated[bool, typer.Option("--unlock", help="Forget manual changes")] = False,
) -> None:
    """Fix one item's metadata. Fixes are never overwritten by scans or matching."""
    st: AppState = ctx.obj
    svc = st.services()
    with svc.db.session() as s:
        item = s.get(MediaItem, item_id)
        if item is None:
            raise PictureHouseError(f"no item with id {item_id}")
        kind, show = item.kind, item.parsed_show
    target_show = show if kind == "episode" else None
    if unlock:
        svc.matcher.unlock(None if target_show else item_id, target_show)
        svc.matcher.match()
        typer.echo("manual changes removed; re-matched")
    if tmdb is not None or imdb is not None:
        if imdb is not None and not imdb.startswith("tt"):
            raise PictureHouseError(f"{imdb!r} isn't an IMDb id", hint="they look like tt0111161")
        ids = {"tmdb_id": tmdb, "imdb_id": imdb if tmdb is None else None}
        try:
            if target_show:
                svc.matcher.match_show(target_show, manual=True, **ids)
            elif kind == "film":
                svc.matcher.match_film(item_id, manual=True, **ids)
            else:
                raise PictureHouseError(f"a {kind} can't be matched online")
        except MetadataApiError as e:
            raise PictureHouseError(str(e)) from e
        typer.echo(f"matched to TMDb {tmdb}" if tmdb is not None else f"matched to {imdb} (OMDb)")
    fields = {k: v for k, v in (("title", title), ("year", year)) if v is not None}
    if fields:
        svc.matcher.edit(None if target_show else item_id, target_show, fields)
        typer.echo("updated " + ", ".join(fields))
    if not (unlock or tmdb is not None or imdb is not None or fields):
        raise PictureHouseError("nothing to do",
                                hint="use --tmdb N, --imdb tt…, --title, --year or --unlock")  # fmt: skip
    _save_local_if_on(svc)


@app.command("refresh")
@handles_errors
def refresh_cmd(
    ctx: typer.Context,
    item_ids: Annotated[
        list[int] | None, typer.Argument(help="Items to refresh (default all)")
    ] = None,
) -> None:
    """Re-fetch metadata for matched items (keeps their matches and your edits)."""
    st: AppState = ctx.obj
    svc = st.services()
    _no_key_note(svc)
    typer.echo(_summary_line(svc.matcher.refresh(item_ids or None)))
    _save_local_if_on(svc)


@app.command("check-audio")
@handles_errors
def check_audio_cmd(
    ctx: typer.Context,
    item_ids: Annotated[
        list[int] | None, typer.Argument(help="Items to check (default: new/changed)")
    ] = None,
    all_: Annotated[
        bool, typer.Option("--all", help="Re-check everything, including dismissed")
    ] = False,
) -> None:
    """Check films and episodes for broken sound (silence, missing dialogue)."""
    st: AppState = ctx.obj
    svc = st.services()
    counts = svc.audio.run(force=all_, item_ids=item_ids or None)
    typer.echo(", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "nothing to check")
    with svc.db.session() as s:
        flagged = list(s.scalars(select(MediaItem).where(
            MediaItem.audio_check == "warn", MediaItem.available).order_by(MediaItem.sort_title)))  # fmt: skip
    for i in flagged:
        typer.secho(f"{i.id:>6}  {_label(i)}", fg=typer.colors.YELLOW)
        typer.echo(f"        {i.audio_note}")


@app.command("logs")
@handles_errors
def logs_cmd(
    ctx: typer.Context,
    lines: Annotated[int, typer.Option("--lines", "-n", help="How many entries")] = 50,
    dlna: Annotated[
        bool, typer.Option("--dlna", help="Only what DLNA clients (TVs) asked for")
    ] = False,
    level: Annotated[str | None, typer.Option(help="Minimum level, e.g. WARNING")] = None,
) -> None:
    """Show recent log entries in readable form."""
    import json as _json
    from collections import deque
    from datetime import datetime

    from picturehouse.log import LOG_FILE_NAME

    st: AppState = ctx.obj
    f = st.layout.log_dir / LOG_FILE_NAME
    if not f.exists():
        raise PictureHouseError(f"no log file yet at {f}")
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    floor = order.get((level or "DEBUG").upper(), 10)
    keep: deque = deque(maxlen=lines)
    with f.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                e = _json.loads(raw)
            except ValueError:
                continue
            if dlna and not e.get("logger", "").startswith("picturehouse.dlna"):
                continue
            if order.get(e.get("level"), 0) < floor:
                continue
            keep.append(e)
    skip = {"ts", "level", "logger", "msg", "job_id", "exc"}
    for e in keep:
        extras = " ".join(f"{k}={v}" for k, v in e.items() if k not in skip and v not in (None, ""))
        try:  # logs are stored in UTC; show local time
            when = datetime.fromisoformat(e["ts"]).astimezone().strftime("%H:%M:%S")
        except (KeyError, ValueError):
            when = "?"
        typer.echo(f"{when} {e['level']:<7} {e['msg']}" + (f"  [{extras}]" if extras else ""))
        if e.get("exc"):
            typer.echo(e["exc"])


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------


@app.command("serve")
@handles_errors
def serve_cmd(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option(help="Interface to listen on")] = None,
    port: Annotated[int | None, typer.Option(help="HTTP port")] = None,
    dlna: Annotated[bool, typer.Option("--dlna/--no-dlna", help="Announce to TVs (SSDP)")] = True,
    scan: Annotated[
        bool, typer.Option("--scan/--no-scan", help="Incremental scan on startup")
    ] = True,
) -> None:
    """Run the server: web console, streaming API and DLNA."""
    import uvicorn

    from picturehouse.dlna.server import DLNAServer
    from picturehouse.dlna.ssdp import local_ipv4_addresses
    from picturehouse.library.watcher import FolderWatcher
    from picturehouse.streaming import procs
    from picturehouse.web.app import create_app

    st: AppState = ctx.obj
    settings = st.load()
    server_cfg = settings.server.model_copy(
        update={k: v for k, v in (("host", host), ("http_port", port)) if v is not None}
    )
    settings = settings.model_copy(update={"server": server_cfg})
    st.start_logging(settings, console=True)
    # Nothing this server starts may outlive it (clean restarts, no orphans).
    procs.tie_children_to_this_process()
    if os.environ.get("PICTUREHOUSE_LAUNCHED_BY") == "desktop":
        # ... and it doesn't outlive the desktop app.
        parent = os.environ.get("PICTUREHOUSE_PARENT_PID", "")
        procs.exit_with_parent(int(parent) if parent.isdigit() else None)
    pin_problem = _apply_env_pin(st.layout)

    with log_context(job_id=new_job_id("startup")):
        results = run_startup_checks(settings, st.layout, include_port=True)
        failed = [r for r in results if r.status is Status.FAIL]
        for r in results:
            if r.status is not Status.OK:
                log.warning("startup check %s: %s", r.name, r.detail)
        if failed:
            raise StartupCheckFailed(
                "cannot start: " + "; ".join(f"{r.name}: {r.detail}" for r in failed)
            )
        svc = open_services(settings, st.layout, st.config_file)

    dlna_srv = DLNAServer(svc) if dlna and settings.dlna.enabled else None
    https = None
    if server_cfg.https_port:
        from picturehouse.checks import check_port
        from picturehouse.tls import ensure_certificates

        port_ok = check_port(server_cfg.host, server_cfg.https_port)
        if port_ok.status is Status.OK:
            tls_files = ensure_certificates(st.layout.root / "tls", local_ipv4_addresses())
            https = {"port": server_cfg.https_port, "files": tls_files}
        else:
            log.warning("HTTPS disabled: %s", port_ok.detail)
    svc.pin_problem = pin_problem
    web = create_app(svc, dlna=dlna_srv, https=https)
    stop = threading.Event()
    svc.background_saves = True  # local metadata copies are written as matches happen

    if settings.metadata.match_on_scan:
        svc.scanner.on_change.append(lambda: web.state.start_match())
    if settings.scan.audio_check:
        svc.scanner.on_change.append(lambda: web.state.start_audio_check())

    def background_scans() -> None:
        if scan and settings.scan.on_startup:
            web.state.start_scan()
        if settings.metadata.match_on_scan:
            # Items left pending last time (e.g. a TMDb key was added since).
            while svc.scanner.running and not stop.wait(1):
                pass
            web.state.start_match()
        if settings.scan.audio_check:
            while svc.scanner.running and not stop.wait(1):
                pass
            web.state.start_audio_check()  # anything unchecked or changed since last time
        interval = settings.scan.interval_minutes * 60
        while interval and not stop.wait(interval):
            web.state.start_scan()

    p = server_cfg.http_port
    ips = local_ipv4_addresses() if server_cfg.host in ("0.0.0.0", "::") else [server_cfg.host]
    typer.secho(f"\n  {settings.server.friendly_name} — picturehouse {__version__}", bold=True)
    typer.echo(f"  Web console:  http://127.0.0.1:{p}/")
    for ip in ips:
        typer.echo(f"                http://{ip}:{p}/")
    if https:
        typer.echo("  Phones:       (HTTPS, for casting and installing the app)")
        for ip in ips:
            typer.echo(f"                https://{ip}:{https['port']}/")
    dlna_note = f"on (TVs see it as {settings.server.friendly_name!r})" if dlna_srv else "off"
    typer.echo(f"  DLNA:         {dlna_note}")
    typer.echo(f"  Data dir:     {st.layout.root}\n  Stop with Ctrl+C\n")

    config = uvicorn.Config(web, host=server_cfg.host, port=p, log_config=None,
                            access_log=False, timeout_graceful_shutdown=3)  # fmt: skip
    server = uvicorn.Server(config)
    web.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    https_server = None
    if https:
        # Second listener for the same app, on a thread: uvicorn only installs
        # signal handlers on the main thread, so Ctrl+C stays with `server`.
        tls_files = https["files"]
        https_server = uvicorn.Server(uvicorn.Config(
            web, host=server_cfg.host, port=https["port"], log_config=None, access_log=False,
            ssl_certfile=str(tls_files.cert), ssl_keyfile=str(tls_files.key),
            timeout_graceful_shutdown=3, lifespan="off"))  # fmt: skip
        threading.Thread(target=https_server.run, name="https", daemon=True).start()
    if dlna_srv:
        dlna_srv.start()
    if settings.scan.watch:
        svc.watcher = FolderWatcher(svc.db, svc.scanner)
        svc.watcher.start()
    threading.Thread(target=background_scans, name="scheduler", daemon=True).start()
    try:
        server.run()
    finally:
        stop.set()
        if https_server:
            https_server.should_exit = True
        if dlna_srv:
            dlna_srv.stop()
        procs.kill_all()
        svc.close()
        log.info("server stopped")


def run() -> None:
    """Console-script entry point."""
    # Keep odd file names from crashing output on non-UTF-8 consoles/pipes.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="backslashreplace")
    app()
