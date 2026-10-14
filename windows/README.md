# PictureHouse for Windows

**To install:** download `PictureHouse-<version>-setup.exe` from the
[Releases page](https://github.com/LukeShoe199/PictureHouse/releases) and run
it. Full steps, including the portable zip and running from source, are in
[INSTALL.md → Windows](../INSTALL.md#windows).

## What's in this folder

| File | What it's for |
|---|---|
| `run-server.bat` | Run the server from the source code (double-click). Needs [uv](https://docs.astral.sh/uv/) and FFmpeg. |
| `build_windows.py` | Builds the installer and the portable zip into `dist\windows\`. |
| `picturehouse.iss` | The Inno Setup installer script `build_windows.py` compiles. |

The window and tray app itself is in [`../desktop`](../desktop) (shared with
Linux), and the server in [`../src`](../src) (shared by every platform).

## Building the installer

Needs [Rust](https://rustup.rs), [Node.js](https://nodejs.org),
[uv](https://docs.astral.sh/uv/) and
[Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`). From the repository's top folder:

```powershell
uv sync
uv run python windows/build_windows.py
```

This makes:

- `dist\windows\PictureHouse-<version>-setup.exe`
- `dist\windows\PictureHouse-<version>-windows-portable.zip`

Add `--skip-build` to repackage the last build without recompiling.
