# PictureHouse server in a container (ZimaOS, other NAS systems, any Linux).
#
#   docker build -t picturehouse .
#   docker run -d --name picturehouse --network host \
#     -v /path/to/appdata:/data -v /path/to/media:/media:ro,rslave picturehouse
#
# --network host matters: TVs find the server by DLNA (SSDP multicast on UDP
# 1900), which Docker's default bridge network doesn't pass. See
# zimaos/ for the ZimaOS app and its guide.

# ---- build: the Python environment (uv stays in this stage) ----
FROM python:3.12-slim-trixie AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
WORKDIR /app
# Dependencies first: they change rarely, so this layer is reused between builds.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---- run ----
FROM python:3.12-slim-trixie
LABEL org.opencontainers.image.source="https://github.com/LukeShoe199/PictureHouse" \
      org.opencontainers.image.title="PictureHouse" \
      org.opencontainers.image.description="Home media server: films, TV, music and photos to TVs, phones and browsers."
ENV PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:/usr/lib/jellyfin-ffmpeg:$PATH \
    PICTUREHOUSE_DATA_DIR=/data \
    PICTUREHOUSE_MEDIA_HINT=/media
# FFmpeg: Jellyfin's build, because it brings Intel's video driver (Quick Sync
# through VAAPI): a NAS's processor can't convert HD video in real time, its
# GPU can (streaming/hwaccel.py; the container needs /dev/dri, see
# zimaos/docker-compose.yml). Without a GPU it works as plain FFmpeg.
# Pinned by version and checksum; bump both together.
ARG JELLYFIN_FFMPEG=8.1.3-1
ARG JELLYFIN_FFMPEG_SHA256=fbef9f81a53e175194e3f67832618a86111f7bd37197df21a5d748c34289f9c0
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL -o /tmp/ffmpeg.deb \
      "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v${JELLYFIN_FFMPEG}/jellyfin-ffmpeg8_${JELLYFIN_FFMPEG}-trixie_amd64.deb" \
 && echo "${JELLYFIN_FFMPEG_SHA256}  /tmp/ffmpeg.deb" | sha256sum -c - \
 && apt-get install -y --no-install-recommends /tmp/ffmpeg.deb \
 && rm /tmp/ffmpeg.deb \
 && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv

VOLUME /data
# Web console + API, HTTPS (phones, casting), DLNA discovery.
EXPOSE 9595 9596 1900/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9595/api/hello', timeout=4)"

ENTRYPOINT ["picturehouse"]
CMD ["serve"]
