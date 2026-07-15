# Emotion viewer — local window for Лілі's face

The simplest way to show Лілі's face without a server or web: a separate **local desktop window** that displays pre-made emotion images. All it needs to know is **which emotion to show right now**. It is another **renderer of the emotion channel** (EMOTION.md §5), alongside the emoji in the TUI — the same `emotion → image` tier the web `ImageRenderer` (v4.1) will use, just rendered to a local window. It lands as **v0.7**.

> **Naming:** the face images live in a `faces/` asset pack — **not** the v6 `gallery` (the per-user artifact store, GALLERY_MCP.md). These are the **emotion-face assets** described in EMOTION.md §7; v0.7 and v4.1 share them.

## Essence

- The `faces/` folder holds **all emotions as ready-made files** (prepared in advance).
- The core writes **a single word** — Лілі's current emotion — into a small **local signal** each turn (it already has the emotion; it is part of the model's reply).
- The viewer reads that word and shows the matching `faces/<emotion>.png`. It periodically re-reads the signal; the word changes, the picture changes. No on-the-fly generation — just switching between existing images by emotion.

## The faces folder

File names match the Lumi emotion enum (EMOTION.md §4):

```
faces/
  calm.png       joy.png        playful.png
  tender.png     thoughtful.png serious.png
  surprise.png   doubt.png      sad.png
```

This is the same emotion-face asset pack (e.g. `lili_v1`) that the web `ImageRenderer` reuses in v4.1.

## The current-emotion signal

The core exposes the current emotion locally — either:
- a tiny `state/face.txt` with one word inside (e.g. `calm`), rewritten whenever the emotion field changes; or
- the emotion field already in local state behind `repository` (the viewer reads the same value the core persists).

Either way the viewer never talks to the core directly — only through the shared local signal.

## Viewer logic

1. Periodically (every ~0.5–1 s, or a filesystem watch) read the current emotion from the signal.
2. Build the path `faces/<emotion>.png`.
3. If it changed from the previous one — show the new file.
4. **Fallback:** an unknown word or a missing file → show `faces/calm.png` (the neutral default, per EMOTION.md §8), so the window never breaks.
5. **Idle relax (EMOTION.md `ttl_ms`):** if the signal hasn't changed for `LUMI_FACE_IDLE_SECONDS` (default ~120 s; `0` = off), relax the face to the **default (`calm`)** — a still-but-present resting face when the conversation pauses. The next signal change wakes it again (and resets the idle timer). Computed from an injected clock (deterministic; the resolver/idle logic is tested without a display).

## Optional — intensity

For finer control, keep variants like `joy_low.png` / `joy_high.png` and pick by `intensity` from the emotion field (the same optional intensity variants as EMOTION.md §7). One image per emotion is enough to start.

## Connection to the rest of Lumi

- A **separate local viewer process**, not embedded in the TUI (a terminal can't show PNGs); its only link to the core is the shared signal/folder.
- Another **renderer of the emotion channel** (EMOTION.md §5): in the TUI the emotion shows as an emoji (v0.5), here as an image from `faces/` by the same emotion word (v0.7), in the web as a portrait + caption (v4.1), later animated (v5.1). Same `EmotionState`, different renderer.
- Fully **local, no server** — the v0-stage way to get a real image face before the web. Server + web is the v4.1 sibling.
- Face images are placed by the user/Лілі in advance; later they can be supplemented by the creative layer's image generation (v6.3).

## Contract (minimal)

- **Input:** the current-emotion signal (a one-word file, or the emotion field in local state).
- **Action:** show `faces/<emotion>.png`, with a fallback to `calm`.
- **Update:** polling the signal (simple) or a filesystem watch (instant).

## Where it lives in the Lumi roadmap

**v0.7 — Local emotion viewer (image face)**, right after the emoji channel (v0.5): real emotion images locally, without a server, before the web. Stack — any simple desktop viewer (e.g. Python/Tkinter). Depends on: v0.3 (the emotion channel). The web sibling is v4.1.
</content>
