"""Library roots: config.toml is the source of truth, the database mirrors it.

``library add`` / ``remove`` (CLI and web console) edit the config file with
tomlkit so the user's comments and layout survive, then re-sync.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from pydantic import ValidationError
from sqlalchemy import select

from picturehouse.config import LibraryRoot, Settings, parse_config
from picturehouse.db import Database
from picturehouse.db.models import LibraryRootRow, utcnow
from picturehouse.errors import ConfigError


def sync_roots(db: Database, settings: Settings) -> list[LibraryRootRow]:
    """Upsert configured roots into the database. Roots no longer in the config
    are flagged ``configured=False``; their items are kept (and hidden)."""
    wanted = {r.name.casefold(): r for r in settings.libraries}
    with db.session() as s:
        rows = {row.name.casefold(): row for row in s.scalars(select(LibraryRootRow))}
        for key, root in wanted.items():
            row = rows.get(key)
            if row is None:
                row = LibraryRootRow(name=root.name, path=str(root.path), kind=root.kind,
                                     available=root.path.is_dir())  # fmt: skip
                if not row.available:
                    row.unavailable_since = utcnow()
                s.add(row)
            else:
                row.name, row.path, row.kind = root.name, str(root.path), root.kind
            row.configured = True
        for key, row in rows.items():
            if key not in wanted:
                row.configured = False
        s.flush()
        return list(s.scalars(select(LibraryRootRow).where(LibraryRootRow.configured)))


def _load_doc(config_file: Path) -> tomlkit.TOMLDocument:
    if config_file.exists():
        return tomlkit.parse(config_file.read_text(encoding="utf-8-sig"))
    from picturehouse.config import template_text

    return tomlkit.parse(template_text())


def _write_doc(config_file: Path, doc: tomlkit.TOMLDocument) -> Settings:
    text = tomlkit.dumps(doc)
    settings = parse_config(text, source=str(config_file))  # validate before writing
    config_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_file.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(config_file)
    return settings


def add_root(config_file: Path, name: str, path: Path, kind: str) -> Settings:
    path = path.expanduser().absolute()
    if any(ord(c) < 32 for c in str(path)):
        raise ConfigError("the folder path contains control characters")
    try:
        LibraryRoot(name=name, path=path, kind=kind)  # validate early, clear errors
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"invalid library: {msgs}") from e
    doc = _load_doc(config_file)
    libs = doc.get("libraries")
    if libs is None:
        libs = tomlkit.aot()
        doc.append("libraries", libs)
    for t in libs:
        if str(t.get("name", "")).casefold() == name.casefold():
            raise ConfigError(f"a library named {t['name']!r} already exists")
    entry = tomlkit.table()
    entry.add("name", name)
    # Literal string keeps Windows backslashes readable in the file.
    entry.add("path", tomlkit.string(str(path), literal="'" not in str(path)))
    entry.add("kind", kind)
    libs.append(entry)
    return _write_doc(config_file, doc)


def remove_root(config_file: Path, name: str) -> Settings:
    doc = _load_doc(config_file)
    libs = doc.get("libraries")
    keep = [t for t in (libs or []) if str(t.get("name", "")).casefold() != name.casefold()]
    if libs is None or len(keep) == len(libs):
        raise ConfigError(f"no library named {name!r}")
    new = tomlkit.aot()
    for t in keep:
        new.append(t)
    doc["libraries"] = new
    return _write_doc(config_file, doc)


def set_config_values(config_file: Path, section: str, values: dict) -> Settings:
    """Set keys in one [section] of the config, keeping comments and layout.

    The result is validated before anything is written; an invalid value
    raises ``ConfigError`` and leaves the file untouched.
    """
    doc = _load_doc(config_file)
    table = doc.get(section)
    if table is None:
        table = tomlkit.table()
        doc.add(section, table)
    for k, v in values.items():
        table[k] = "" if v is None else v
    return _write_doc(config_file, doc)
