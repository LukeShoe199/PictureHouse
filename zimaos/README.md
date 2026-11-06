# PictureHouse for ZimaOS

**To install:** in the ZimaOS dashboard, go to **App Store → Install Custom
App → Import**. Choose `docker-compose.yml` from this folder (also on the
[Releases page](https://github.com/LukeShoe199/PictureHouse/releases)), set
your admin PIN, and press **Install**. Full steps are in
[INSTALL.md → ZimaOS](../INSTALL.md#zimaos).

## What's in this folder

| File | What it's for |
|---|---|
| `docker-compose.yml` | The app definition ZimaOS imports. It also works with plain Docker Compose on any Linux: `docker compose up -d`, after editing the folder paths. |
| `build_docker.py` | Builds the image, and with `--push` publishes it to `ghcr.io/lukeshoe199/picturehouse`. |

The image itself is built from [`../Dockerfile`](../Dockerfile). It contains
the same server as every other platform, with FFmpeg included, and Intel's
video driver: the app file passes the box's GPU in (`devices: /dev/dri`) so
video is converted in hardware. On a machine without `/dev/dri`, remove
those two lines (the container won't start otherwise).

## Publishing a new version

```bash
docker login ghcr.io -u lukeshoe199     # once, with a token that has write:packages
uv run python zimaos/build_docker.py --push
```

Bump the version in `pyproject.toml` first. `--push` publishes both
`:<version>` and `:latest`. The app file uses `:latest`, so installed copies
pick the release up as an update without a new app file. Only when
`docker-compose.yml` itself changes (volumes, environment) do users need to
paste the new file into the app's Settings → YAML.

ZimaOS's installer always downloads the image, and it fails if the download
fails, even when the image is already on the box. So a new version has to be
pushed before ZimaOS can install it.

## Licences

PictureHouse is MIT-licensed (see [`../LICENSE`](../LICENSE)). The image also
contains [Jellyfin's build of FFmpeg](https://github.com/jellyfin/jellyfin-ffmpeg)
8.1.3 (with Intel's media driver), which is licensed under the GPL. Its
source code and build recipe are in that repository; FFmpeg's own is at
[ffmpeg.org](https://ffmpeg.org/download.html#releases). The Windows and Linux downloads don't include
FFmpeg; they use the copy installed on the computer.
