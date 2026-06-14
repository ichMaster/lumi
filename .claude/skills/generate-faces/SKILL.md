---
name: generate-faces
description: Generate a theme's raw face set (27 emotion×intensity portraits) from its reference image with Google Gemini "Nano Banana" (gemini-2.5-flash-image), saving them to viewer/faces/raw/<theme>/ for review.
---

# Skill: Generate Theme Faces (Gemini / Nano Banana)

Generate the raw face images for one **theme** from its reference picture. For each
`<emotion>[_low|_high].png` prompt in `viewer/faces/PROMPTS.md`, this re-renders the **same** portrait
with **only the facial expression changed**, using `viewer/faces/raw/<theme>/<theme>.png` as the
identity + wardrobe reference (image-to-image). Output lands in the **same `raw/<theme>/` folder** for
you to review before the second skill (`/place-faces`) reshapes and files them.

This is step 1 of 2: **generate (this) → review → `/place-faces <theme>`**.

## Usage

```
/generate-faces <theme> [--force] [--only joy,sad] [--files a.png,b.png] [--dry-run]
```

- `/generate-faces drowning` — generate every missing image for the `drowning` theme.
- `/generate-faces drowning --only tender,thoughtful` — only those emotions (all 3 intensities each).
- `/generate-faces drowning --files tender_high.png,sad.png` — (re)generate exactly those files
  (always overwrites — use this to redo one image, e.g. a wrong eye colour).
- `/generate-faces drowning --force` — also regenerate images that already exist (overwrite).
- `/generate-faces drowning --dry-run` — print the assembled prompts only; **no API calls, costs nothing**.

By default **existing files are skipped**, so hand-made examples already in the folder are never
overwritten.

## Prerequisites

- A **Gemini API key** in `GEMINI_API_KEY` (env var or a line in the repo `.env`). Get one free at
  https://aistudio.google.com/apikey . The script reads it directly; never print the key.
- The reference image `viewer/faces/raw/<theme>/<theme>.png` must exist.
- This calls a **paid** Google API (one request per image, up to 27). Confirm with the user before a
  full non-dry run if they haven't already asked for it.

## Instructions

1. **Parse the theme** (first argument) and any flags from the invocation.
2. **Preflight (no spend):** run a `--dry-run` first if the user is unsure, OR check directly:
   - reference exists: `viewer/faces/raw/<theme>/<theme>.png`
   - key present: `GEMINI_API_KEY` in env or `.env` (do not echo its value).
   If the reference is missing, stop and tell the user to add `raw/<theme>/<theme>.png`. If the key is
   missing, stop and tell them to set `GEMINI_API_KEY` (point to the aistudio link).
3. **Run the generator:**
   ```bash
   python3 .claude/skills/generate-faces/generate.py <theme> [flags]
   ```
   It needs no venv (stdlib only). It prints `OK / skip / FAIL` per file and a final tally.
4. **Report** the tally to the user (generated / skipped / failed) and the folder
   `viewer/faces/raw/<theme>/`. If any file FAILED (safety block, network), say which and that a plain
   re-run resumes (it skips the ones already done).
5. **Point to the next step:** once they've reviewed the images, run `/place-faces <theme>` to reshape
   to 768×768 and file them into `viewer/faces/<theme>/<emotion>/`.

## Important rules

- **Never overwrite without `--force`.** The default protects manually-made images.
- **The reference image is the source of identity** — the prompt only changes the expression; do not
  edit `PROMPTS.md` here.
- **Do not commit anything.** This skill only writes image files into `raw/<theme>/` for review; the
  user reviews and commits (or runs `/place-faces`) themselves.
- **Never print or log the API key.**
- **Model:** `gemini-2.5-flash-image` ("Nano Banana") at `imageSize: 1K`. Override via env
  `GEMINI_IMAGE_MODEL` / `GEMINI_IMAGE_SIZE` only if the user asks.
