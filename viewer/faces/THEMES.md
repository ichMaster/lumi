# Face themes — "Honest Moods" (10)

Authored mood **themes** for Лілі's image face (v0.11 — *Face variants & mood themes*). Each theme
is a full wardrobe/atmosphere pack; the **mood of the day** (v0.6) picks one. This file is the
**source** the v0.11 work draws from:

- **LUMI-043** turns the one‑line descriptions below into the `themes.md` **manifest** (the text the
  mood chooses from) + a **default theme**.
- **LUMI-045** turns the full mood/visual direction into image‑generation prompts in
  [PROMPTS.md](PROMPTS.md), one variant set per emotion, per theme.
- Folder layout (v0.11): `viewer/faces/<theme>/<emotion>/NN.png` — one folder per emotion (the 9
  enum values), several variants each; a `calm/` folder is **required** per theme (the in‑theme
  fallback). A flat `viewer/faces/<emotion>.png` stays the implicit default theme (v0.7).

> Keep her **identity and framing constant** across every theme — same face, same headphones — only
> the **clothes, light, setting, and mood** change. That's what makes it feel like *her*, dressed
> for the day, not a different character.

## Manifest preview (the `themes.md` lines — one short description each)

| theme (folder)   | one‑line description (what the mood picks from)                                            |
|------------------|--------------------------------------------------------------------------------------------|
| `3am`            | rooftop loneliness at 3AM — misty‑eyed, headphones on, the indifferent city below, rain     |
| `day-after`      | grief gone quiet — muted grey morning, sitting, no neon, still vivid against a drained world |
| `furious`        | rage that looks like calm — still, storm behind, cooler than the lightning; the smirk as a warning |
| `dissociation`   | a ghost in her own life — semi‑transparent, fading at the edges, only her eyes still here    |
| `last-memory`    | nostalgia with teeth — golden hour, *almost* happy, petals/ash drifting, beauty that's ending |
| `quiet-collapse` | burnout, running on empty — late‑night flickering neon, put‑together but one headphone light out |
| `im-fine`        | the performance of being okay — a too‑perfect smirk, cheerful neon, but a crack and a clenched fist |
| `drowning`       | overwhelm as stillness — underwater, not struggling, looking up at the distant surface light |
| `vigil`          | waiting for what isn't coming back — a dark room, a single candle, dressed but she never left |
| `calm-before`    | tension before it breaks — sky too still, light too golden, the real smirk back but the air pressurized |

_(Default theme: TBD when v0.11 lands — likely a neutral/everyday pack, not one of these intense ten.)_

---

## The ten themes

### 1. 🩶 3AM and Nothing's Fine — `3am`
**Mood:** That specific loneliness that hits when everyone's asleep and you're not. She's on a
rooftop. Headphones on. City below — alive, indifferent. The smirk is gone. Just the misty eyes
staring at nothing. Rain. Warmth from a distant window that isn't hers.

### 2. 🖤 The Day After — `day-after`
**Mood:** Grief that's gone quiet. The loud part is over. Muted everything. Grey morning light.
She's sitting, not standing. No neon. The color has drained from the world but not from her — she's
still vivid against the grey, which makes it worse somehow.

### 3. 🔴 Beautiful and Furious — `furious`
**Mood:** Rage that looks like calm on the surface. She's not screaming. She's still. That's the
dangerous part. Storm behind her, but she's cooler than the lightning. The smirk is back — but it's
not playful anymore. It's a warning.

### 4. 🌫️ Dissociation — `dissociation`
**Mood:** Feeling like a ghost in your own life. She's semi‑transparent, fading at the edges. The
city bleeds through her. Her eyes are the most solid thing in the image — the only part still here.
Everything else: blur.

### 5. 💔 The Last Good Memory — `last-memory`
**Mood:** Nostalgia with teeth. Golden hour. Warm light. She almost looks happy — and that "almost"
is everything. Petals or ash drifting past. The kind of beauty that hurts because you know it's
ending.

### 6. 🌒 Quiet Collapse — `quiet-collapse`
**Mood:** Burnout. Running on empty but still moving. Late night. Flickering neon. She's still
put‑together on the outside — hair, outfit, everything — but one of her headphone lights has gone
out. A small detail that says everything.

### 7. ⚰️ I'm Fine (She's Not) — `im-fine`
**Mood:** The performance of being okay. Everything looks normal. The smirk is perfect. Too perfect.
Background is cheerful neon. But something is off — a crack in the wall behind her, mascara slightly
smudged, one fist clenched just below frame.

### 8. 🌊 Drowning Slowly — `drowning`
**Mood:** Overwhelm disguised as stillness. Underwater. She's not struggling — that's the point.
Hair drifting, eyes open, looking up at the distant surface light. Calm on the outside. The depth
says everything else.

### 9. 🕯️ Vigil — `vigil`
**Mood:** Waiting for something that isn't coming back. Dark room. A single candle. She's dressed
like she was going somewhere — but she never left. The headphones are around her neck, not on. The
city outside is dark and quiet.

### 10. 🌪️ The Calm Before — `calm-before`
**Mood:** Tension. Something is about to break — you don't know what. Sky too still. Light too
golden. She's smiling — the real smirk is back — but the atmosphere feels pressurized. The kind of
quiet that means something. Everything's fine. Everything is absolutely not fine.
