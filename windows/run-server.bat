@echo off
rem Start the media server. Double-click, or run from a terminal.
rem Uses `python -m picturehouse` so the picturehouse.exe launcher isn't locked while
rem the server runs (that would block `uv sync` / `uv add`).
cd /d "%~dp0.."
uv run python -m picturehouse serve %*
echo.
echo Server stopped.
pause
