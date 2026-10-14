"""Linux: FFmpeg and the server must live exactly as long as their owner.

PR_SET_PDEATHSIG is tied to the *thread* that started a child. HLS starts
FFmpeg from anyio worker threads, which exit when idle, so conversions were
killed mid-film; the desktop app's server was stopped seconds after start.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from picturehouse.streaming import procs

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Job Objects cover Windows")


def test_child_started_from_a_short_lived_thread_outlives_that_thread():
    started: dict[str, subprocess.Popen] = {}
    t = threading.Thread(target=lambda: started.setdefault("p", procs.popen(["sleep", "30"])))
    t.start()
    t.join()  # the worker thread is gone, as anyio's idle workers go
    time.sleep(0.5)
    p = started["p"]
    try:
        assert p.poll() is None, f"child was killed with its thread (rc={p.returncode})"
    finally:
        p.kill()
        p.wait()


def test_server_stops_when_the_app_that_started_it_goes_away(tmp_path):
    # app (exits at once) -> server (watches for that) ; we can't wait() on it
    server = (
        "import time; from picturehouse.streaming import procs;"
        "import os; procs.exit_with_parent(int(os.environ['PARENT']), poll=0.1);"
        "time.sleep(30)"
    )
    app = (
        "import os, subprocess, sys;"
        f"p = subprocess.Popen([sys.executable, '-c', {server!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,"
        " env={**os.environ, 'PARENT': str(os.getpid())});"
        "print(p.pid, flush=True)"
    )
    out = subprocess.run([sys.executable, "-c", app], capture_output=True, text=True, timeout=30)
    pid = int(out.stdout.strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().split(") ")[1].startswith("Z"):
                    break  # exited, not yet reaped
        except FileNotFoundError:
            break
        time.sleep(0.1)
    else:
        subprocess.run(["kill", "-9", str(pid)])
        pytest.fail("the server kept running after its app went away")
