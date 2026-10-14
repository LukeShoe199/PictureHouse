"""Build the Android app and put it where the server offers it for download.

    uv run python "Android APK/build_android.py"

Runs Gradle (in this folder) to build a release APK, then copies it to
src/picturehouse/web/static/downloads/PictureHouse.apk. The Library page then
shows "Get the Android app", and the frozen server / desktop installer built
afterwards include it. Needs the Android SDK (local.properties or
ANDROID_HOME) and JDK 17+ (JAVA_HOME).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parent  # the Gradle project is this folder
REPO = ANDROID.parent
APK = ANDROID / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
DEST = REPO / "src" / "picturehouse" / "web" / "static" / "downloads" / "PictureHouse.apk"


def main() -> int:
    if not (ANDROID / "local.properties").exists():
        sdk = os.environ.get("ANDROID_HOME") or str(
            Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"
        )
        (ANDROID / "local.properties").write_text(
            "sdk.dir=" + sdk.replace("\\", "/").replace(":", "\\:") + "\n", encoding="utf-8"
        )
    gradlew = ANDROID / ("gradlew.bat" if os.name == "nt" else "gradlew")
    proc = subprocess.run([str(gradlew), "assembleRelease", "--console=plain", "-q"], cwd=ANDROID)
    if proc.returncode != 0 or not APK.is_file():
        print("Android build failed", file=sys.stderr)
        return proc.returncode or 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APK, DEST)
    print(f"built {DEST} ({DEST.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
