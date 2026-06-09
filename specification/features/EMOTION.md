# EMOTION.md

The emotion channel for Lumi — the cross-cutting mechanism referenced by
[MISSION.md](../MISSION.md), [ARCHITECTURE.md](../ARCHITECTURE.md), and
[ROADMAP.md](../ROADMAP.md). Лілі returns her emotional state alongside every reply;
how that state is *shown* changes by version, but the channel itself does not.

This document defines a single emotion **contract** emitted by the model, a single
**`IEmotionRenderer`** interface, and a **ladder of interchangeable renderers** —
so improving how emotion looks is a **renderer swap, not a rewrite**:

- **Logged (v0.3):** the field is validated and logged; optionally a small TUI status line.
- **Emoji (v0.5):** emotion → an emoji shown next to the reply in the terminal.
- **Local image face (v0.7):** emotion → a portrait of Лілі in a separate local desktop window, from a `faces/` asset pack — no server (see [EMOTION_VIEWER.md](EMOTION_VIEWER.md)).
- **Web portrait + caption (v2.1):** emotion → the same portrait in the web interface, plus a short descriptive caption (§6).
- **Animation (v3.1):** the portrait comes alive (transitions, idle motion, lip-sync to voice).

## 1. Goals and non-goals

**Goals**
- Carry Лілі's current emotional state on every turn, decided **by the model**, not guessed after the fact.
- Decouple emotion *content* (the contract) from emotion *presentation* (the renderer).
- Make each render tier (log → emoji → image → animation) share one contract and one enum, so the channel is locked once (v0.3) and only the renderer changes later.

**Non-goals**
- A separate "emotion engine" that infers state from the text. In Lumi the **model emits its own state** as a structured field (MISSION: "emotion is part of the reply").
- Per-frame model calls. Emotion is decided once per turn with the reply; animation/lip-sync (v3) run locally off that single state plus the TTS audio.

## 2. Who decides emotion (Lumi-specific)

The **model** returns `emotion` + `intensity` as part of its structured reply. The
**core** validates the field against the enum (§3) and the 0–1 range, repairs or
falls back on invalid output (§8), logs it, and hands a clean `EmotionState` to
whatever renderer the current interface uses. The renderer never asks the model
anything — it only renders the state it is given. This is the seam that lets the
TUI and the web be two faces of one core.

## 3. The emotion contract (shared across all tiers)

One object, the same schema from v0 onward. It is the persona output contract from
ARCHITECTURE.md, formalized:

```json
{
  "reply": "…Лілі's text…",
  "emotion": "playful",
  "intensity": 0.8,
  "ttl_ms": 8000,
  "speaking": false
}
```

Fields:
- `reply` — string. Лілі's spoken/written text. Required.
- `emotion` — enum, see §4. Required. The model is constrained to this fixed set.
- `intensity` — float 0.0–1.0. Scales presentation: emoji variant/emphasis (v0.5),
  portrait intensity variant (v2.1), animation amplitude and idle motion (v3).
  Required.
- `ttl_ms` — int, optional (default 8000). After this with no new turn, an
  animated renderer (v3) relaxes toward `calm`. Ignored by the log/emoji/static tiers.
- `speaking` — bool, optional (default false). Set by the renderer (not the model)
  while voice (v2.2) is playing, to drive lip-sync (v3). Not part of the model's output.

The model outputs only `{reply, emotion, intensity}`; `ttl_ms`/`speaking` are
renderer-side concerns reserved here so the contract does not change when animation
and voice arrive.

## 4. Emotion enum

A small, **fixed** set of 9. Every renderer tier implements the same names. The
model is instructed (and, where the SDK supports it, schema-constrained) to return
exactly one.

| emotion      | reads as                                  | emoji (v0.5) | portrait key (v2.1) |
|--------------|-------------------------------------------|------------|-------------------|
| `joy`        | bright, openly happy                      | 😄         | `joy`             |
| `calm`       | base, resting, attentive (the neutral)    | 🙂         | `calm`            |
| `playful`    | teasing half-smile, light                 | 😏         | `playful`         |
| `tender`     | soft, warm, gentle                        | 🥰         | `tender`          |
| `thoughtful` | contemplative, gaze aside, considering    | 🤔         | `thoughtful`      |
| `serious`    | focused, level, no smile                  | 😐         | `serious`         |
| `surprise`   | sudden, wide-eyed                         | 😮         | `surprise`        |
| `doubt`      | uncertain, skeptical, a small frown       | 😕         | `doubt`           |
| `sad`        | downcast, quiet                           | 😢         | `sad`             |

