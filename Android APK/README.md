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
| `build_android.py` | Builds the signed release APK and copies it to where the server offers it for download (`src/picturehouse/web/static/downloads/`). Also creates the release key (`--create-key`, once). |
| `signing-certificate.txt` | The SHA-256 fingerprint of the release signing certificate (public). |

## Updating

Install the newer APK the same way; it replaces the app and keeps its
settings. Every release is signed with the same PictureHouse key, which is
what lets Android accept it as an update.

**If Android says "App not installed" or the package conflicts**, the copy
on the phone was signed with a different key (for example a test build).
Uninstall PictureHouse once, then install the new APK. Your library lives on
the server, so nothing is lost.

## Checking an APK is genuine

Official APKs are signed with the certificate "CN=PictureHouse", whose SHA-256
fingerprint is (also in [`signing-certificate.txt`](signing-certificate.txt)):

```
1F:2B:F9:18:B0:68:A0:D8:6C:6E:D2:08:1D:4C:0C:61:2D:88:1D:C8:4A:43:60:44:2E:99:9C:B4:07:19:04:FC
```

To check a download, use `apksigner` from the Android SDK build-tools:

```bash
apksigner verify --print-certs PictureHouse-<version>.apk
```

The `certificate SHA-256 digest` must match that file (ignore the colons and
the letter case).

## Building

You need the Android SDK (Android Studio installs it) and JDK 17 or later.
From the repository's top folder:

```bash
uv run python "Android APK/build_android.py" --debug-key
```

That's a test build, signed with your computer's own debug key. Only the
maintainer's release key makes APKs that update the published app.

The APK goes to `app/build/outputs/apk/release/app-release.apk` and is
copied to where the server offers it for download. Desktop builds made
afterwards (`windows/`, `linux/`) include it, for the Library page.

### Release signing (maintainer)

- **Once:** run `build_android.py --create-key`. It creates the release key
  at `~/.picturehouse/android-release.p12` (outside the repository) with a
  password you choose. **Back up the file and the password** (password
  manager, plus a copy of the file off this computer). Without both, no
  future version can update the app on anyone's phone.
- **Each release:** run `build_android.py`. It asks for the password, or reads
  `PICTUREHOUSE_KEYSTORE_PASSWORD`. Set `PICTUREHOUSE_KEYSTORE` if the key is
  somewhere else, such as a USB stick. The version number comes from
  PictureHouse's version (`pyproject.toml`), so each release is an upgrade.
- After building, the script checks the APK's certificate against
  `signing-certificate.txt` and refuses to finish if it differs.
