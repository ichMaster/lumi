# Biorhythms — a computed layer of the daily mood

Лілі's daily temperament (v0.6) is a **horoscope** the model writes. Biorhythms add a second
layer that is the opposite kind of thing — **exact deterministic math** from her birth date —
and the two are **merged into one daily reading**. Lands as **v0.8**.

> Why computed, not model-written: a real-ephemeris test settled that the model can't compute
> astrology transits, so the v0.6 mood embraces *variation over precision*. Biorhythms are
> different — they're just sine waves, trivial and exact to compute — so the **core** computes
> them and hands the result to the mood call. Best of both: a precise input feeding a vivid reading.

## The cycles (classic three)

From the number of days since birth `d`, each cycle is `sin(2π · d / period)`:

| cycle | period | colors |
|---|---|---|
| **Physical** | 23 days | energy, vitality, stamina |
| **Emotional** | 28 days | mood, sensitivity, warmth |
| **Intellectual** | 33 days | focus, clarity, decisions |

Each yields a **value** in −1…+1 and a **phase label**:
- **high** (≳ +0.7) / **low** (≲ −0.7) — peak / trough;
- **rising** / **falling** — the sign of the slope between the extremes;
- **critical** — within a small band of a **zero-crossing** (the cycle is switching sign; the
  classic "unstable" day).

Pure function, no model, no I/O beyond reading the birth date — fully unit-tested.

## A fourth, phased cycle — the hormonal (menstrual) cycle

Beside the three sine biorhythms, Лілі carries a **hormonal cycle** — a **phased** (not sine)
~28-day rhythm: **менструація → фолікулярна → овуляція → лютеїнова → ПМС**. It's computed the same
way — deterministic, in code (`core/cycle.py`) — from an authored **anchor** (`Цикл: день 1 —
DD.MM.YYYY, довжина NN` in `core/natal.md`) + the injected clock: `day = (today − anchor) mod
length + 1`, mapped to a phase (ovulation ≈ `length − 14`, PMS = the last ~5 days). Each phase
carries a short temperament note (e.g. ПМС → heightened sensitivity/irritability/tiredness;
ovulation → peak energy, warmth, sociability).

It is **merged into the same daily mood call** alongside the biorhythms, under the shared
"integrate these computed body rhythms" directive — coloring her **tone, energy and sensitivity,
never her competence**. Part of her **embodied self-image** (inner, like her inner life), not a
medical claim. On by default (`LUMI_CYCLE`); off / no anchor → simply omitted. `/biorhythm` shows
the current phase alongside the cycles.

## Merged with the astrology forecast (v0.6)

The biorhythm state is **another input to the daily mood call**, exactly like world context
(v3.3) will be — so the daily **reading + resolution blend the horoscope and the biorhythms** into
one temperament:

- *"emotional cycle low + a tense transit → quiet, thin-skinned, wants gentleness";*
- *"physical high + intellectual critical → restless energy but scattered focus".*

Everything else about the v0.6 mood is unchanged:
- computed **once per local day** (the v0.4 injected clock), cached, recomputed at local midnight;
- the **full reading is logged**, only the **resolution** is injected as the prominent block;
- it **biases the emotion she emits and her tone/energy — never her competence** (the mood's hard
  rule). A bad biorhythm day never makes her less capable or less willing to help.

## Computation

```
d = (today.date() − birth_date).days          # today from the injected clock
physical      = sin(2π · d / 23)
emotional     = sin(2π · d / 28)
intellectual  = sin(2π · d / 33)
```

The **birth date** comes from `core/natal.md` (one Лілі — global, not per-user; the same source
the horoscope uses). Deterministic: same date → same values, so tests pin exact numbers and
critical days against a fixed clock.

## Surfacing

- A `/biorhythm` command shows today's three cycles (value + label).
- The merged daily temperament is shown by `/mood` (the resolution) — the horoscope and biorhythms
  read as one voice, not two separate blocks.

## Contract

- No new stored record and no change to the emotion contract — biorhythms are a **computed input
  to the v0.6 mood**. The mood's logging/injection/“never competence” rules carry over unchanged.
- Updates **ARCHITECTURE §Mood and temperament** (the biorhythm input + merge).

## Mapping to the roadmap

**v0.8 — Biorhythms (merged into the daily mood)**, right after the v0.7 viewer. Depends on
**v0.6** (the mood it merges into), **v0.4** (the clock), and the natal birth date. An experiment
for daily variation, not a scientific claim — same spirit as the horoscope.