`calm` is the neutral / fallback state (§8). The emoji column is the v0.5 mapping;
the portrait-key column is the v2.1 asset manifest key (§7).

## 5. The renderer interface

The core codes against one interface; each tier is one implementation. In Python
(the TUI and the web both depend on `core`, never the reverse):

```python
from typing import Protocol

class IEmotionRenderer(Protocol):
    def render(self, state: EmotionState) -> None: ...      # show the new state
    def set_speaking(self, speaking: bool) -> None: ...     # v2.2+ voice → lip-sync (v3)
    def tick(self, dt_ms: int) -> None: ...                 # v3 idle loop: transitions, micro-motion
```

- **`LogRenderer` (v0.3)** — writes the validated field to the log; optional TUI status line. `tick`/`set_speaking` are no-ops.
- **`EmojiRenderer` (v0.5)** — maps `emotion`→emoji (§4) and shows it beside the reply; `intensity` may pick an emphasis variant.
- **Local viewer (v0.7)** — a separate desktop process that polls a local emotion **signal** and shows `faces/<emotion>.png` from the §7 asset pack. A renderer of the channel in spirit, decoupled via a file signal rather than an in-process `render()` call (see [EMOTION_VIEWER.md](EMOTION_VIEWER.md)).
- **`ImageRenderer` (v2.1)** — resolves `emotion`(+`intensity`)→a portrait asset (§7, the **same pack** as the v0.7 viewer) and swaps the web portrait panel; also shows the §6 caption.
- **`AnimationRenderer` (v3.1)** — `render` sets a target expression and crossfades; `tick` runs the idle loop (blink, breathe, micro gaze-drift); `set_speaking` + the TTS amplitude envelope drive mouth lip-sync.

Only the renderer changes between versions. The `EmotionState` and the enum are constant.

## 6. Emoji mapping (v0.5) and mood caption (v2.1)

**Emoji (v0.5).** The `emoji` column of §4 gives each emotion its base **face**;
`intensity` scales the **emphasis, not the feeling** — the *same* face, made stronger by
**repeating it or adding an accent emoji**. Three intensity bands (so the default `0.5`
lands at "mid"):

| band   | intensity     | emphasis            |
|--------|---------------|---------------------|
| low    | `0.00 – 0.33` | face only           |
| mid    | `0.34 – 0.66` | + 1 (repeat/accent) |
| high   | `0.67 – 1.00` | + 2 (repeat/accent) |

| emotion         | low (subtle) | mid (~0.5, default) | high (strong) | scales by |
|-----------------|:------------:|:-------------------:|:-------------:|-----------|
| `joy` 😄        | 😄           | 😄✨                | 😄✨✨         | add ✨ |
| `calm` 🙂       | 🙂           | 🙂                  | 🙂            | — (neutral / fallback) |
| `playful` 😏    | 😏           | 😏😜                | 😏😜😜        | add 😜 |
| `tender` 🥰     | 🥰           | 🥰💕                | 🥰💕💕        | add 💕 |
| `thoughtful` 🤔 | 🤔           | 🤔💭                | 🤔💭💭        | add 💭 |
| `serious` 😐    | 😐           | 😐❗                | 😐❗❗         | add ❗ |
| `surprise` 😮   | 😮           | 😮😮                | 😮😮😮        | repeat 😮 |
| `doubt` 😕      | 😕           | 😕❓                | 😕❓❓         | add ❓ |
| `sad` 😢        | 😢           | 😢😢                | 😢😢😢        | repeat 😢 |

The **face never changes** within an emotion (only the emphasis grows), the map is
**total over the enum**, and `calm` (the neutral / fallback) does not escalate. So a
reply at `joy 0.9` reads `Лілі 😄✨✨:` and at `sad 0.8` reads `Лілі 😢😢😢:`. v0.5's job
is to prove the channel reads in the terminal end to end.

This table is the **built-in default**; in v0.5 it is loaded from an **editable authored
file** (`LUMI_EMOJI_PATH`, like the canon / styles / nudges), so the user can **change
the table and add / remove / replace emojis** without touching code. A missing file or an
absent/blank row falls back to this default (ultimately the base glyph → `calm`), keeping
the resolved map total over the enum.

**Mood caption (v2.1).** Alongside the web portrait, a short evocative **caption**
describes Лілі's current state — **not** the emotion's enum name, and not her
reply: a small atmospheric line in her spirit. A curated phrase per emotion
(presentation-only, no contract change; `intensity` may pick a variant, and a few
rotating variants per emotion are allowed). Illustrative set:

