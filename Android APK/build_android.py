"""Build the signed Android app and put it where the server offers it for download.

    uv run python "Android APK/build_android.py" --create-key   # once, ever
    uv run python "Android APK/build_android.py"                # each release
    uv run python "Android APK/build_android.py" --debug-key    # test build only

Signing: release APKs are signed with PictureHouse's release key, a PKCS#12
keystore kept OUTSIDE the repository (default ~/.picturehouse/
android-release.p12, or PICTUREHOUSE_KEYSTORE). Every release must use the
same key, or phones refuse the update. Back the file and its password up.
The password is read from PICTUREHOUSE_KEYSTORE_PASSWORD, or asked for; it
reaches Gradle through the environment, never the command line or a file.

After building, the APK's certificate is checked against
signing-certificate.txt (committed, public), so a build can't silently go out
signed with some other key.

Versions: versionName is PictureHouse's version, versionCode is derived
from it (major*10000 + minor*100 + patch), so every release is an upgrade.

Output: app/build/outputs/apk/release/app-release.apk, copied to
src/picturehouse/web/static/downloads/PictureHouse.apk (the Library page then
shows "Get the Android app"; desktop builds made afterwards include it).
Needs the Android SDK (local.properties or ANDROID_HOME) and JDK 17+.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parent  # the Gradle project is this folder
REPO = ANDROID.parent
APK = ANDROID / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
DEST = REPO / "src" / "picturehouse" / "web" / "static" / "downloads" / "PictureHouse.apk"
CERT_FILE = ANDROID / "signing-certificate.txt"
ALIAS = "picturehouse"
DEFAULT_KEYSTORE = Path.home() / ".picturehouse" / "android-release.p12"


def keystore_path() -> Path:
    return Path(os.environ.get("PICTUREHOUSE_KEYSTORE") or DEFAULT_KEYSTORE)


def version() -> tuple[str, int]:
    sys.path.insert(0, str(REPO / "src"))
    from picturehouse import __version__

    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", __version__)
    if not m:
        sys.exit(f"can't derive an Android versionCode from version {__version__!r}")
    major, minor, patch = map(int, m.groups())
    return __version__, major * 10000 + minor * 100 + patch


def java_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / exe).is_file():
        return str(Path(home) / "bin" / exe)
    return shutil.which(name) or sys.exit(f"{name} not found: install JDK 17+ and set JAVA_HOME")


def sdk_dir() -> Path:
    props = ANDROID / "local.properties"
    if not props.exists():
        sdk = os.environ.get("ANDROID_HOME") or str(
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Android" / "Sdk"
        )
        props.write_text(
            "sdk.dir=" + sdk.replace("\\", "/").replace(":", "\\:") + "\n", encoding="utf-8"
        )
    for line in props.read_text(encoding="utf-8").splitlines():
        if line.startswith("sdk.dir="):
            return Path(line.split("=", 1)[1].replace("\\:", ":"))
    sys.exit("sdk.dir missing from local.properties")


def apksigner() -> str:
    tools = sorted((sdk_dir() / "build-tools").glob("*/apksigner*"), reverse=True)
    if not tools:
        sys.exit("apksigner not found: install Android SDK build-tools")
    return str(tools[0])


def certificate_sha256(apk: Path) -> str:
    out = subprocess.run(
        [apksigner(), "verify", "--print-certs", str(apk)],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"apksigner rejected {apk.name}:\n{out.stderr or out.stdout}")
    m = re.search(r"certificate SHA-256 digest: ([0-9a-f]{64})", out.stdout)
    if not m:
        sys.exit("couldn't read the APK's certificate")
    return m.group(1)


def fingerprint(hex_digest: str) -> str:
    return ":".join(hex_digest[i : i + 2] for i in range(0, 64, 2)).upper()


def password_opens(store: Path, env: dict[str, str]) -> bool:
    """Check the password with keytool before Gradle, whose failure is vague.
    keytool reads it from the environment (-storepass:env), never argv."""
    out = subprocess.run(
        [java_tool("keytool"), "-list", "-keystore", str(store), "-storetype", "PKCS12",
         "-alias", ALIAS, "-storepass:env", "PICTUREHOUSE_KEYSTORE_PASSWORD"],
        capture_output=True, text=True, env=env,
    )  # fmt: skip
    if out.returncode == 0:
        return True
    reason = (out.stdout + out.stderr).strip().splitlines()
    print(f"  keytool: {reason[-1] if reason else 'failed'}")
    return False


def unlock(store: Path, env: dict[str, str]) -> bool:
    if env.get("PICTUREHOUSE_KEYSTORE_PASSWORD"):
        if password_opens(store, env):
            return True
        print(f"PICTUREHOUSE_KEYSTORE_PASSWORD doesn't open {store}.")
        return False
    for attempt in range(3):
        env["PICTUREHOUSE_KEYSTORE_PASSWORD"] = getpass.getpass(f"Password for {store.name}: ")
        if password_opens(store, env):
            print("Password OK.")
            return True
        print(f"That password doesn't open {store} ({2 - attempt} tries left).")
    print(
        "If this key was just created and nothing has been published with it, you can\n"
        "delete it and run --create-key again with a password you've saved first."
    )
    return False


def create_key() -> int:
    store = keystore_path()
    if store.exists():
        sys.exit(f"{store} already exists: one key for all releases, so it isn't replaced")
    store.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating the PictureHouse release key in {store}")
    print("keytool asks for a password (6+ characters, twice). Choose a strong one")
    print("and save it in your password manager: it can't be recovered.\n")
    rc = subprocess.call([
        java_tool("keytool"), "-genkeypair",
        "-keystore", str(store), "-storetype", "PKCS12",
        "-alias", ALIAS, "-keyalg", "RSA", "-keysize", "4096",
        "-validity", "10000",  # ~27 years: Google Play wants beyond 2033
        "-dname", "CN=PictureHouse, O=PictureHouse",
    ])  # fmt: skip
    if rc != 0:
        return rc
    print(f"\nCreated {store}.")
    print("BACK IT UP NOW (with its password), somewhere other than this PC.")
    print("Without both, new versions can't update the app on anyone's phone.")
    print("Next: build once; the build records the certificate's fingerprint in")
    print(f"{CERT_FILE.relative_to(REPO)} (public; commit it).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--create-key", action="store_true", help="create the release key (once)")
    ap.add_argument("--debug-key", action="store_true", help="sign with this PC's debug key")
    a = ap.parse_args()
    if a.create_key:
        return create_key()

    name, code = version()
    env = {**os.environ}
    store = keystore_path()
    if a.debug_key:
        print("Signing with this PC's DEBUG key: for testing only, don't publish this APK.")
        for var in ("PICTUREHOUSE_KEYSTORE", "PICTUREHOUSE_KEYSTORE_PASSWORD"):
            env.pop(var, None)
    else:
        if not store.is_file():
            sys.exit(
                f"No release key at {store}.\n"
                "Create it once with --create-key (or restore your backup there, or set\n"
                "PICTUREHOUSE_KEYSTORE). For a test build, use --debug-key."
            )
        env["PICTUREHOUSE_KEYSTORE"] = str(store)
        if not unlock(store, env):
            return 1

    sdk_dir()  # writes local.properties if missing
    gradlew = ANDROID / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if APK.exists():
        APK.unlink()
    proc = subprocess.run(
        # --no-daemon: a background Gradle daemon keeps the key file open and
        # caches the key it first read, so a new key/password would be ignored.
        [str(gradlew), "assembleRelease", "--console=plain", "-q", "--no-daemon",
         f"-PphVersionName={name}", f"-PphVersionCode={code}"],
        cwd=ANDROID,
        env=env,
    )  # fmt: skip
    if proc.returncode != 0 or not APK.is_file():
        print("Android build failed", file=sys.stderr)
        return proc.returncode or 1

    cert = fingerprint(certificate_sha256(APK))
    if not a.debug_key:
        expected = CERT_FILE.read_text(encoding="utf-8").strip() if CERT_FILE.exists() else ""
        if not expected:
            CERT_FILE.write_text(cert + "\n", encoding="utf-8")
            print(f"recorded the release certificate in {CERT_FILE.relative_to(REPO)}")
        elif expected != cert:
            APK.unlink()
            sys.exit(
                f"This APK was signed with a DIFFERENT key than earlier releases:\n"
                f"  this build: {cert}\n  releases:   {expected}\n"
                "Phones would refuse it as an update. Restore the original key."
            )
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APK, DEST)
    kind = "DEBUG key" if a.debug_key else "release key"
    print(f"built {DEST} ({DEST.stat().st_size // 1024} KB), version {name} ({code})")
    print(f"signed with the {kind}, SHA-256 {cert}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
