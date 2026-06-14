#!/usr/bin/env python3
"""Skill: place-faces — reshape a theme's reviewed raw images to 768×768 and place them as variants.

For a theme <T>, takes the reviewed ``viewer/faces/raw/<T>/<emotion>[_low|_high].png`` files,
center-crops each to a square, resizes to 768×768 (macOS ``sips`` — no deps), and writes them into
the v0.11 theme layout as variants:

    raw/<T>/<emotion>.png       -> faces/<T>/<emotion>/01.png   (mid — the canonical look)
    raw/<T>/<emotion>_low.png   -> faces/<T>/<emotion>/02.png
    raw/<T>/<emotion>_high.png  -> faces/<T>/<emotion>/03.png

The reference ``<T>.png`` is never touched. Missing bands are simply skipped (the viewer accepts any
number of variants). Idempotent — overwrites by default; ``--keep`` skips variant files that exist.

Usage:
    python3 place.py <theme> [--keep]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../place-faces -> skills -> .claude -> repo root
FACES = ROOT / "viewer" / "faces"
SIZE = 768  # the v0.11 theme variant size (matches the existing finished packs)

EMOTIONS = ["calm", "joy", "playful", "tender", "thoughtful", "serious", "surprise", "doubt", "sad"]
# raw suffix -> variant filename; mid first so 01 is the canonical look the viewer falls back to.
BANDS = [("", "01.png"), ("_low", "02.png"), ("_high", "03.png")]


def dims(p: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout
    w = h = 0
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            w = int(line.split(":")[1])
        elif line.startswith("pixelHeight:"):
            h = int(line.split(":")[1])
    return w, h


def reshape(src: Path, dst: Path) -> None:
    """Center-crop to a square, resize to SIZE×SIZE, write to dst (src untouched)."""
    w, h = dims(src)
    side = min(w, h)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # sips does NOT chain crop + resize reliably in one invocation — do it in two steps.
    tmp = dst.with_suffix(".crop.tmp.png")
    try:
        subprocess.run(
            ["sips", "-c", str(side), str(side), str(src), "--out", str(tmp)],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["sips", "-z", str(SIZE), str(SIZE), str(tmp), "--out", str(dst)],
            capture_output=True, text=True, check=True,
        )
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Reshape + place a theme's reviewed raw faces into the viewer.")
    ap.add_argument("theme", help="theme name, e.g. drowning")
    ap.add_argument("--keep", action="store_true", help="skip variant files that already exist")
    args = ap.parse_args()

    if sys.platform != "darwin":
        sys.exit("ERROR: this skill uses macOS `sips`. On another OS, swap reshape() for Pillow.")

    raw = FACES / "raw" / args.theme
    dest = FACES / args.theme
    if not raw.is_dir():
        sys.exit(f"ERROR: raw folder not found: {raw}")

    print(f"Theme: {args.theme}  |  {raw}  ->  {dest}  ({SIZE}×{SIZE})\n")
    placed = kept = without = 0
    for emo in EMOTIONS:
        emo_dir = dest / emo
        wrote_any = False
        for suffix, variant in BANDS:
            src = raw / f"{emo}{suffix}.png"
            if not src.exists():
                continue
            dst = emo_dir / variant
            if dst.exists() and args.keep:
                print(f"  keep   {emo}/{variant} (exists)")
                kept += 1
                wrote_any = True
                continue
            try:
                reshape(src, dst)
                print(f"  place  {emo}/{variant}  <- {src.name}")
                placed += 1
                wrote_any = True
            except subprocess.CalledProcessError as e:
                print(f"  FAIL   {emo}/{variant}: {(e.stderr or '').strip()[:160]}")
        if wrote_any:
            gk = emo_dir / ".gitkeep"  # the folder has real art now
            if gk.exists():
                gk.unlink()
        else:
            print(f"  --     {emo}: no raw images, left as-is")
            without += 1

    print(f"\nDone. placed={placed} kept={kept} emotions_without_raw={without}")
    print(f"Theme folder: {dest}")
    if without:
        print("Tip: generate the missing emotions first with  /generate-faces " + args.theme)


if __name__ == "__main__":
    main()
