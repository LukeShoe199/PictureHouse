"""SQLite engine (WAL), Alembic migrations with automatic backup, sessions."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from picturehouse.errors import PictureHouseError

log = logging.getLogger(__name__)

BACKUPS_KEPT = 5


class DatabaseError(PictureHouseError):
    pass


def _pragmas(dbapi_conn, foreign_keys: bool) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def _on_connect(dbapi_conn, _record) -> None:
    _pragmas(dbapi_conn, foreign_keys=True)


def _on_connect_migrating(dbapi_conn, _record) -> None:
    # Foreign keys OFF while migrating: Alembic's SQLite batch mode rebuilds a
    # table by copying it and DROPping the original, and with foreign keys on
    # that DROP cascades and deletes every row that references it (this wiped
    # media_stream and playback_state in migration 0002 during development).
    _pragmas(dbapi_conn, foreign_keys=False)


def _alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("picturehouse.db").joinpath("migrations")))
    cfg.attributes["engine"] = engine
    return cfg


class Database:
    def __init__(self, path: Path, backup_dir: Path | None = None) -> None:
        self.path = path
        self.backup_dir = backup_dir
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        event.listen(self.engine, "connect", _on_connect)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    # ---------------------------------------------------------------- migrations

    def current_revision(self) -> str | None:
        with self.engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def head_revision(self) -> str | None:
        return ScriptDirectory.from_config(_alembic_config(self.engine)).get_current_head()

    def migrate(self, target: str = "head") -> bool:
        """Upgrade to head. Backs the file up first if it has data. Returns True
        if a migration ran."""
        current = self.current_revision()
        head = self.head_revision() if target == "head" else target
        if current == head:
            return False
        if current is not None:
            self._backup(current)
        log.info("migrating database %s -> %s", current, head, extra={"db": str(self.path)})
        # A separate engine for migrations, with foreign keys off (see
        # _on_connect_migrating); the app's pooled connections are dropped first
        # so nothing holds the file in an older schema.
        self.engine.dispose()
        mig = create_engine(f"sqlite:///{self.path}", connect_args={"timeout": 30})
        event.listen(mig, "connect", _on_connect_migrating)
        try:
            command.upgrade(_alembic_config(mig), target)
            with mig.connect() as conn:
                broken = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise DatabaseError(f"migration left {len(broken)} broken references")
        except DatabaseError:
            raise
        except Exception as e:  # pragma: no cover - surfaced to the user verbatim
            raise DatabaseError(
                f"database migration failed: {e}",
                hint=f"a backup was written to {self.backup_dir}" if current else None,
            ) from e
        finally:
            mig.dispose()
        return True

    def _backup(self, revision: str) -> Path | None:
        if self.backup_dir is None or not self.path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / f"library-{stamp}-{revision}.db"
        # Use SQLite's online backup so WAL contents are included.
        import sqlite3

        with sqlite3.connect(self.path) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
        backups = sorted(self.backup_dir.glob("library-*.db"))
        for old in backups[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)
        log.info("database backed up", extra={"backup": str(dest)})
        return dest

    # ------------------------------------------------------------------ sessions

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessions()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()
