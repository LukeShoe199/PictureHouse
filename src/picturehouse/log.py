"""Structured logging: JSON lines to a rotating file, readable lines to the console.

Context such as a job or request id is attached with ``log_context``; every
record logged inside the block (in the same thread or asyncio task) carries it.

    with log_context(job_id=new_job_id("scan"), library="Films"):
        log.info("scan started")
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import re
import secrets
import sys
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_FILE_NAME = "picturehouse.log"

_context: ContextVar[dict[str, Any]] = ContextVar("picturehouse_log_context", default={})  # noqa: B039

# Attributes every LogRecord has; anything else came from `extra=` and is logged.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime", "ctx"}
)


def new_job_id(prefix: str = "job") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[dict[str, Any]]:
    merged = {**_context.get(), **fields}
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = _context.get()
        return True


# Secrets that can appear in logged URLs or messages (TMDb v3 sends the key as
# ?api_key=, OMDb as ?apikey=; v4 tokens go in an Authorization header).
_SECRET = re.compile(r"(?i)((?:api_?key|access_token|token)=)[^&\s\"']+|(Bearer\s+)[\w.\-]+")


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "REDACTED", text)


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "ctx", {}) or {})
        entry.update(_extras(record))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, ensure_ascii=False, default=str))


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        fields = {**(getattr(record, "ctx", {}) or {}), **_extras(record)}
        tail = (" " + " ".join(f"{k}={v}" for k, v in fields.items())) if fields else ""
        line = f"{ts} {record.levelname:<7} {record.name}: {record.getMessage()}{tail}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return redact(line)


_HANDLER_MARK = "_picturehouse_handler"


def setup_logging(
    log_dir: Path | None,
    *,
    level: str = "INFO",
    console_level: str = "INFO",
    max_bytes: int = 10_000_000,
    backup_count: int = 5,
    console: bool = True,
) -> Path | None:
    """Configure the root logger. Safe to call again (replaces our handlers).

    Returns the log file path, or ``None`` when file logging is disabled.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_MARK, False):
            root.removeHandler(h)
            h.close()

    handlers: list[logging.Handler] = []
    log_file: Path | None = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / LOG_FILE_NAME
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(JsonFormatter())
        handlers.append(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(console_level)
        ch.setFormatter(ConsoleFormatter())
        handlers.append(ch)

    for h in handlers:
        setattr(h, _HANDLER_MARK, True)
        h.addFilter(_ContextFilter())
        root.addHandler(h)

    levels = [logging.getLevelName(level), logging.getLevelName(console_level)]
    root.setLevel(min(levels) if handlers else logging.WARNING)
    # httpx/httpcore log every request URL at INFO, query string (API keys)
    # included. Our clients log what matters themselves.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file
