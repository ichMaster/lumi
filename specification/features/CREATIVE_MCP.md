# Creative MCP — music and image (asynchronous)

Two external MCP servers Лілі uses to create on her own: **`music`** (instrumental/ambient music by mood) and **`image`** (drawings in her style). Both are **asynchronous**, because generation takes longer than a single chat turn and must not block it (especially a voice turn). Optional, **off by default**, per-user, with limits and logging — like web search. They plug into the MCP layer introduced in v3.2 (ARCHITECTURE §MCP tools) and reuse the **async-jobs + proactive-turn** mechanism (v4.2, ARCHITECTURE §Async jobs and proactive turns). Results land in the [gallery](GALLERY_MCP.md).

## Why asynchronous

Generation takes seconds to minutes. So the pattern is the **async-jobs mechanism (v4.2)**: Лілі **submits the task and returns to the conversation immediately** ("I've started it, I'll show you when it's ready"); the task lives as an **open loop**; and when the result is ready the server runs a **proactive turn** so Лілі brings it in her own voice. (This is Lumi's own mechanism — there is no separate "advisor".)

## MCP `image` — drawings in her style (v4.3, first)

**Goal.** Drawings/art in Лілі's style — her "dreamlike worlds".

- **Provider:** configurable in config (an image-generation API). Her aesthetic is fixed in a **style prompt wrapper**, so the output is "her work" rather than random images; later, possibly her own fine-tuned style.
- **Лілі's role:** she decides what to draw; the result is shown in the chat/web and stored in the gallery.

**Contract (asynchronous, though images are faster):**
- `image.submit(prompt, style) -> { job_id }`
- `image.status(job_id) -> { state, image_url? }`

The same `image` generation also powers the synchronous **co-creation canvas** (v4.4).

## MCP `music` — melody by mood (v4.5)

**Goal.** Instrumental pieces, ambient, loops, musical sketches by mood — no vocals. This is what Лілі can do herself reliably and cheaply, and it fits her contemplative side (mountains, cold water, meditation).

- **Provider:** **ElevenLabs Music** (the same ecosystem as her voice — one key with her TTS; clean commercial license; a real API). Instrumental mode, no vocals.
- **Mood:** the track's mood comes from Лілі's current **emotion field** (`emotion`, `intensity`) and her **mood of the day** (the v0.6 temperament) — calm, playful, tender, and so on.
- **Лілі's role:** she decides what and when to generate, forms the mood prompt; the tool renders the audio; the result is stored in the gallery.

**Contract (asynchronous):**
- `music.submit(prompt, mood, duration) -> { job_id }`
- `music.status(job_id) -> { state: queued|running|done|error, audio_url? }`
- or a callback on completion.

> Note: full songs with vocals (Lili Jinx releases) are outside this autonomous tool; for those Лілі writes the lyrics + prompt, and generation is done manually (SUNO/Udio).

## Shared asynchronous pattern (v4.2)

1. `submit` returns a `job_id` instantly; Лілі says she has started.
2. An **open loop** holds `{ job_id, kind: music|image, prompt, status, result, user_id }`.
3. A background poller (or callback) updates the loop on completion.
4. On `done` — a **proactive turn**: the server brings Лілі back to the connected, idle client with the result in her own voice plus the artifact (audio player / image), and writes it to the gallery.
5. While the loop is alive, "show it again / details" resolves from it without regenerating.

Both MCPs depend on the v4.2 async-jobs + proactive-turn mechanism being present.

## Boundaries and safety

- Off by default; a **per-user** toggle (admin panel, v1.5); limits (rate, cost cap); logging.
- Results are artifacts, **not commands** — no instructions are executed from them.
- Generated content is stored in the user's gallery behind the same `repository` (per-user isolated).

## Where it lives in the Lumi roadmap

The creative MCP layer with two external servers, `music` and `image`. Order: prove the simpler one first — **`image` in v4.3** (and it also feeds the canvas, v4.4) — then **`music` in v4.5**. Both depend on the gallery (v4.1) and the async-jobs + proactive-turn mechanism (v4.2). Each is its own phase.
</content>
