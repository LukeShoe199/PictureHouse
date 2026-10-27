# How to install and run PictureHouse

PictureHouse is a home media server. It organises your films, TV series,
music, audiobooks and photos from your own folders, and plays them on TVs
(DLNA and the LG TV app), phones, tablets and browsers. It converts files on
the fly when a device can't play them as they are.

Install it on **one** computer that's on when you want to watch: a Windows
PC, a Linux PC or server, or a ZimaOS box. Everything else in the house
connects to it.

| Platform | Quickest way | Section |
|---|---|---|
| Windows 10 / 11 | The installer, `PictureHouse-<version>-setup.exe` | [Windows](#windows) |
| Ubuntu, Kubuntu, Debian, Mint | The desktop app, `PictureHouse_<version>_amd64.deb` | [Linux](#linux-ubuntu-kubuntu) |
| ZimaOS (ZimaBoard, ZimaBlade, ZimaCube) | Install Custom App in the dashboard | [ZimaOS](#zimaos) |

All downloads are on the
[Releases page](https://github.com/LukeShoe199/PictureHouse/releases).
Each platform's files are also in this repository's
[`windows/`](windows), [`linux/`](linux) and [`zimaos/`](zimaos) folders.
When it's running, carry on with [First-time setup](#first-time-setup).
That part is the same everywhere.

---

## Windows

You need Windows 10 or 11 (64-bit). There are three ways to install it.
Pick one.

### Option A: the installer (recommended)

1. Download **`PictureHouse-<version>-setup.exe`** from the Releases page and
   run it.
2. If Windows shows **"Windows protected your PC"**, press **More info → Run
   anyway**. It appears because the installer isn't code-signed.
3. Follow the steps. No admin rights are needed: it installs just for you, in
   `%LOCALAPPDATA%\Programs\PictureHouse`.
   - If the page **"FFmpeg was not found on this PC"** appears, leave
     **Install FFmpeg with winget** ticked. A black window opens and asks
     you to agree to the terms (type `Y`, Enter). FFmpeg is the free tool
     PictureHouse uses to read and convert video and music.
4. Leave **Start PictureHouse now** ticked and press **Finish**.
5. The first time, **Windows Firewall** asks about `picturehouse-server`.
   Tick **Private networks** and press **Allow**. Without this, your TV and
   phone can't reach it.

PictureHouse opens in its own window, with an icon in the system tray (by
the clock). From the tray icon's menu:
- **Start when I sign in**: start PictureHouse each time you sign in.
- **Keep serving when the window is closed**: turn this on so TVs and phones
  can still play after you close the window.

**Updating:** run the newer setup file. It replaces the old version and
keeps your library.
**Uninstalling:** Settings → Apps → PictureHouse → Uninstall. Your library,
settings and artwork stay in `%LOCALAPPDATA%\PictureHouse`. Delete that
folder too if you won't use PictureHouse again.

### Option B: the portable zip

For trying it out without installing, or running it from another drive.

1. Download **`PictureHouse-<version>-windows-portable.zip`** and unzip it
   anywhere (keep `PictureHouse.exe` and the `server` folder together).
2. Install FFmpeg once, in PowerShell or Command Prompt:
   ```powershell
   winget install --id Gyan.FFmpeg --exact
   ```
3. Double-click **`PictureHouse.exe`**. Allow it through Windows Firewall
   (Private networks), as in Option A, step 5.

The library is still stored in `%LOCALAPPDATA%\PictureHouse`, so the zip and
the installer share it. Only the program is portable, not the library.

### Option C: from the source code

For developers, or to run the latest unreleased code. You'll use the web
console in your browser instead of the PictureHouse window.

1. Install the tools once, in PowerShell:
   ```powershell
   winget install --id Git.Git --exact
   winget install --id astral-sh.uv --exact
   winget install --id Gyan.FFmpeg --exact
   ```
   Close and reopen PowerShell so it finds them.
2. Get the code and install PictureHouse's Python packages:
   ```powershell
   git clone https://github.com/LukeShoe199/PictureHouse.git
   cd PictureHouse
   uv sync
   ```
   uv downloads the right Python by itself, if needed.
3. Start the server: double-click **`windows\run-server.bat`**, or run
   `uv run picturehouse serve`. Keep that window open while you use it.
4. Open **http://127.0.0.1:9595/** in your browser.

To update: `git pull`, then `uv sync`.

**Building the installer and zip yourself** additionally needs
[Rust](https://rustup.rs), [Node.js](https://nodejs.org) and
[Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`). Then run:

```powershell
uv run python windows/build_windows.py
```

The installer and zip appear in `dist\windows\`.

---

## Linux (Ubuntu, Kubuntu)

There are three ways to install. Pick one:

- **A. The desktop app.** Best for a PC you use (Kubuntu, Ubuntu, Mint): the
  same window and tray icon as on Windows.
- **B. The background service.** Best for a server or a PC without a screen:
  it starts with the computer, even when nobody logs in.
- **C. Docker.** If the machine already runs Docker.

Don't run A and B on the same machine; they'd compete for the same port.

### Option A: the desktop app

1. Install FFmpeg:
   ```bash
   sudo apt install ffmpeg
   ```
2. Download **`PictureHouse_<version>_amd64.deb`** from the Releases page
   and install it. Double-click it (Kubuntu opens Discover, Ubuntu opens App
   Center), or run:
   ```bash
   sudo apt install ./PictureHouse_<version>_amd64.deb
   ```
   **On another distribution** (Fedora, Arch...), use
   **`PictureHouse_<version>_amd64.AppImage`** instead. Make it executable
   (`chmod +x PictureHouse_*.AppImage`, or Properties → Permissions →
   "Is executable") and double-click it. Install FFmpeg with your package
   manager.
3. Open **PictureHouse** from the app menu. It works like the Windows app:
   the window is the console, and the tray icon's menu has **Start when I
   sign in** and **Keep serving when the window is closed**. (KDE/Kubuntu
   shows the tray icon as is. On GNOME it needs the AppIndicator extension,
   which Ubuntu includes.)

**Updating:** install the newer `.deb` the same way. **Uninstalling:**
`sudo apt remove picture-house`, or remove it in Discover / App Center. Your
library stays in `~/.local/share/picturehouse`.

### Option B: the background service

1. Install FFmpeg, Git and curl:
   ```bash
   sudo apt install ffmpeg git curl
   ```
2. Get the code (into your home folder, say):
   ```bash
   git clone https://github.com/LukeShoe199/PictureHouse.git ~/PictureHouse
   ```
   Or download the **Source code** zip from the Releases page and unzip it
   there.
3. Run the install script as yourself, **not** with `sudo`:
   ```bash
   cd ~/PictureHouse
   ./linux/install.sh
   ```
   It installs [uv](https://docs.astral.sh/uv/) and PictureHouse's packages
   inside that folder, adds the service and starts it. At the end it prints
   the address to open, e.g. `http://192.168.1.20:9595/`.
4. If it says to run `sudo loginctl enable-linger <you>`, do that once. This
   keeps PictureHouse running while you're logged out.

**On a server without a screen**, nobody browses from the machine itself.
Set an admin PIN, which you'll enter on another device to add folders:

```bash
~/PictureHouse/.venv/bin/picturehouse admin-pin 2468
```

Use your own 4 to 12 digits.

Everyday commands:

```bash
systemctl --user status picturehouse              # is it running?
systemctl --user restart picturehouse
journalctl --user -u picturehouse -f              # the log, live
cd ~/PictureHouse && git pull && ./linux/install.sh   # update
./linux/install.sh --uninstall                    # remove the service
```

Your library lives in `~/.local/share/picturehouse`. Uninstalling keeps it.

### Option C: Docker

Use the same image as ZimaOS. Copy `zimaos/docker-compose.yml` and edit it:
- set your admin PIN;
- change the `/DATA` volume to your media folder (e.g. `/home/you/Media`);
- change `/DATA/AppData/picturehouse` to a folder you back up.

Then run `docker compose up -d`. To update later: `docker compose pull && docker compose up -d`.

### Firewall (all options)

Ubuntu's firewall is off by default. If you've turned it on
(`sudo ufw status` says *active*), allow PictureHouse in:

```bash
sudo ufw allow 9595:9596/tcp
sudo ufw allow 1900/udp
```

---

## ZimaOS

This was tested on ZimaOS 1.7.1. It works on ZimaBoard, ZimaBlade, ZimaCube,
or any PC running ZimaOS. You don't need a terminal. ZimaOS downloads
PictureHouse from GitHub like any App Store app.

1. On the computer you use for the ZimaOS dashboard, download
   **`docker-compose.yml`** from the Releases page (it's also in this
   repository's [`zimaos/`](zimaos) folder).
2. In the dashboard, open **App Store**, then **Install Custom App** (top
   right of **My Apps**).
3. Press **Import** (top right) and choose `docker-compose.yml`. The form
   fills itself in.
4. Scroll to **Environment** and fill in **PICTUREHOUSE_ADMIN_PIN**: your
   own 4 to 12 digits. It's blank on purpose. Without a PIN, nothing can
   add folders. Check **TZ** (time zone), e.g. `Europe/London`.
5. Leave **Network type** on **host**. TVs find PictureHouse through it.
6. Press **Install**. It downloads (about 190 MB) and then shows
   **Running** in My Apps. If it says **Stopped** at 100 %, refresh the page.
7. Click PictureHouse in My Apps, or open `http://<zimaos-address>:9595/`.

Good to know:

- **Finding your media folders:** use **Browse…** in the Add folder form.
  It shows the box's storage as PictureHouse sees it. Note that the names
  ZimaOS's **Files** app shows aren't the real paths: the main drive
  ("ZimaOS-HD") is `/DATA`, so its films are typically `/DATA/Media/Movies`,
  and an external drive called "WD_SSD" is `/media/WD_SSD`.
- **USB drives** can be plugged in while PictureHouse runs: its folders come
  online within about 20 seconds, and show as offline when unplugged
  (nothing is removed from the library).
- **ZimaOS has its own simple DLNA server** (Settings → General → Developer
  mode → DLNA). It's off unless you turned it on. Leave it off, or your TV
  lists two servers.
- **Your files are mounted read-only.** PictureHouse doesn't write to them
  anyway, unless you turn on "Save a copy next to the files" (Library →
  Online metadata). To allow that, delete the two `read_only: true` lines
  from `docker-compose.yml` before importing.
- **The library** (database, artwork, settings) is kept in
  `/DATA/AppData/picturehouse`. Back that folder up.
- **Converting video uses the CPU.** ZimaBoard and ZimaBlade play most files
  directly with ease, but converting 4K, or several videos at once, is too
  much for them. Keep **Library → DLNA → Videos converted at once** at 1.
- **Updating:** the app follows PictureHouse's `latest` release, so when a
  new version is out, ZimaOS offers it under **My Apps → Update**. Your
  library is kept (it lives in `/DATA/AppData/picturehouse`).
  - If a release says its **app file changed**, copy the new
    `docker-compose.yml`, then in **My Apps** open the running PictureHouse's
    **⋮** menu → **Settings** → **YAML**, replace everything with it, put
    your admin PIN back in, and **Save**.
  - Don't use *Install Custom App* for an update: that installs a second
    copy, which fails because the first is running.

**Optional: the command line.** In Settings → General → Developer mode,
turn on **SSH access**, then open **Web-based terminal**. Log in with your
dashboard account: type the username, press Enter, then the password. Then
you can run:

- `sudo docker logs picturehouse`: the server log.
- `sudo docker exec picturehouse picturehouse admin-pin 13579`: set a new
  PIN.

ZimaOS can't install from an image *file*. Its installer always downloads
the image, and gives up if it can't, even when the image is already on the
box. That's why PictureHouse is published to GitHub's registry
(`ghcr.io/lukeshoe199/picturehouse`).

---

## First-time setup

Do this once, on whichever computer runs PictureHouse.

1. **Open the console.** This is the PictureHouse window on Windows, or
   `http://<that-computer>:9595/` in any browser on your network.
2. **Unlock changes, if asked.** On the computer running PictureHouse you can
   change everything. From any other device (and always on ZimaOS), go to
   **Library**, enter the admin PIN and press **Unlock**. To set or change
   the PIN on Windows or a Linux desktop, use Library → Admin access on that
   computer.
3. **Add your folders.** Library → **Add folder**: press **Browse…**, find
   the folder and press **Use this folder**, or type its path, for example
   `D:\Media\Films`, `/home/you/Media/Films` or `/DATA/Media/Movies`. Then
   choose what it holds: Films, TV series, Music, Home videos, Photos or
   Mixed. Add one for each kind of media. Folders on USB drives work too;
   they show as offline while the drive is unplugged.
4. **Posters and plot summaries (optional, free).** Create a free account at
   [themoviedb.org](https://www.themoviedb.org/signup), then under your
   account's **Settings → API** request an API key. Paste it in Library →
   **Online metadata** and save. Without a key, titles come from file names
   and any `.nfo` files.
5. **Scan.** Press **Scan all folders**. New and changed files are then
   picked up automatically.

**Watching:**

- **TVs** (DLNA): PictureHouse appears as **PictureHouse** in the TV's media
  player or input list, e.g. "Media Player", "Plex/DLNA" or "Home network".
- **LG TVs**: the PictureHouse TV app has a much nicer interface; see
  [Smart TV Apps/LG webOS](Smart%20TV%20Apps/LG%20webOS).
- **Android phones and tablets:** install the app, `PictureHouse-<version>.apk`
  from the Releases page; see [Android APK](Android%20APK).
- **Other phones, tablets and computers:** open `http://<that-computer>:9595/`
  in the browser. On phones, "Add to Home screen" makes it an app. Casting
  to a Chromecast works from there.
- **Older iPhones, iPads and browsers** (e.g. the first iPad mini on iOS 9):
  the same address opens **PictureHouse Lite** instead, a simpler version
  made of plain pages: browse films, TV, music and photos, and tap to play.
  It works over plain `http://` with no certificate, and remembers where you
  were like everything else. Any device can open it at
  `http://<that-computer>:9595/lite/`; *Full version* at the bottom of a Lite
  page switches that device back to the full console.

**If something doesn't work:**

- **The TV doesn't see PictureHouse.** The TV and the server must be on the
  same network (not a guest Wi-Fi). Check the firewall step for your
  platform.
- **"FFmpeg not found"** on the console's status page. Install FFmpeg (see
  your platform's section) and restart PictureHouse.
- **A file won't play.** The Library page lists files that couldn't be
  read, and why.
