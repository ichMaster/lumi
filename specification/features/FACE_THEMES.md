# Face variants & mood themes — Лілі's wardrobe

Builds on the **v0.7 local emotion viewer**. Two things make her face feel alive instead of
mechanical: she stops repeating the same picture (**several variants per emotion**, picked at
random), and she **dresses for the day** (themed outfit packs, the theme chosen by her **mood
of the day**). It lands as **v0.10**.

> Both features are **renderer + mood-side**. The model still emits only `{reply, emotion,
> intensity}` (EMOTION.md §3) — the v0.3 contract is untouched. They reuse the v0.7 signal +
> `calm` fallback and the v0.6 mood. The web portrait (v2.1) can reuse the same packs.

## Essence

- **Variants.** Each emotion is a *folder* of images; the viewer shows a **random** one (no
  immediate repeat), so the same emotion isn't the same picture twice running.
- **Themes.** Each theme is a full face pack in different clothes; the **daily mood (v0.6)
  picks the theme** that fits the day. Her outfit shifts with her temperament, once per local day.

## The faces tree

```
faces/
  <theme>/                  # one folder per theme (outfit set) — e.g. neon/  cozy/  sharp/
    <emotion>/              # one folder per emotion — joy/  calm/  …  (the 9 enum values)
      01.png 02.png 03.png  # any number of variants — the viewer picks one at random
    calm/                   # REQUIRED per theme — the in-theme fallback
  themes.md                 # the theme manifest: name + one-line description + the default
```

**Backward-compatible with v0.7:** a flat `faces/<emotion>.png` (no theme, one variant) still
works — it's the implicit single/default theme. So v0.7 packs keep running unchanged.

## 1. Variants — random, not predictable

- The resolver gathers every image for the current `(theme, emotion)` —
  `faces/<theme>/<emotion>/*.png` — and picks one at **random, with no immediate repeat** (the
  same logic as the v0.4 idle-nudge picker).
- It re-picks when her emotion changes; **optionally** on a coarse interval (config) so a
  long-held emotion still breathes.
- **Total over the enum, `calm` fallback:** a missing emotion → the theme's `calm/`; a missing
  theme → the default theme; an empty set → the v0.7 single image. The window never breaks.
- **Intensity:** v0.7 used it to pick `_low`/`_high`; here variety is the chosen axis, so
  intensity is **not** required to vary the picture. (It may later narrow the random pick — an
  optional refinement, not part of v0.10's must-have.)

## 2. Themes — the wardrobe, chosen by mood

- Each theme is a complete pack (all 9 emotions, with variants) in a different outfit/setting.
- **Theme manifest** (`faces/themes.md`, editable like the canon/styles): each theme → a
  one-line description (so the mood can choose), plus the **default theme**. Theme folders are
  auto-discovered; the manifest gives them meaning and the fallback.
- **The mood picks it (v0.6 coupling).** The daily mood call already runs once per local day; it
  is handed the available theme **names + descriptions** and **returns a chosen theme** that fits
  the day's mood, alongside the resolution. The choice is cached **with the mood** (per local
  day) and recomputed at local midnight.
  - e.g. a bright, social day → a vivid theme; a low, quiet day → a muted, cozy theme.
- **Graceful:** mood off / failed / no themes → the **default theme** (or the flat v0.7 pack).
  It never blocks a turn.

## 3. The signal

The core's one-line face signal (v0.7) extends to carry the theme:

```
<theme> <emotion> <intensity>        e.g.   cozy sad 0.30
```

The viewer reads the theme + emotion and renders a random `faces/<theme>/<emotion>/*.png`. A bare
`<emotion> <intensity>` (no theme) still works → the default theme. The core writes the **theme
of the day** (from the mood) together with the per-turn emotion.

## 4. Where it touches the code

- **viewer (v0.7):** the resolver gains theme + random-variant support (`face_for` → a set +
  random pick; `FaceSwitcher` remembers the last pick); the signal parse gains the theme.
- **core mood (v0.6):** `MoodState` gains a `theme`; the daily mood call also returns it; the
  face signal includes it.
- **authoring:** `viewer/faces/PROMPTS.md` extends — per-emotion variants (same expression, small
  natural differences) and per-theme wardrobes (same identity + framing, different clothes/setting).

## 5. Safety & fallback (unbreakable, like v0.7)

Resolution chain, always ending in a picture: a missing variant → another variant → the theme's
`calm` → the **default theme** → the v0.7 single `calm.png`. With no themes present it behaves
exactly like v0.7. The contract (EMOTION.md §3) does not change — variants and themes are
renderer + mood-side.

## Mapping to the Lumi roadmap

**v0.10 — Face variants & mood themes**, right after the v0.7 viewer: variety + a wardrobe that
follows her mood. **Depends on v0.7** (the viewer + the signal) and **v0.6** (the mood). The web
portrait (v2.1) reuses the same packs.
