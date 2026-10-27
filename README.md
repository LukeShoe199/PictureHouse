# PictureHouse

A home media server for your own folders of films, TV series, music,
audiobooks, home videos and photos. It plays them on TVs, phones, tablets
and browsers around the house.

- **Organised from your folders.** Films and series are matched to posters,
  plots and cast (TMDb, optional). Music follows your Artist / Album folders.
  Photos are grouped by folder and date.
- **Plays everywhere.**
  - TVs over DLNA, with nothing to install on the TV;
  - a proper LG TV app;
  - any browser, which can be added to a phone's home screen;
  - old devices too (iOS 9 Safari and similar), through the simpler *PictureHouse Lite*;
  - Chromecast.
- **Converts only when needed.** A file a device can play is sent as it is.
  Anything else is converted on the fly with FFmpeg.
- **Remembers where you were** on every device, audiobooks included.
  There are also photo slideshows with music and a music visualiser.
- **Themes.** Pick a look per device or for everyone: the original PictureHouse style, or
  *iStream*, inspired by Apple TV. Make your own with the theme language in
  [docs/themes.md](docs/themes.md).
- **Yours.** Everything runs on your own computer, with no account and no
  cloud. Your media folders are only read, never changed.

## Install

| | |
|---|---|
| **Windows 10 / 11** | Installer or portable zip: [`windows/`](windows) |
| **Linux** (Ubuntu, Kubuntu, Mint, Debian…) | Desktop app (.deb / AppImage) or background service: [`linux/`](linux) |
| **ZimaOS** (ZimaBoard, ZimaBlade, ZimaCube) | One-click app from the dashboard: [`zimaos/`](zimaos) |

Step-by-step instructions for each, plus first-time setup, are in
**[INSTALL.md](INSTALL.md)**. Downloads are on the
[Releases page](https://github.com/LukeShoe199/PictureHouse/releases).

| | |
|---|---|
| **LG TVs** | TV app: [`Smart TV Apps/LG webOS`](Smart%20TV%20Apps/LG%20webOS) |
| **Android** | Phone and tablet app: [`Android APK`](Android%20APK) |

## What's where

```
src/picturehouse/   the server (Python): library, metadata, streaming, DLNA, web console
desktop/            the window + tray app for Windows and Linux (Rust, Tauri 2)
windows/            Windows installer, portable build, run-from-source script
linux/              Linux .deb/AppImage build, install script for the background service
zimaos/             ZimaOS / Docker app file and image publishing
Dockerfile          the container image (ZimaOS, NAS, any Docker host)
Smart TV Apps/      TV apps (LG webOS now; each opens the server's /tv/ interface)
Android APK/        Android app (a launcher for the server's own pages)
tests/              test suite, with generated fixture media
docs/               HTTP API, UI design reference
```

## Development

You need [uv](https://docs.astral.sh/uv/) and FFmpeg on PATH.

```
uv sync
uv run picturehouse serve                # http://127.0.0.1:9595/
uv run pytest                            # first run generates fixture media
uv run ruff check . && uv run ruff format --check .
```

Other commands:

```
uv run picturehouse library add Films "D:\Media\Films" --kind films
uv run picturehouse scan [--full]
uv run picturehouse list [--failed]
uv run picturehouse config init|path|show|check
uv run picturehouse admin-pin [PIN] [--remove]
```

The data directory (library database, settings, artwork, caches) is
`%LOCALAPPDATA%\PictureHouse` on Windows and `~/.local/share/picturehouse`
on Linux. Override it with `--data-dir` or `PICTUREHOUSE_DATA_DIR`.

The HTTP API is described in [docs/api.md](docs/api.md).
