#!/usr/bin/env python3
"""Skill: generate-faces — generate a theme's raw face set with Google Gemini ("Nano Banana").

For a theme <T>, takes ``viewer/faces/raw/<T>/<T>.png`` as the identity + wardrobe reference and,
for every (emotion × intensity) prompt in ``viewer/faces/PROMPTS.md``, asks
``gemini-2.5-flash-image`` to re-render the SAME portrait with ONLY the facial expression changed.
Each result is saved as ``viewer/faces/raw/<T>/<filename>.png`` at ~1K. Existing files are skipped
(so hand-made examples are never clobbered) unless ``--force``.

Stdlib only (urllib) — no third-party deps. Needs ``GEMINI_API_KEY`` (env or repo ``.env``).
Get a key at https://aistudio.google.com/apikey .

Usage:
    python3 generate.py <theme> [--force] [--only joy,sad] [--sleep 2] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../generate-faces -> skills -> .claude -> repo root
FACES = ROOT / "viewer" / "faces"
PROMPTS = FACES / "PROMPTS.md"

EMOTIONS = ["calm", "joy", "playful", "tender", "thoughtful", "serious", "surprise", "doubt", "sad"]

MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
API_VER = os.getenv("GEMINI_API_VERSION", "v1beta")
ENDPOINT = f"https://generativelanguage.googleapis.com/{API_VER}/models/{MODEL}:generateContent"
IMAGE_SIZE = os.getenv("GEMINI_IMAGE_SIZE", "1K")  # 512 / 1K / 2K / 4K


# --- key + parsing -------------------------------------------------------------------------------
def load_key() -> str | None:
    """GEMINI_API_KEY / GOOGLE_API_KEY from the environment, falling back to the repo .env."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key and key.strip():
        return key.strip()
    envf = ROOT / ".env"
    if envf.exists():
        for raw in envf.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            for name in ("GEMINI_API_KEY=", "GOOGLE_API_KEY="):
                if line.startswith(name):
                    val = line.split("=", 1)[1].split("#", 1)[0]  # value, minus any inline comment
                    val = val.strip().strip('"').strip("'")
                    if val:  # skip an empty placeholder; let a later line / None win
                        return val
    return None


def _strip_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)  # drop **bold**
    s = re.sub(r"\*(.+?)\*", r"\1", s)      # drop *italic* (e.g. author notes)
    return s.replace("`", "").strip()


def _anchor_expression(text: str) -> str:
    """The ``## ANCHOR`` section's ``> Expression: …`` blockquote (used for calm.png)."""
    m = re.search(r"## ANCHOR.*?\n((?:>.*\n?)+)", text)
    if not m:
        return "relaxed and attentive; a faint closed-lip smile; the gaze soft and at ease."
    block = " ".join(line.lstrip("> ").rstrip() for line in m.group(1).splitlines())
    block = re.sub(r"(?i)^\s*expression:\s*", "", block.strip())
    return _strip_md(block)


def parse_prompts(text: str) -> dict[str, str]:
    """Map ``<file>.png -> expression text`` for every ``- `x.png` — Expression: …`` bullet."""
    anchor = _anchor_expression(text)
    out: dict[str, str] = {}
    for m in re.finditer(r"^-\s+`([^`]+\.png)`\s*[—–-]\s*(.+)$", text, re.M):
        fname, desc = m.group(1).strip(), m.group(2).strip()
        dm = re.match(r"(?i)expression:\s*(.+)$", desc)
        if dm:
            out[fname] = _strip_md(dm.group(1))
        elif fname == "calm.png":  # "the anchor (mid)" placeholder → the ANCHOR block
            out[fname] = anchor
    out.setdefault("calm.png", anchor)
    return out


def theme_line(theme: str) -> str:
    """The one-line mood for <theme> from themes.md, to reinforce the (already-in-reference) setting."""
    tf = FACES / "themes.md"
    if not tf.exists():
        return ""
    m = re.search(rf"^##\s+{re.escape(theme)}\s*\n(.+)$", tf.read_text(encoding="utf-8"), re.M)
    return _strip_md(m.group(1)) if m else ""


def _sanitize_expression(expr: str) -> str:
    """Reword 'warm eyes' wording — the image model misreads it as an amber eye COLOUR, not feeling."""
    expr = re.sub(r"(?i)\beyes\s+warm\b", "eyes tender", expr)        # "eyes warm and glistening"
    expr = re.sub(r"(?i)\bwarm(\s+\w+\s+eyes)\b", r"tender\1", expr)  # "warm caring eyes"
    return expr


def build_prompt(mood: str, expression: str) -> str:
    mood_s = f" (scene mood: {mood.rstrip(' .')})" if mood else ""
    expression = _sanitize_expression(expression)
    return (
        "This is a reference portrait of a young woman (Лілі). Produce the SAME portrait again, "
        "changing ONLY her facial expression. Keep her identity exactly as in the reference: the "
        "split-dyed magenta-left / cobalt-blue-right hair, and the same face. Her eyes MUST stay the "
        "exact cool misty grey-blue iris colour of the reference — never green, amber, hazel, brown, "
        "red or pink, even when the expression is emotional or the eyes look moist. "
        "Copy the EXACT hairstyle, clothing, jewellery, accessories, background, setting, "
        "lighting, colour palette and the head-and-shoulders framing of the reference faithfully. "
        "Do NOT add, remove or alter any accessory or prop that is not already in the reference — "
        "for example, do not add headphones if the reference has none."
        f"{mood_s} Change ONLY her facial expression to: {expression} "
        "In that expression, any words like 'warm', 'glowing' or 'glistening' describe her FEELING "
        "and emotion only — they never change her eye colour, which stays cool grey-blue. "
        "Everything else stays identical — same person, same hair, same outfit, same scene, same "
        "composition; only the expression differs. High-detail neon-noir digital painting, identical "
        "style to the reference."
    )