| emotion      | caption (illustrative)     |
|--------------|----------------------------|
| `joy`        | lit up, openly glad        |
| `calm`       | quietly here               |
| `playful`    | a teasing little smile     |
| `tender`     | soft and warm              |
| `thoughtful` | somewhere in thought       |
| `serious`    | level and present          |
| `surprise`   | caught off guard           |
| `doubt`      | not quite sure             |
| `sad`        | a quiet heaviness          |

The caption map is **total over the enum** and **never emits the bare emotion
name**. Final wording is authored in Лілі's voice (canon); the table above is a
placeholder.

## 7. Image asset manifest (v0.7 / v2.1)

The portrait tier is described by a manifest so adding/replacing art never
touches the core — only the manifest and the image files change. **The same pack
is shared** by the local viewer (v0.7, a `faces/` folder) and the web
`ImageRenderer` (v2.1):

```json
{
  "pack_id": "lili_v1",
  "canvas": [768, 768],
  "default": "calm",
  "emotions": {
    "joy":        { "image": "joy.png" },
    "calm":       { "image": "calm.png" },
    "playful":    { "image": "playful.png" },
    "tender":     { "image": "tender.png" },
    "thoughtful": { "image": "thoughtful.png" },
    "serious":    { "image": "serious.png" },
    "surprise":   { "image": "surprise.png" },
    "doubt":      { "image": "doubt.png" },
    "sad":        { "image": "sad.png" }
  }
}
```

Optional `intensity` variants per emotion (e.g. `"joy": {"low": "...", "high": "..."}`)
may be added within the same schema; the resolver falls back to the single `image`
when no variant matches. Full PNG quality, no palette limits (ROADMAP v2.1 DoD).
Asset packs live in `/web` (or `/assets`) — see §10.

## 8. Validation and fallback

The core never trusts raw model output:
- **Schema enforcement first.** Use the model's constrained output — **Anthropic tool/structured output** for Claude Haiku (v0.1); for the models added in v0.21, each provider's mechanism (OpenAI/DeepSeek JSON-schema, MiniMax JSON) — to force `emotion` to the enum and `intensity` to a 0–1 number so invalid values are rare by construction. (The gate below is still the real safety net.)
- **Validation gate.** On parse: an unknown/missing `emotion` → `calm`; `intensity` clamped to `[0,1]`, missing → `0.5`; a missing `reply` is an error surfaced to the interface (not a silent empty turn).
- **Log every repair** keyed by `session_id`/turn so drift in model behavior is visible (ARCHITECTURE §Observability).

This is the single place emotion can go wrong, so it is tested: a contract test
pins the schema and a unit test pins the repair/fallback rules (ARCHITECTURE
§Testing and CI).

## 9. Voice delivery (v2.2, optional)

Where the TTS voice supports it, the `emotion`/`intensity` may bias delivery
(tone/tempo). This is presentation only and best-effort — it never changes the
`reply` text. The renderer sets `speaking=true` while audio plays so a later
animated face (v3) can lip-sync.

## 10. Mapping to the Lumi roadmap

- **v0.3 — emotion field.** The contract (§3), the enum (§4), validation/fallback
  (§8), and `IEmotionRenderer` + `LogRenderer` are **locked here**. Pinned by a
  contract test. Renderers after this are swaps.
- **v0.5 — emoji.** `EmojiRenderer` (§6). No contract change.
- **v0.7 — local image face.** A separate desktop viewer over a local signal + the §7 asset pack (`faces/`); `calm` fallback (see [EMOTION_VIEWER.md](EMOTION_VIEWER.md)). No contract change.
- **v2.1 — web portrait + caption.** `ImageRenderer` + the same asset manifest (§7) in the browser, plus the §6 mood caption. No contract change.
- **v2.2 — voice.** Optional emotion-biased TTS delivery (§9); renderer sets `speaking`.
- **v3.1 — animation.** `AnimationRenderer` (§5): transitions, idle loop, lip-sync. The same `EmotionState` drives it.

## 11. Repo placement

- `specification/features/EMOTION.md` — this file.
- `core/` — the `EmotionState` model, the enum, the validation/fallback gate, the `IEmotionRenderer` interface, and (v0.7) writing the current emotion to the local signal.
- `tui/` — `LogRenderer` (v0.3), `EmojiRenderer` (v0.5).
- `viewer/` (v0.7) — the local desktop face window (Tkinter or similar) + the `faces/` asset pack; polls the local signal (see [EMOTION_VIEWER.md](EMOTION_VIEWER.md)).
- `web/` (v1.4+) — `ImageRenderer` + the mood caption, the portrait panel, and the same asset pack (`lili_v1`); `AnimationRenderer` (v3).
</content>
