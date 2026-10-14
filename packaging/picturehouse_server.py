"""Entry point for the frozen (PyInstaller) server: same CLI as `picturehouse`."""

import multiprocessing
import os
import sys


def _system_libraries_for_children() -> None:
    """On Linux, PyInstaller points LD_LIBRARY_PATH at its bundled libraries.
    Programs we start inherit that, and the system's FFmpeg then loads the
    wrong library versions and fails ("ffmpeg -version exited with code 1").
    This process has loaded its own libraries already, so restore the
    original value (saved by PyInstaller) for everything it starts."""
    if not getattr(sys, "frozen", False) or sys.platform == "win32":
        return
    for var in ("LD_LIBRARY_PATH", "LIBPATH", "DYLD_LIBRARY_PATH"):
        original = os.environ.pop(f"{var}_ORIG", None)
        if original is not None:
            os.environ[var] = original
        else:
            os.environ.pop(var, None)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _system_libraries_for_children()

    from picturehouse.cli import run

    run()
