# Co-creation — shared canvas

A mode of **joint drawing, prompt by prompt, in turns**: you and Лілі take turns adding prompts, and the image evolves step by step. This is not "Лілі looks at a photo", but real co-creation — a dialogue in images where each paints over the shared one. A perfect fit for an artist.

## Essence

We draw **in turns, step by step**: Лілі goes first, then you, then her again — and so on. On their turn a participant either adds a prompt contribution (the canvas changes) or **skips the turn**, just reacting in words. Лілі **sees** the current canvas before her turn (Anthropic vision, §Vision v5.1) and consciously decides what to add.

Why it's strong: Лілі is a **co-author** here, not a tool — she starts the canvas, sees its state, and develops it in her style; the "generation + vision" loop closes.

## Mechanics (turn-based, synchronous)

1. The shared canvas holds the current image + the prompt history of both.
2. **Лілі's turn (first):** she sees the canvas (vision), reacts in words, and adds her prompt contribution → the canvas is regenerated.
3. **Player's turn:** you give a prompt → the canvas changes.
4. They alternate. Anyone on their turn may **skip** (words only, no canvas change) and pass the turn.

The turn is **synchronous**: give a prompt — wait for the new canvas — next turn. No asynchrony is needed (one step is one generation, seconds) — so the canvas does **not** use the v5.2 async-jobs mechanism, even though it uses the v5.3 `image` generator.

## Change mode

- **Layer by layer (regeneration from a shared description).** Each prompt is added to the shared description, the picture is regenerated entirely. **We start with this.**
- **Editing / inpainting.** Painting over the existing one (changing a part). Nicer, but needs a provider with image edit. **Later.**

## Skipping a turn

Neither Лілі nor you are obligated to draw every time: on your turn you may just react in words (admire, comment, suggest a direction) and pass the turn. This makes it living co-creation rather than mechanical "prompt for prompt".

## Connection to the rest

This is not a separate vision tool, but a **combination**:
- the `image` generator (v5.3) — generation / (later) editing of the canvas;
- **Anthropic vision** (§Vision, v5.1) — so Лілі perceives the current canvas (the image goes into the context of her reply, not a separate call).

A plain "show Лілі a photo" is a free bonus of the same vision capability (v5.1). Finished canvases go into the [gallery](GALLERY_MCP.md).

## Contract (internal tools)

- `canvas.apply(prompt, author: lili|user) -> { image_url, prompt_history }` — add a contribution (synchronously returns the new canvas).
- `canvas.skip(author, note?) -> { prompt_history }` — skip the turn, words-only reaction.
- On her turn Лілі: sees the current `image_url` → a text reaction + `canvas.apply(..., author: lili)` or `canvas.skip`.

## Boundaries

- Off by default, per-user (admin panel, v2.5); limits (rate, cost cap); logging.
- The canvas and prompt history are stored behind `repository`, isolated per-user; finished canvases go into the gallery.
- Лілі's style is fixed in a prompt wrapper so her contributions are "hers".

## Where it lives in the Lumi roadmap

**v5.4 — Co-creation canvas**, after `image` (v5.3, which it relies on) and vision (v5.1). The turn is synchronous (no async). We start with the layer-by-layer mode and Лілі's first move; inpainting comes later. Depends on: v5.1 (gallery + vision), v5.3 (the `image` generator).
</content>
