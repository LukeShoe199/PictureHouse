"""Build the fixture media library.

    uv run python scripts/build_fixtures.py [TARGET_DIR] [--force]

Default target is tests/.fixture-media (the same place the tests use).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import fixture_library  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("target", nargs="?", type=Path, default=REPO / "tests" / ".fixture-media")
    ap.add_argument("--force", action="store_true", help="rebuild even if current")
    ns = ap.parse_args()

    entries = fixture_library.build(ns.target, force=ns.force)
    counts = Counter(e.kind for e in entries if not e.skipped)
    print(f"fixture library at {ns.target}")
    print("  " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    for e in entries:
        if e.skipped:
            print(f"  skipped {e.path}: {e.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
