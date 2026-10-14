# PictureHouse for Android

A small app for Android phones and tablets (Android 8.0 or later). It finds
PictureHouse on your Wi-Fi, or takes its address, and shows the PictureHouse
console full screen. Videos can also be handed to another app, such as VLC,
which can cast them to a Chromecast. The screen stays on while something
plays.

## Install it

The app isn't in the Play Store; you install the APK file directly:

1. On the phone, get **`PictureHouse-<version>.apk`**, either:
   - from the [Releases page](https://github.com/LukeShoe199/PictureHouse/releases), or
   - from PictureHouse itself: open `http://<server-address>:9595/` in the
     phone's browser, go to **Library**, and press **Get the Android app**
     (shown when the server includes the APK).
2. Open the downloaded file. Android asks to allow installs from your browser
   ("Install unknown apps"). Allow it, then press **Install**.
3. Open **PictureHouse**. It searches the Wi-Fi and connects; if it can't
   find the server, type its address (e.g. `192.168.1.20:9595`).

Without the app, the console also works in the phone's browser. Use **Add
to Home screen** for an app-like icon.

## What's in this folder

A standard Gradle project (Kotlin, no external libraries) plus:

| File | What it's for |
|---|---|
| `build_android.py` | Builds the release APK and copies it to where the server offers it for download (`src/picturehouse/web/static/downloads/`). |

## Building

You need the Android SDK (Android Studio installs it) and JDK 17 or later.
From the repository's top folder:

```bash
uv run python "Android APK/build_android.py"
```

The APK is at `Android APK/app/build/outputs/apk/release/app-release.apk`.
Desktop builds made afterwards (`windows/`, `linux/`) include it, so the
server can offer it on the Library page.

**Signing:** release builds are signed with the building computer's Android
*debug* key. That's fine for installing it yourself. An update installs over
the top only when it's signed by the same computer. Before distributing it
widely, create a release keystore and set it as `signingConfig` in
`app/build.gradle.kts`, kept out of git.
