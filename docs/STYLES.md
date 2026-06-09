# Answer styles — how Лілі shapes the *form* of a reply (implementation reference)

A **style** changes *how* Лілі answers — length, structure, expressiveness, register —
**never what she knows or how warm she is**. It's the form‑shaping sibling of the daily
**mood** (which colors tone automatically). This document describes the **implemented**
behavior; the design note lives in
[../specification/ARCHITECTURE.md](../specification/ARCHITECTURE.md) §Configuration.

> TL;DR — Styles are authored in [../core/styles.md](../core/styles.md). The **mega‑styles
> (each with a short description) are offered in the system prompt each turn and Лілі picks the
> one that fits** (the base styles stay authored but are no longer dumped into the prompt),
> writes in it, and **declares it as `<style>name</style>`** (parsed and
> stripped, like the emotion tag). **`/style <name>`** is a **soft recommendation** (she still
> decides); **`/style auto`** clears it. The status line shows her chosen style and **who
> picked it** — `(Лілі)`, or `(ти)` when she followed your recommendation. Per‑session.

---

## 1. The two kinds of style

| Kind | What it is | Example |
|---|---|---|
| **Base style** | one overlay — a concrete instruction with a length limit | `коротко` → "2–3 sentences, ≤40 words" |
| **Meta‑style** | a preset that expands to **several** base styles | `лагідна` → `поясни + просто + приклад` |

The default is **auto** — Лілі picks her own style each turn (preferring meta‑styles);
`auto`/`normal` just means *no recommendation*. Names are **Ukrainian**; base styles are
adverbs/nouns, meta‑styles are adjectives in Лілі's voice.

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
adjectives in Лілі's voice. Authored in [../core/styles.md](../core/styles.md) as a `= a, b, c`
alias line **plus a one‑line description** on the next line — and it's that **description** (not
the base list) that rides in the prompt's style palette, so it can be as detailed as you like.

| `/style` | expands to | the vibe |
|---|---|---|
| **`блискавична`** | `коротко` + `списком` | швидко, яскраво |
| **`лагідна`** | `поясни` + `просто` + `приклад` | веде лагідно |
| **`прискіплива`** | `докладно` + `порівняй` + `офіційно` | розкладає прискіпливо |
| **`завзята`** | `кроки` + `практично` + `невимушено` | береться завзято |
| **`лірична`** | `поетично` + `метафора` + `емоційно` | лірика, чуття |
| **`допитлива`** | `питанням` + `приклад` + `невимушено` | вертає питаннями |

---

## 4. Using `/style` (TUI) — a recommendation, not a switch

Лілі **picks her own style every turn** (see §5). `/style` only *recommends*:

| Command | Effect |
|---|---|
| `/style` | lists meta‑styles + base styles, her current pick, and your recommendation |
| `/style лагідна` | **recommend** a style — a soft hint she leans toward (she still decides) |
| `/style лірична невимушено` | recommend several (space‑, comma‑, or `+`‑separated) |
| `/style auto` (or `/style normal`) | clear the recommendation — she chooses freely |

Rules:
- **It's a hint, not a switch** — the recommendation rides in the prompt as *«Користувач
  радить: … — врахуй, якщо доречно; ти все одно вирішуєш.»* Лілі may follow it or not.
- **All‑or‑nothing** — if *any* name is unknown (`/style коротко xxx`), the recommendation is
  unchanged and you get `Unknown style in '…'`.
