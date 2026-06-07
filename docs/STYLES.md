# Answer styles — how Лілі shapes the *form* of a reply (implementation reference)

A **style** changes *how* Лілі answers — length, structure, expressiveness, register —
**never what she knows or how warm she is**. It's the manual, form‑shaping sibling of the
future v0.5 daily **mood** (which colors tone automatically). This document describes the
**implemented** behavior; the design note lives in
[../specification/ARCHITECTURE.md](../specification/ARCHITECTURE.md) §Configuration.

> TL;DR — Styles are named text **overlays** authored in [../core/styles.md](../core/styles.md).
> The active style is injected at the **end** of the system prompt as a prioritized
> directive. Pick one with **`/style <name>`**; several **stack**; a **meta‑style** expands
> to several base styles. The selection is **per‑session** (resets to `normal`).

---

## 1. The two kinds of style

| Kind | What it is | Example |
|---|---|---|
| **Base style** | one overlay — a concrete instruction with a length limit | `коротко` → "2–3 sentences, ≤40 words" |
| **Meta‑style** | a preset that expands to **several** base styles | `лагідна` → `поясни + просто + приклад` |

`normal` is the default — **no overlay** (Лілі's plain self). Names are **Ukrainian**; base
styles are adverbs/nouns, meta‑styles are adjectives in Лілі's voice.

---

## 2. The base styles (16, by category)

Each base style carries a **concrete limit** (sentences / words / lines) so the directive
is enforceable. Authored in [../core/styles.md](../core/styles.md).

| Category | Style | Form | Limit |
|---|---|---|---|
| **Довжина** | `коротко` | brief, no preamble | 2–3 sentences / ~40 words |
| | `суть` | the essence only | 1 sentence / ~25 words |
| | `докладно` | exhaustive | **no length cap** |
| **Пояснення** | `поясни` | step‑by‑step + why + example | 4–8 sentences / ~150 words |
| | `просто` | like to a child, one analogy | 3–5 short sentences |
| | `приклад` | lead with a concrete example | 1 example + 2–3 sentences |
| | `метафора` | one running metaphor throughout | ~6 sentences |
| **Структура** | `списком` | bulleted list | 3–6 bullets, one line each |
| | `кроки` | numbered how‑to | one action per line |
| | `порівняй` | side‑by‑side / за‑проти table | + a one‑line verdict |
| | `практично` | concrete advice, no fluff | ≤5 points / ~80 words |
| **Тон** | `офіційно` | neutral, precise, no slang | full sentences |
| | `невимушено` | relaxed, contractions OK | like to a friend |
| | `емоційно` | feeling‑led, warm | 2–4 sentences / ~60 words |
| | `поетично` | lyrical, image‑rich | ≤4–5 lines |
| **Взаємодія** | `питанням` | answers by asking 1–2 questions back | ties to the «Коани» game |

---

## 3. The meta‑styles (6, presets)

A meta‑style is a **bundle** — choosing it selects several base styles at once. Named as
adjectives in Лілі's voice. Authored as `= a, b, c` alias lines in
[../core/styles.md](../core/styles.md).

| `/style` | expands to | the vibe |
|---|---|---|
| **`блискавична`** | `коротко` + `списком` | швидко, яскраво |
| **`лагідна`** | `поясни` + `просто` + `приклад` | веде лагідно |
| **`прискіплива`** | `докладно` + `порівняй` + `офіційно` | розкладає прискіпливо |
| **`завзята`** | `кроки` + `практично` + `невимушено` | береться завзято |
| **`лірична`** | `поетично` + `метафора` + `емоційно` | лірика, чуття |
| **`допитлива`** | `питанням` + `приклад` + `невимушено` | вертає питаннями |

---

## 4. Using `/style` (TUI)

| Command | Effect |
|---|---|
| `/style` | lists meta‑styles + base styles, and the current selection |
| `/style лагідна` | switch to a meta‑style (expands to its base styles) |
| `/style коротко офіційно` | **stack** several styles (space‑, comma‑, or `+`‑separated) |
| `/style лірична невимушено` | mix a meta‑style with a base style |
| `/style normal` | clear back to the default (no overlay) |

Rules:
- **Stacking** — selected overlays are concatenated **in order**, under one directive header.
- **All‑or‑nothing** — if *any* name is unknown (`/style коротко xxx`), nothing changes and
  you get `Unknown style in '…'`.
- **`normal`** anywhere clears the overlay; duplicates are de‑duped; order is preserved.
- The **status line** shows the active selection (e.g. `… · style: коротко+офіційно`); a
  meta‑style shows its own name (`style: лагідна`).
- **Per‑session** — the selection resets to `normal` on `/new` and on restart (it is *not*
  persisted).

---

## 5. How a style reaches the model

Every turn, `Core._system_prompt(session)` ([../core/agent.py](../core/agent.py)) resolves the
active selection to overlay text and passes it to the assembler. The style is the **last**
block of the system prompt, framed by an importance header:

```
system = canon
       + past-session summaries
       + long-term facts
       + session digest (in-session compaction)
       + ── STYLE_HEADER ──            ← prioritized directive, the last thing the model reads
         <overlay text of the active style(s)>
messages = [ …live tail… ] + your new line
```

`build_system_prompt(…, style=…)` ([../core/prompt.py](../core/prompt.py)) appends:

```python
if style:
    parts.append(f"{STYLE_HEADER}\n{style}")   # at the very end
```

`STYLE_HEADER` makes it a **prioritized directive** — paraphrased: *"ВАЖЛИВО — ФОРМАТ І
ДОВЖИНА ТВОЄЇ ВІДПОВІДІ. Дотримуйся цього СУВОРО; це має пріоритет над типовою
багатослівністю…"*. Placing it last (recency) and emphasizing it is what makes a short
style actually override Лілі's default verbosity.

> The overlay is **prompt text only** — there is no code that truncates the reply. The model
> follows the instruction; the limits are guidance, not hard enforcement.

---

## 6. How it's wired (code map)

| File | Responsibility |
|---|---|
| [../core/styles.md](../core/styles.md) | the authored styles — `## name` + body; `= a, b, c` for a meta‑style; `#` comments / category headers |
| [../core/styles.py](../core/styles.py) | `load_styles` (base, prose), `load_meta_styles` (`=` aliases), `_sections` parser, `DEFAULT_STYLE` |
| [../core/config.py](../core/config.py) | `styles_path` (default `core/styles.md`, env `LUMI_STYLES_PATH`) |
| [../core/agent.py](../core/agent.py) | `Core._styles`/`_meta`/`_active`; `set_style` (parse + validate, all‑or‑nothing), `_expand` (meta→base), `_style_overlay`, `style`/`style_names`/`base_names`/`meta_names`; `start_session` resets; `build_core` loads both |
| [../core/prompt.py](../core/prompt.py) | `STYLE_HEADER`, `build_system_prompt(…, style)` — appends the overlay last |
| [../tui/app.py](../tui/app.py) | the `/style` command (`_style_command`), the status‑line style display |

### The resolution flow

```
core/styles.md
   │  load_styles ─────────▶ Core._styles  (base: name → overlay text)
   │  load_meta_styles ────▶ Core._meta    (meta: name → [base names])
   ▼
/style <spec>  →  set_style(spec)
                    split on space/comma/+, lowercase
                    validate ALL against {normal} ∪ base ∪ meta   (else reject)
                    → Core._active = [tokens]            (per-session)
                    │
Core._system_prompt → _style_overlay → _expand (meta→base, dedupe, order)
                    → "\n\n".join(base overlay texts)
                    → build_system_prompt(style=…)  → STYLE_HEADER + overlay (at the end)
                    → model
```

`set_style` parses one or several names (a base style or a meta‑style); validation is
**all‑or‑nothing**. `_expand` turns the active tokens into an ordered, de‑duped list of base
styles (a meta expands to its members; a base maps to itself; unknown alias members are
skipped). `style` returns the display name — `"+".join(active)` (e.g. `лагідна`,
`коротко+офіційно`), or `normal` when empty.

---

## 7. Authoring & extending

Everything is one editable file — no code change needed.

**Add a base style** — append a section with a concrete limit:

```markdown
## стисло
Дуже стисло, телеграфно: до 15 слів, без зайвих слів.
```

**Add a meta‑style** — append an alias section referencing base style names:

```markdown
## учительська
= поясни, приклад, офіційно
```

Notes:
- A style **name must be a single token** (no spaces) — `set_style` splits on spaces, commas,
  and `+`.
- A meta‑style is anything whose body starts with `=`; otherwise the section is a base style.
- Category headers and any `#`‑prefixed line are **comments** (skipped by the loader); only
  `## name` starts a section. `normal` and empty bodies are dropped.
- Meta alias members should reference **real** base style names (a typo'd member is silently
  skipped at expansion).

Point at a different file with `LUMI_STYLES_PATH` in `.env`.

---

## 8. Tests

All in [../tests/integration/test_styles.py](../tests/integration/test_styles.py), against the
mock model (no paid calls):

- **loader** — the authored file yields the 16 Ukrainian base styles + the 6 meta‑styles;
  every meta expands to ≥2 real base styles; category headers never leak into a body; a
  missing file → `{}`.
- **core** — `normal` has no overlay; a single style injects its overlay **last**, with the
  importance header; unknown is rejected; per‑session reset on `start_session`.
- **stacking & metas** — several styles stack in order; comma/`+` separators; all‑or‑nothing
  on an unknown name; `normal` clears; a meta‑style expands to its base overlays; metas and
  base styles combine (`_expand` order); names list separately.
- **prompt assembly** — `build_system_prompt` places the style block last with `STYLE_HEADER`.
- **TUI** — `/style` lists; `/style <name>` switches and updates the status line; unknown
  rejected.

---

## 9. Known limitations / notes

1. **Prompt‑only.** The limits are instructions the model follows, not hard truncation —
   a style nudges length/form, it doesn't guarantee it.
2. **Conflicting combos are allowed.** `/style коротко докладно` (one caps length, the other
   removes the cap) or `суть + поясни` send contradictory instructions; the model lands
   somewhere in between. There is no conflict guard yet.
3. **Per‑session, not persisted.** Each session starts at `normal`; the choice doesn't carry
   across restarts. (The future daily **mood** is the persistent, automatic sibling.)
4. **Single‑token names.** Multi‑word names would break spec parsing.
5. **Shapes form, never competence** — by design. A style never changes what Лілі knows, her
   canon, or her memory; it only re‑shapes the reply.

---

*Reflects the implementation as of the v0.2.x style work. When the style seam changes, update
this file alongside the code and [../tests/integration/test_styles.py](../tests/integration/test_styles.py).*
