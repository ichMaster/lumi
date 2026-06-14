---
name: place-faces
description: Reshape a theme's reviewed raw face images to 768×768 and file them into the v0.11 viewer layout faces/<theme>/<emotion>/01.png (mid), 02.png (low), 03.png (high).
---

# Skill: Place Theme Faces (reshape + file by emotion)

Take the **reviewed** raw images in `viewer/faces/raw/<theme>/`, center-crop each to a square, resize
to **768×768** (macOS `sips`, no deps), and file them as variants in the v0.11 theme layout:

```
raw/<theme>/<emotion>.png       -> faces/<theme>/<emotion>/01.png   (mid — the canonical look)
raw/<theme>/<emotion>_low.png   -> faces/<theme>/<emotion>/02.png
raw/<theme>/<emotion>_high.png  -> faces/<theme>/<emotion>/03.png
```

The three intensity files become the three **variants** the viewer randomly picks among (v0.11 theme
packs use variants, not intensity). The reference `<theme>.png` is **never** touched. Missing bands are
simply skipped (the viewer accepts any number of variants).

This is step 2 of 2: **`/generate-faces <theme>` → review → place (this)**.

## Usage

```
/place-faces <theme> [--keep]
```

- `/place-faces drowning` — reshape + file every emotion that has raw images (overwrites existing
  variants; idempotent).
- `/place-faces drowning --keep` — skip variant files that already exist (don't overwrite).

## Prerequisites

- macOS (`sips`). On another OS the script stops with a clear message (swap `reshape()` for Pillow).
- `viewer/faces/raw/<theme>/` exists and holds the reviewed images from `/generate-faces`.

## Instructions

1. **Parse the theme** (first argument) and the optional `--keep` flag.
2. **Run the placer:**
   ```bash
   python3 .claude/skills/place-faces/place.py <theme> [--keep]
   ```
   It prints `place / keep / --` per file and a final tally, removes the `.gitkeep` from any emotion
   folder it fills, and leaves empty emotion folders untouched.
3. **Report** the tally (placed / kept / emotions-without-raw) and the folder `viewer/faces/<theme>/`.
   If some emotions had **no raw images**, name them and suggest `/generate-faces <theme>` to fill the
   gaps, then re-run this.
4. **Verify (optional):** the viewer resolves `faces/<theme>/<emotion>/*.png` at random; the daily mood
   (v0.6) picks the theme. No code change is needed — dropping the files in is enough.

## Important rules

- **Never touch the reference** `raw/<theme>/<theme>.png` — it is the source, not a face.
- **Output is exactly 768×768**, matching the existing finished theme packs.
- **Don't commit.** This skill only files images; the user reviews and commits.
- **`calm/` is required** per theme (the in-theme fallback). If the theme has no `calm` raw images, warn
  the user — the viewer will fall back to the default theme for missing emotions, but an explicit
  `calm/` is expected.