- **`auto`/`normal`/empty** clears it; duplicates are de‑duped; order is preserved.
- The **status line** shows her chosen style and **who picked it**: `style: лагідна (Лілі)`
  (her own), `style: коротко (ти)` (she followed your recommendation), or `style: авто` before
  her first reply (`· радиш: …` if you've recommended one).
- **Per‑session** — the recommendation and her last pick reset on `/new` and on restart.

---

## 5. How a style reaches the model — and comes back

Every turn, `Core._system_prompt(session)` ([../core/agent.py](../core/agent.py)) builds the
**palette** (the mega‑styles + their descriptions) and passes it as the **last** block of the system prompt,
framed by a header that asks Лілі to choose:

```
system = canon
       + …emotion instruction / ambient / memory / digest / mood…
       + ── STYLE_HEADER ──        ← "choose a style (prefer mega), write in it, declare it"
         Мега-стилі (обирай переважно з них) — кожен поєднує базові:
         - лагідна = поясни, просто, приклад
         …
         Базові стилі:
         - коротко: <text>
         …
         (Користувач радить: <recommendation> — врахуй, якщо доречно.)   ← only if set
messages = [ …live tail… ] + your new line
```

`build_system_prompt(…, style=…)` ([../core/prompt.py](../core/prompt.py)) appends
`f"{STYLE_HEADER}\n{style}"` at the very end (unchanged). The new `STYLE_HEADER` asks her to
**choose** the fitting style, **prefer mega‑styles**, write in it, and **declare** it.

She replies **in** the chosen style and tags it: `… <style>лагідна</style>`. `split_style`
parses the name, **strips the tag** (so it never shows), and `Core` records it as `last_style`
— which drives the status‑line «who». A mirror of the `<emotion>` channel.

> The palette is **prompt text only** — no code truncates the reply. Лілі follows the
> instruction; the limits are guidance, not hard enforcement.

---

## 6. How it's wired (code map)

| File | Responsibility |
|---|---|
| [../core/styles.md](../core/styles.md) | the authored styles — `## name` + body; `= a, b, c` for a meta‑style; `#` comments / category headers |
| [../core/styles.py](../core/styles.py) | `load_styles` (base, prose), `load_meta_styles` (`=` aliases), `_sections` parser, `DEFAULT_STYLE` |
| [../core/config.py](../core/config.py) | `styles_path` (default `core/styles.md`, env `LUMI_STYLES_PATH`) |
| [../core/agent.py](../core/agent.py) | `Core._styles`/`_meta`/`_recommendation`/`last_style`; `set_style` (recommendation; `auto`/`normal`/empty clears; all‑or‑nothing), `_style_directive` (builds the palette + recommendation), `style` (chosen + who) / `recommendation` / `style_names` / `base_names` / `meta_names`; `start_session` resets; `reply` parses `<style>`; `build_core` loads both |
| [../core/prompt.py](../core/prompt.py) | `STYLE_HEADER` (choose + declare), `split_style` (parse/strip the `<style>` tag), `build_system_prompt(…, style)` — appends the palette last |
| [../tui/app.py](../tui/app.py) | the `/style` command (`_style_command` — recommend / clear), the status‑line style + «who» display |

### The resolution flow

```
core/styles.md
   │  load_styles ─────────▶ Core._styles  (base: name → text)
   │  load_meta_styles ────▶ Core._meta    (meta: name → [base names])
   ▼
/style <spec>  →  set_style(spec)            (auto/normal/empty → clear)
                    validate ALL against base ∪ meta   (else reject)
                    → Core._recommendation = [tokens]   (per-session, a hint)

Core._system_prompt → _style_directive
                    → the full palette (every meta + base) + the recommendation
                    → build_system_prompt(style=…)  → STYLE_HEADER + palette (at the end)
                    → model

model reply "…<style>лагідна</style>"
                    → split_style → Core.last_style = "лагідна"   (tag stripped)
                    → status: «style: лагідна (Лілі | ти)»
```

`set_style` stores a soft recommendation (all‑or‑nothing validation); `auto`/`normal`/empty
clears it. `_style_directive` lists the whole palette so Лілі can choose. `style` returns her
last pick + who chose it — `(Лілі)`, or `(ти)` when it matches your recommendation — or `авто`
before her first reply.

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
- Meta alias members should reference **real** base style names — they're shown to Лілі as the
  meta's composition (`лагідна = поясни, просто, приклад`) so she knows what choosing it means.

Point at a different file with `LUMI_STYLES_PATH` in `.env`.

---

## 8. Tests

All in [../tests/integration/test_styles.py](../tests/integration/test_styles.py), against the
mock model (no paid calls):

- **loader** — the authored file yields the 16 Ukrainian base styles + the 6 meta‑styles;
  every meta expands to ≥2 real base styles; category headers never leak into a body; a
  missing file → `{}`.
- **`<style>` parser** — `split_style` extracts the declared name (lowercased) and strips the
  tag (and stray markers) from the reply.
- **auto‑style palette** — every turn the prompt offers the **whole palette** (all base texts +
  meta compositions) and asks her to choose (prefer mega) and declare it; no styles → no palette.
- **her choice + who** — a declared `<style>` is recorded as `last_style` and stripped from the
  reply; `style` shows `(Лілі)`, or `(ти)` when her pick matches the recommendation; `авто`
  before her first reply.
- **recommendation** — `/style <name>` puts a soft recommendation in the prompt; `auto`/`normal`/
  empty clears it; unknown is rejected (all‑or‑nothing); dedupe/order; per‑session reset.
- **prompt assembly** — `build_system_prompt` places the style block last with `STYLE_HEADER`.
- **TUI** — `/style` lists + shows who's choosing; `/style <name>` recommends and updates the
  status line; `/style auto` clears; unknown rejected.

---

## 9. Known limitations / notes

1. **Prompt‑only.** The limits are instructions Лілі follows, not hard truncation — a style
   nudges length/form, it doesn't guarantee it.
2. **She chooses, not you.** `/style` only *recommends*; Лілі may follow it or pick another.
   Watch the status «who» (`(Лілі)` vs `(ти)`) to see whether she took the hint. There is no
   way to force a style — that's the design (cf. her self‑emitted emotion).
3. **Per‑session, not persisted.** The recommendation and her last pick reset each session; they
   don't carry across restarts. (The daily **mood** is the persistent, automatic sibling.)
4. **Single‑token names.** A recommendation splits on spaces/commas/`+`, so each style name is
   one token.
5. **Shapes form, never competence** — by design. A style never changes what Лілі knows, her
   canon, or her memory; it only re‑shapes the reply.

---

*Reflects the implementation as of the v0.7.x auto‑style work. When the style seam changes, update
this file alongside the code and [../tests/integration/test_styles.py](../tests/integration/test_styles.py).*
