# PictureHouse for Linux

There are three ways to install. Full steps are in
[INSTALL.md → Linux](../INSTALL.md#linux-ubuntu-kubuntu).

- **Desktop app** (Ubuntu, Kubuntu, Mint, Debian, or any distribution via the
  AppImage). The same window and tray icon as on Windows. Download the
  `.deb` or `.AppImage` from the
  [Releases page](https://github.com/LukeShoe199/PictureHouse/releases).
- **Background service** (servers, or PCs without a screen). Use
  `install.sh` from this folder.
- **Docker.** Use the same image as ZimaOS; see [`../zimaos`](../zimaos).

## What's in this folder

| File | What it's for |
|---|---|
| `install.sh` | Installs PictureHouse from the source code as a systemd user service. Run it as yourself, not with sudo. Options: `--no-service`, `--uninstall`. |
| `build_linux.py` | Builds the `.deb` and `.AppImage` into `dist/linux/`, inside Docker (works from Windows too). |
| `build.Dockerfile` | The Ubuntu 22.04 build environment that `build_linux.py` uses. |

## Building the desktop app

You need Docker. From the repository's top folder:

```bash
uv run python linux/build_linux.py
```

It builds in Ubuntu 22.04, so the result runs on Ubuntu 22.04 and newer,
Debian 12, Linux Mint 21 and Fedora 36 or later.
