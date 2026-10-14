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
    PATH=/app/.venv/bin:$PATH \
    PICTUREHOUSE_DATA_DIR=/data \
    PICTUREHOUSE_MEDIA_HINT=/media
# FFmpeg 7.1, a self-contained static build: Debian's package would pull in
# ~450 MB of graphics libraries (LLVM, Mesa) a headless server never uses.
COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /ffprobe /usr/local/bin/
COPY --from=build /app/.venv /app/.venv

VOLUME /data
# Web console + API, HTTPS (phones, casting), DLNA discovery.
EXPOSE 9595 9596 1900/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9595/api/hello', timeout=4)"

ENTRYPOINT ["picturehouse"]
CMD ["serve"]
