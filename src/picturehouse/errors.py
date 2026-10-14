"""Error types and process exit codes.

Every error the CLI can surface derives from ``PictureHouseError`` and carries the
exit code the process should end with. Per-file problems during scans are *not*
raised — they are recorded against the file (see library/scanner.py).
These exceptions are for conditions that stop a command.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1  # unexpected / generic
    CONFIG = 2  # config file missing required values, unparsable or invalid
    STARTUP_CHECK = 3  # one or more startup checks failed
    FFMPEG = 4  # ffmpeg / ffprobe not found or not runnable


class PictureHouseError(Exception):
    """Base class for errors that end a command with a clear message."""

    exit_code: ExitCode = ExitCode.ERROR

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message}\n  hint: {self.hint}" if self.hint else self.message


class ConfigError(PictureHouseError):
    exit_code = ExitCode.CONFIG


class StartupCheckFailed(PictureHouseError):
    exit_code = ExitCode.STARTUP_CHECK


class FFmpegNotFound(PictureHouseError):
    exit_code = ExitCode.FFMPEG
