#!/usr/bin/env bash
# Install PictureHouse from this source folder on Linux (Ubuntu, Kubuntu,
# Debian, Fedora...) and run it as a background service for your user.
#
#   cd PictureHouse && ./linux/install.sh
#
# What it does:
#   1. checks FFmpeg is installed (asks you to install it if not);
#   2. installs uv (Python project tool) into ~/.local/bin if missing;
#   3. installs PictureHouse's Python packages into .venv in this folder;
#   4. adds a systemd user service, starts it, and keeps it running after you
#      log out and after reboots ("lingering").
# Run it again after `git pull` to update. Your library lives in
# ~/.local/share/picturehouse and is never touched by this script.
#
# Options:  --no-service   install only; run it yourself with `uv run picturehouse serve`
#           --uninstall    stop and remove the service (keeps your library)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/picturehouse.service"
SERVICE=1

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --no-service) SERVICE=0 ;;
    --uninstall)
      systemctl --user disable --now picturehouse.service 2>/dev/null || true
      rm -f "$UNIT"
      systemctl --user daemon-reload 2>/dev/null || true
      say "PictureHouse service removed. Your library is still in ~/.local/share/picturehouse."
      exit 0 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "unknown option: $arg (try --help)" ;;
  esac
done

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not with sudo: the service runs as you."
[ -f "$HERE/pyproject.toml" ] || die "Can't find the PictureHouse source next to this script ($HERE)."

# 1. FFmpeg
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  say "FFmpeg is needed. Install it, then run this script again:"
  if command -v apt >/dev/null; then echo "  sudo apt install ffmpeg"
  elif command -v dnf >/dev/null; then echo "  sudo dnf install ffmpeg   (from RPM Fusion on Fedora)"
  else echo "  install the 'ffmpeg' package with your package manager"; fi
  exit 1
fi
say "FFmpeg: $(ffmpeg -hide_banner -version | head -n1 | cut -d' ' -f1-3)"

# 2. uv
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  command -v curl >/dev/null || die "curl is needed to install uv: sudo apt install curl"
  say "Installing uv into ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
say "uv: $(uv --version)"

# 3. Python packages (uv fetches Python 3.12 itself if the system has none)
say "Installing PictureHouse's packages into $HERE/.venv ..."
(cd "$HERE" && uv sync --frozen --no-dev)
"$HERE/.venv/bin/picturehouse" config init >/dev/null 2>&1 || true
say "$("$HERE/.venv/bin/picturehouse" --version)"

if [ "$SERVICE" -eq 0 ]; then
  say "Installed. Start it with:  cd \"$HERE\" && uv run picturehouse serve"
  exit 0
fi

# 4. systemd user service
if ! systemctl --user show-environment >/dev/null 2>&1; then
  say "No systemd user session here, so no service was added."
  say "Start it with:  cd \"$HERE\" && uv run picturehouse serve"
  exit 0
fi
mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=PictureHouse media server
Documentation=file://$HERE/INSTALL.md
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$HERE/.venv/bin/picturehouse serve
WorkingDirectory=$HERE
Restart=on-failure
RestartSec=5
# Stop FFmpeg conversions along with the server.
KillMode=control-group
TimeoutStopSec=20

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable picturehouse.service >/dev/null
systemctl --user restart picturehouse.service
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ] \
    && ! loginctl enable-linger "$USER" 2>/dev/null; then
  say "To keep PictureHouse running when you're logged out, run once:  sudo loginctl enable-linger $USER"
fi

for _ in $(seq 1 20); do
  curl -fs http://127.0.0.1:9595/api/hello >/dev/null 2>&1 && break
  sleep 1
done
if curl -fs http://127.0.0.1:9595/api/hello >/dev/null 2>&1; then
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  say "PictureHouse is running:  http://127.0.0.1:9595/   (other devices: http://${ip:-<this-pc>}:9595/)"
  say "Logs:  journalctl --user -u picturehouse -f"
else
  say "The service was added but isn't answering yet. Check:  journalctl --user -u picturehouse -n 50"
fi
