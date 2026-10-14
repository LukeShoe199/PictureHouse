"""picturehouse — home media server (library, metadata, streaming, DLNA)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("picturehouse")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
