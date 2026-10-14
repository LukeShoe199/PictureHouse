# Build environment for the Linux desktop app (used by linux/build_linux.py).
# Ubuntu 22.04 so the result also runs on older distributions (glibc 2.35+):
# Ubuntu 22.04+, Kubuntu, Linux Mint 21+, Debian 12+, Fedora 36+.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/root/.cargo/bin:/opt/node/bin:$PATH \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    # linuxdeploy (AppImage bundler) is itself an AppImage; Docker has no FUSE.
    APPIMAGE_EXTRACT_AND_RUN=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl wget file ca-certificates git pkg-config xz-utils \
      libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
      libxdo-dev libssl-dev xdg-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.98.1
RUN mkdir -p /opt/node \
    && curl -fsSL https://nodejs.org/dist/v24.13.1/node-v24.13.1-linux-x64.tar.xz \
       | tar -xJ -C /opt/node --strip-components=1
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv

WORKDIR /work
