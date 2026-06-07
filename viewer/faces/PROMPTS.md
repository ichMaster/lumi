# Лілі face pack — image prompts (v0.7, 27 portraits)

9 emotions × 3 intensity bands, all built on **one fixed identity** so it's the same
Лілі in every frame — only the **expression** changes.

**Filenames** (v0.7 viewer / EMOTION.md §7 resolver): mid = `<emotion>.png` (the default),
low = `<emotion>_low.png`, high = `<emotion>_high.png`. `calm.png` is the anchor.

---

## IDENTITY BLOCK — paste at the top of every prompt (keep IDENTICAL across all 27)

> Digital‑art portrait of **Лілі**, a young woman, neon‑noir cyberpunk style. **Split‑dyed
> hair** — vivid magenta‑pink on the left, deep cobalt blue on the right, slightly damp and
> tousled, falling loosely over her shoulders. **Large futuristic over‑ear headphones**, dark
> metallic finish with subtle teal accents. **Misty grey‑blue eyes**, long dark lashes, lightly
> defined brows; **light freckles** dusted across her nose and cheeks. **Black studded choker**,
> a dark geometric pendant necklace, and a **form‑fitting dark top with glowing neon‑pink
> circuit‑rune patterns**. Background: a rain‑soaked cyberpunk cityscape at night, blurred neon
> teal and cyan reflections from skyscrapers and holographic billboards, soft rain streaks in
> the air. Style: high‑detail digital painting, semi‑realistic, cinematic lighting, neon‑noir —
> Cyberpunk 2077 concept art meets League of Legends splash art. **Head‑and‑shoulders portrait,
> centered, square 768×768**; identical framing, identity, lighting and palette across the set.

**Assemble each image as:** `IDENTITY BLOCK` + `Expression: <the line below>`.
(Or, if you generate the calm anchor first, feed it as the reference and use the lines as
img2img expression‑only deltas.)

**Intensity** = expression strength only: `low` = subtle micro‑expression · `mid` = clear,
readable (default) · `high` = strong but natural, never a caricature.

---

## ANCHOR — `calm.png` (correction of your basis)

Your basis reads as *playful* (the smirk). For the calm anchor, **neutralize the expression**:

> Expression: relaxed and attentive; **drop the smirk/mischief** — mouth relaxed into the
> faintest closed‑lip smile; brows smooth and even; the misty grey‑blue gaze **soft but gently
> present on the viewer**, calm and at ease (dreamy, not sharp). This is the neutral anchor the
> other 26 are built from.

---

## calm
- `calm_low.png` — Expression: even stiller and more neutral than the anchor; features fully at rest, the faint smile nearly gone, gaze soft and distant. Almost no expression.
- `calm.png` — **the anchor (mid).**
- `calm_high.png` — Expression: deeper serenity; eyelids slightly lowered and relaxed, a soft warm closed‑lip smile, a peaceful, almost meditative ease — warmth, but not yet joy.

## joy
- `joy_low.png` — Expression: a small genuine closed‑lip smile, eyes a touch brighter and more focused, cheeks just beginning to lift.
- `joy.png` — Expression: an open warm smile (upper teeth showing), eyes crinkling slightly (Duchenne), cheeks raised, clearly happy.
- `joy_high.png` — Expression: a wide radiant grin, eyes sparkling and crinkled, cheeks high, brows lifted a touch, chin slightly up — openly delighted.

## playful  (this is your original smirk look)
- `playful_low.png` — Expression: a faint one‑sided smirk, a knowing glint in the eyes, lips closed.
- `playful.png` — Expression: a clear teasing half‑smile pulled to one side, one eyebrow slightly raised, mischievous sparkling eyes — *your basis expression.*
- `playful_high.png` — Expression: a cheeky lopsided grin, one brow arched high, eyes dancing with mischief, a slight playful head‑tilt (chin tucked, looking up a touch).

## tender
- `tender_low.png` — Expression: gaze softened, brows relaxed, a faint warm smile, a gentle melting in the misty eyes.
- `tender.png` — Expression: a soft loving look, gentle closed‑lip smile, warm caring eyes, a slight tender head‑tilt.
- `tender_high.png` — Expression: deeply affectionate; eyes warm and almost glistening with feeling, a tender emotional half‑smile, inner brows softly raised, very gentle and open.

## thoughtful
- `thoughtful_low.png` — Expression: eyes drifting slightly to the side, the misty gaze turned inward, lips neutral.
- `thoughtful.png` — Expression: gaze off to the side and a little up, a small contemplative furrow between the brows, lips lightly pressed in thought.
- `thoughtful_high.png` — Expression: deep contemplation; a distant, absorbed off‑camera gaze, a defined brow furrow, lips pressed — lost in thought.

## serious
- `serious_low.png` — Expression: the smile gone, a level steady gaze (now focused, not dreamy), calm but firm, lips relaxed and closed.
- `serious.png` — Expression: no smile, direct steady eye contact, jaw a little set, brows level — focused and present.
- `serious_high.png` — Expression: intense focus; a firm set mouth, brows slightly drawn together, a strong unwavering direct gaze.

## surprise
- `surprise_low.png` — Expression: eyes a little wider and sharper, brows slightly raised, lips just beginning to part.
- `surprise.png` — Expression: clearly wide eyes, raised brows, mouth open a little — caught off guard.
- `surprise_high.png` — Expression: very wide eyes, brows high, mouth open in an "oh", head pulled back a touch — fully startled.

## doubt
- `doubt_low.png` — Expression: one brow faintly raised, a small skeptical narrowing of the eyes, lips slightly tightened.
- `doubt.png` — Expression: a clear skeptical look; one eyebrow up, the other level, lips pursed to one side, eyes a little narrowed, a hint of a frown.
- `doubt_high.png` — Expression: strong skepticism; one brow high, a side‑eye glance, a pronounced pursed/downturned mouth — clearly unconvinced.

## sad
- `sad_low.png` — Expression: gaze lowered a little, inner brows just lifting, a faint downturn at the mouth, the eyes a touch heavier.
- `sad.png` — Expression: downcast eyes, inner brows raised (the "sadness brow"), mouth turned down, a quiet heaviness over the face.
- `sad_high.png` — Expression: deep sadness; eyes lowered and glistening as if near tears, inner brows strongly lifted and drawn, mouth downturned, head dipped slightly.