# --- Gemini call ---------------------------------------------------------------------------------
def _extract_image(data: dict) -> bytes:
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"no candidates (safety block?): {json.dumps(data)[:300]}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    for p in parts:
        inl = p.get("inline_data") or p.get("inlineData")  # request snake, response camel
        if inl and inl.get("data"):
            return base64.b64decode(inl["data"])
    said = " ".join(p.get("text", "") for p in parts)[:300]
    raise RuntimeError(f"no image returned; model said: {said!r}")


def call_gemini(key: str, prompt: str, ref_b64: str, *, with_format: bool = True, retries: int = 4) -> bytes:
    parts = [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": ref_b64}}]
    gen: dict = {"responseModalities": ["TEXT", "IMAGE"]}
    if with_format:
        gen["responseFormat"] = {"image": {"imageSize": IMAGE_SIZE}}
    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": gen}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    delay = 5.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return _extract_image(json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "ignore")
            # responseFormat/imageSize support varies by model (gemini-2.5-flash-image rejects "1K").
            # It's the only optional part of the request, so on ANY 400 retry without it (native ~1K).
            if e.code == 400 and with_format:
                return call_gemini(key, prompt, ref_b64, with_format=False, retries=retries)
            if e.code == 429 and ("limit: 0" in err or "free_tier" in err or "free tier" in err.lower()):
                raise RuntimeError(
                    "Gemini quota is 0 for this model — gemini-2.5-flash-image (Nano Banana) is not on "
                    "the free tier. Enable BILLING on your Google project, then re-run "
                    "(~$0.04/image): https://aistudio.google.com → your key's project → set up billing."
                ) from e
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {e.code}: {err[:400]}") from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"network error: {e}") from e
    raise RuntimeError("exhausted retries")


# --- main ----------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a theme's raw face set with Gemini (Nano Banana).")
    ap.add_argument("theme", help="theme name, e.g. drowning (folder viewer/faces/raw/<theme>/)")
    ap.add_argument("--force", action="store_true", help="regenerate files that already exist")
    ap.add_argument("--only", default="", help="limit to these emotions, e.g. tender,sad")
    ap.add_argument("--files", default="",
                    help="(re)generate exactly these filenames, e.g. tender_high.png,sad.png — implies --force")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds to wait between API calls")
    ap.add_argument("--dry-run", action="store_true", help="print the prompts; no API calls, nothing written")
    args = ap.parse_args()

    raw = FACES / "raw" / args.theme
    ref = raw / f"{args.theme}.png"
    if not ref.exists():
        sys.exit(f"ERROR: reference image not found: {ref}")
    if not PROMPTS.exists():
        sys.exit(f"ERROR: prompts file not found: {PROMPTS}")

    prompts = parse_prompts(PROMPTS.read_text(encoding="utf-8"))
    mood = theme_line(args.theme)
    only = {e.strip() for e in args.only.split(",") if e.strip()}
    explicit = [f.strip() for f in args.files.split(",") if f.strip()]

    force = args.force
    targets: list[str] = []
    if explicit:  # exact filenames win — always (re)generated
        force = True
        for fn in explicit:
            if fn in prompts:
                targets.append(fn)
            else:
                print(f"  (skip unknown file: {fn})")
    else:
        for emo in EMOTIONS:
            if only and emo not in only:
                continue
            for fn in (f"{emo}_low.png", f"{emo}.png", f"{emo}_high.png"):
                if fn in prompts:
                    targets.append(fn)

    print(f"Theme: {args.theme}  |  reference: {ref.name}  |  model: {MODEL}  |  size: {IMAGE_SIZE}")
    print(f"Targets: {len(targets)}  (skip-existing={'no' if force else 'yes'})\n")

    if args.dry_run:
        for fn in targets:
            print(f"=== {fn} ===\n{build_prompt(mood, prompts[fn])}\n")
        print(f"[dry-run] {len(targets)} prompts shown; no API calls, nothing written.")
        return

    key = load_key()
    if not key:
        sys.exit("ERROR: GEMINI_API_KEY not set (env or .env). Get one at https://aistudio.google.com/apikey")
    ref_b64 = base64.b64encode(ref.read_bytes()).decode()

    done = skip = fail = 0
    for fn in targets:
        out = raw / fn
        if out.exists() and not force:
            print(f"  skip   {fn} (exists)")
            skip += 1
            continue
        try:
            img = call_gemini(key, build_prompt(mood, prompts[fn]), ref_b64)
            out.write_bytes(img)
            print(f"  OK     {fn}  ({len(img) // 1024} KB)")
            done += 1
        except Exception as e:  # noqa: BLE001 — report per-file, keep going
            print(f"  FAIL   {fn}: {e}")
            fail += 1
        time.sleep(args.sleep)

    print(f"\nDone. generated={done} skipped={skip} failed={fail}")
    print(f"Review the images in: {raw}")
    print(f"When happy, place them with:  /place-faces {args.theme}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
