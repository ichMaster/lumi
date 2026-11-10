"""v2.9 (LUMI-245) — write ``settings.example.yaml`` + ``secrets.example.yaml`` from the settings schema.

The example files are generated, never edited by hand: every setting of ``core/settings.py`` ``SETTINGS``, by group,
commented out at its default with its description, its allowed values and its env name.

Usage:
    uv run python scripts/settings_examples.py           # (re)write both files at the repo root
    uv run python scripts/settings_examples.py --check   # exit 1 when a committed file is stale

The schema contract test (``tests/contract/test_settings_schema.py``) runs the same comparison as ``--check``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # repo root on the path

from core.settings import render_example  # noqa: E402

FILES = {"settings.example.yaml": False, "secrets.example.yaml": True}


def stale(root: Path = ROOT) -> list[str]:
    """The example files whose content differs from what the schema renders (missing counts as stale)."""
    out = []
    for name, secret in FILES.items():
        path = root / name
        if not path.exists() or path.read_text(encoding="utf-8") != render_example(secret=secret):
            out.append(name)
    return out


def write(root: Path = ROOT) -> list[str]:
    """(Re)write both example files; returns the names written."""
    for name, secret in FILES.items():
        (root / name).write_text(render_example(secret=secret), encoding="utf-8")
    return list(FILES)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the settings example files from the schema.")
    ap.add_argument("--check", action="store_true", help="exit 1 when an example file is stale (writes nothing)")
    args = ap.parse_args(argv)
    if args.check:
        bad = stale()
        if bad:
            print("stale (run `uv run python scripts/settings_examples.py`): " + ", ".join(bad), file=sys.stderr)
            return 1
        print("settings examples are up to date")
        return 0
    for name in write():
        print(f"wrote {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
