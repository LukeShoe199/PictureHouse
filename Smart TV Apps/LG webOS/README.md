# PictureHouse on your LG TV

A step-by-step guide to putting the PictureHouse app on an LG TV (tested on
a 2020 model, which runs **webOS 5**; any LG from 2018 on works the same
way).

**What you get:** a proper TV app you drive with the normal remote:
- films, series and home videos, with posters, Continue watching and a
  full-screen player;
- music and audiobooks, with a visualiser;
- photos, with slideshows that can play music. It plays
most files exactly as they are (HEVC, 4K and HDR included) and only converts
the ones the TV can't handle. Your watch progress is shared with phones and
the web console.

**How it works, in one line:** the app on the TV is a small launcher. It
finds PictureHouse on your network and opens the TV screens that PictureHouse
itself serves. So when PictureHouse is updated on the PC, the TV app updates
too. You rarely need to reinstall it.

> **Why "Developer Mode"?** Apps normally come from the LG Content Store.
> PictureHouse is your own app, so it goes on the TV the way developers test
> theirs: with LG's free **Developer Mode** app. That's the official route,
> with no hacking or rooting. The one catch is in [Part 3](#part-3-keep-it-installed).

**Time needed:** about 20 minutes, once.

---

## Before you start

You need:

- [ ] The TV and the PC on the **same home network**.
- [ ] **PictureHouse running** on the PC (the desktop app is fine).
- [ ] **Node.js** on the PC. To check, open a
      terminal and run `node --version`; any version number means you're set.
      If it isn't installed, get the LTS version from <https://nodejs.org>.
- [ ] A terminal open in the PictureHouse project folder
      (where you cloned or unzipped the source code), with
      [uv](https://docs.astral.sh/uv/) installed (run `uv sync` there once).

---

## Part 1: Set up the TV (once)

### 1. Make a free LG Developer account

1. On the PC, go to <https://webostv.developer.lge.com> and click
   **Sign In** (top right).
2. Click **Create Account** and follow the steps. It's free and just needs an
   email address.

Keep the email and password handy: you type them on the TV next.

### 2. Install the Developer Mode app on the TV

1. On the TV remote, press **Home** (the house button).
2. Open the **LG Content Store** (it may be called **Apps**).
3. Search for **Developer Mode** and install it. It's made by LG.
4. Open **Developer Mode** and sign in with the account from step 1. Type
   carefully: the on-screen keyboard is fiddly.

### 3. Switch Developer Mode on

1. In the Developer Mode app, turn **Dev Mode Status** to **ON**.
2. The TV asks to restart. Let it.
3. When it's back, open the **Developer Mode** app again. You'll now see:
   - **IP Address**, for example `192.168.1.50`. Write it down.
   - **Passphrase**: 6 letters and numbers at the bottom left, for example
     `A1B2C3`. It's case sensitive. Write it down.
   - **Remain Session**: how long Developer Mode lasts (see Part 3).
4. Turn **Key Server** to **ON**.

> **Tip:** give the TV a fixed address in your router (often called
> "DHCP reservation" or "static lease"). Otherwise its IP can change after a
> power cut, and you'd need to redo step 4.

---

## Part 2: Put the app on the TV

### 4. Connect the PC to the TV (once)

In the terminal, run this, replacing the IP with the one your TV showed:

```bash
uv run python "Smart TV Apps/LG webOS/build_webos.py" --setup tv --tv-ip 192.168.1.50
```

- The first run downloads LG's tools, which takes a minute.
- When it asks for the **passphrase**, type the 6 characters from the TV and
  press Enter.
- It finishes by printing some details about the TV (model, webOS version).
  That means it worked.

"tv" is just the name the PC uses for your TV. If you have two TVs, set up
the second as `tv2`.

### 5. Install PictureHouse

```bash
uv run python "Smart TV Apps/LG webOS/build_webos.py" --install tv
```

This builds the app, installs it, and opens it on the TV. Done!

### 6. First launch

The app looks for PictureHouse on your network, which takes about 10 seconds,
then opens it. After that, it remembers the server and opens straight away.

If it can't find the server, it asks you to type the address. Type the PC's
address, for example `192.168.1.20`. (On the PC, PictureHouse shows it under
**Library → Phones & tablets**.)

The app is in the TV's app list. To move it to the front of the bar: press
**Home**, highlight PictureHouse, hold **OK**, and move it.

---

## Using it

| Remote button | What it does |
|---|---|
| Arrows | Move around |
| **OK** | Open / play |
| **Back** | Go back (on the Home screen it closes the app) |
| **OK** while playing | Pause / play |
| **◀ / ▶** while playing | Back 10 seconds / forward 30 seconds |
| **⏪ / ⏩** | Back / forward 1 minute |
| **■** (stop) or **Back** while playing | Stop, keeping your place |
| **Red**, while "Opening PictureHouse…" shows | Choose a different PictureHouse server |

The Magic Remote's pointer works too: point and click.

- **Resume:** films and episodes remember where you stopped, on every
  device. "Continue watching" is the first row on the Home screen.
- **Next episode:** at the end of an episode, the next one starts after 10
  seconds. Press **OK** to start it now, or **Back** to stop.
- **Search:** go to **Search**, press **OK** on the box and type with the TV
  keyboard. Results appear as you type.
- **Music & audiobooks:**
  - Music opens on **Artists**, which follow your folders (Artist → Album →
    songs). Switch to **Albums** or **Songs** with the buttons at the top;
    the TV remembers your choice.
  - Press **Play** (or **Shuffle**) on an album. The music keeps playing
    while you browse: a small visualiser shows in the bottom corner, and the
    **♪** button in the top bar opens **Now playing**.
  - In Now playing: **◀ ▶** move between the buttons (Previous,
    Play/Pause, Next, Repeat, Style) and **OK** presses one. **Back**
    returns to browsing while the music keeps playing. The remote's
    **play/pause** and **⏪ ⏩** keys work anywhere.
  - **Audiobooks** (tracks over 20 minutes) remember your place: **◀ ▶** skip
    30 seconds, **⏪ ⏩** change chapter, and they appear in *Continue
    watching*.
- **Photos:**
  - On **Photos**, press **Slideshow of everything**, or open an album and
    press **Slideshow**. You can also pick a single photo to view it.
  - **OK** shows the control bar: photo back/forward, pause, **In order /
    Shuffled**, and, when music is playing, Previous / Play-Pause / Next /
    **Repeat**. **◀ ▶** move along the bar and **OK** presses a button.
  - With the bar hidden, **◀ ▶** change photo. **Back** hides the bar, and a
    second **Back** closes the slideshow.
  - **Slideshow with music** asks what to play: an album, shuffle all music,
    or keep what's already playing.
- **Films and episodes:** the player shows buttons for −10 s, play/pause,
  +30 s and, for episodes, **Next episode** (also on **channel up**).
- **Changing server:** go to **Settings → Change server** if the PC gets a
  new address (or to use another PictureHouse).
  - Type the address, or choose **Search the network again**.
  - **Keep using…** (or **Back**) returns you to where you were.
  - Settings also shows which server and address you're connected to.

---

## Part 3: Keep it installed

**The catch:** Developer Mode doesn't last forever. Each session runs for a
set time, up to 1000 hours (about 6 weeks). The time left is shown as
**Remain Session** in the Developer Mode app. **If the session runs out, the
TV removes the PictureHouse app.** Developer Mode also switches off if the TV
restarts 10 times without a network connection.

**Extending is easy. Do one of these every few weeks:**

- **On the TV:** open the **Developer Mode** app and press **Extend**.
- **From the PC:**

  ```bash
  uv run python "Smart TV Apps/LG webOS/build_webos.py" --extend tv
  ```

**If it did run out:** open the Developer Mode app, sign in, switch **Dev
Mode Status** on again, then repeat **step 5**. Your progress and library are
on the PC, so nothing is lost.

---

## Troubleshooting

**"No PictureHouse server was found"**
- If the PC's address changed, the app notices on its own: it searches
  again and asks for the address if it can't find it. You can also go to
  **Settings → Change server** at any time.
- Is PictureHouse running on the PC? Is the TV on the same network (not a
  guest Wi-Fi)?
- Type the PC's address by hand when the app asks.
- Windows Firewall must allow PictureHouse. If your TV already sees
  PictureHouse as a media source (DLNA), it's allowed.

**Step 4 says "connection refused" or times out**
- In the Developer Mode app, check that **Dev Mode Status** and **Key
  Server** are both on.
- Check the IP address: it's shown in the Developer Mode app, and it can
  change. Run step 4 again with the new one.

**The passphrase is rejected**
- It's case sensitive. Also check you're typing it from the Developer Mode
  app, not the TV's Wi-Fi password.

**A video won't play, or keeps buffering**
- If the TV can't play a file as it is, the app switches to a converted
  version on its own ("Converting for the TV…").
- **Buffering over Wi-Fi:** on the PC, go to **Library → DLNA → TV bitrate
  limit** and choose 12 Mbps. Big files are then converted to fit your
  Wi-Fi. This setting covers the TV app too. A network cable to the TV is
  the real fix.
- "PictureHouse is already converting 2 videos…" means other people are
  watching converted videos. Wait for one to finish, or raise **Videos
  converted at once** on the same page.

**The app disappeared**
- Developer Mode ran out: see [Part 3](#part-3-keep-it-installed).

**I updated PictureHouse: do I reinstall the app?**
- Usually not. The TV screens come from PictureHouse itself. Only reinstall
  (step 5) if the update notes say the launcher changed.

---

## For developers

- **The launcher** is this folder (`appinfo.json`, `index.html`, `launcher.js`),
  packaged by `build_webos.py` with LG's `@webos-tools/cli`
  (`ares-package`) into `dist/webos/com.picturehouse.tv_<version>_all.ipk`.
  - Discovery: the TV's IP comes from Luna (`com.palm.connectionmanager/
    getStatus`, ACG `network.query`) or WebRTC. It then calls `GET
    /api/hello` (CORS `*`) on port 9595 across that /24.
- **The TV interface** is `src/picturehouse/web/static/tv/`, served at
  `/tv/`. Its constraints:
  - Keep it Chrome 68-compatible (webOS 5): ES2017, no `?.` / `??`, no flex
    `gap`, no `:is()`, no `aspect-ratio`.
  - Playback uses `GET /api/items/{id}/play?client=tv`: the original file
    when the DLNA profile and the TV bitrate limit allow, otherwise HLS
    (which webOS plays natively). A direct-play error falls back to HLS at
    the same position.
- **Debugging on the TV:**
  `npx -p @webos-tools/cli@3.2.6 ares-inspect --device tv --app com.picturehouse.tv --open`
  opens Chrome DevTools for the app.
