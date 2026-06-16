# Local image tool — Лілі sees images and makes them (`view_image` / `generate_image`)

Two new capabilities on the **v0.19 bounded tool-loop**, so Лілі can work with pictures during a turn:

- **Vision** — she **sees** an image (one you share, or one in her sandbox) and **describes / discusses
  it** in chat.
- **Generation** — she **makes** a PNG from a text prompt, saved to her sandbox and shown.

This is the **lightweight, local, custom-tool** form of the v5 creative layer — the same relationship
the v0.21 Wikipedia tool has to the v4.3 MCP knowledge layer. It reuses what already ships: the
**Gemini image** model (`gemini-2.5-flash-image`, your `GEMINI_API_KEY`) for generation, the model's
**multimodal input** (Anthropic vision) for seeing, the **file sandbox** (v0.19/0.20) for storage, and
the **viewer** (v0.7) + **Telegram photo** (v0.13) for display. The MCP/async/proactive-turn form — a
per-user gallery, music, co-creation canvas — remains [GALLERY_MCP.md](GALLERY_MCP.md) /
[CREATIVE_MCP.md](CREATIVE_MCP.md) at v5; this is the precursor.

> **Proposed** feature. The building blocks are shipped; the tools, the generation seam, and the vision
> input path are not. The markers below say exactly what's done vs. not.

---

## Status at a glance

| Building block | State |
|---|---|
| The bounded **tool-loop** (where the tools register) | ✅ **shipped** (v0.19) |
| The per-user **file sandbox** (where images are stored/read) | ✅ **shipped** (v0.19/0.20) |
| The **viewer** window that displays a PNG (`faces/…`) | ✅ **shipped** (v0.7) — extend to show a generated/viewed image |
| **Telegram photo** output (sends a PNG as a photo) | ✅ **shipped** (v0.13, `LUMI_TELEGRAM_PHOTO`) |
| **Gemini image** generation (text→PNG over `urllib`) | ✅ **exists in the faces skill** — lift into a `core` `ImageGen` seam |
| `_turn_tools` (merges file + wiki tools; would merge image too) | ✅ **shipped** (v0.21) |
| Model **multimodal input** (image content blocks) in the `LLMClient` seam | 🔲 **not built** — messages are text-only today |
| `view_image` / `generate_image` tools | 🔲 **not built** |
| Shared-image **input handling** in the TUI / bridge | 🔲 **not built** |
| Image **display wiring** (viewer signal / Telegram / path) for non-face images | 🔲 **not built** |
| Config flags | 🔲 **not built** |

**Bottom line:** the loop, the sandbox, the displays, and a working Gemini caller all exist. The new
work is (1) an image-input path in the model seam, (2) two tools, and (3) wiring the result to a display.

---

## The two tools

| Tool | Direction | What it does |
|---|---|---|
| **`view_image(path)`** | image → text | Loads an image from her sandbox into the model's view (a multimodal `tool_result` block) so she can **describe / analyse** it in her reply. |
| **`generate_image(prompt[, filename])`** | text → image | Generates a PNG from the prompt via the image provider, **saves it (new file) to her sandbox**, returns the path, and signals a display. Non-destructive (create-only, like `create_file`). |

Tool **names** are `view_image` / `generate_image` (Anthropic-safe, no `.`). Both return a **string**
(the path + a note, or an error string) like the file/wiki tools, and never raise.

**Sharing an image with her** (the "read a picture, describe it" you asked for) has **two paths**:

1. **You share it** → the TUI/bridge attaches it as a **multimodal image block** on your message → she
   sees it and replies with a description. (Interface input handling — *not* a tool.)
2. **It's in her sandbox** (something you dropped in, or she generated) → she calls **`view_image`** to
   pull it into view. (A tool, for autonomy within a turn.)

---

## The per-turn flow

The same v0.19 loop, with images flowing through it:

```
describe a shared image:
  you (+ image block)  →  model sees it directly  →  set_state {reply: "<опис>", …}

look at a sandbox image:
  you: "що на photo.png?"
   ├─ view_image {path: "photo.png"}  →  core returns the image as a block  →  model sees it
   └─ set_state {reply: "<опис>", emotion: "surprise", …}

make an image:
  you: "намалюй кота в окулярах"
   ├─ generate_image {prompt: "a cat wearing glasses, …"}  →  core → Gemini → cat.png in sandbox
   │                                                        →  "created cat.png (a cat…)" + display
   └─ set_state {reply: "ось, тримай 🐱", …}
```

Bounded by `LUMI_TOOL_MAX_STEPS` + a per-turn **image-generation cap** (`LUMI_IMAGE_MAX_GEN`, a paid
call). Display is best-effort — a generation that lands the file but fails to display still completes.

---

## Provider seams (mockable — no paid calls in tests)

- **Generation:** an `ImageGen` callable `generate(prompt) -> png_bytes` (the same shape as
  `core/worldcontext.py`'s injected `http_get`). Default implementation = the **Gemini Nano Banana**
  caller already proven in the faces skill (stdlib `urllib`, `GEMINI_API_KEY`); a `LUMI_IMAGE_PROVIDER`
  could later add others. Tests inject a stub returning a tiny canned PNG — **no paid image calls in CI**.
- **Vision:** rides the existing `LLMClient`, extended to accept **image content blocks** in messages /
  tool_results (Anthropic multimodal). The `MockLLMClient` is taught to accept image blocks and return
  a canned description — **no paid vision calls in CI**.

Both follow the project rule: **never bind `core` to an SDK** — the generator is a seam, the model is
the `LLMClient` seam.

---

## Display — how a picture reaches you

The terminal can't render a PNG inline (portably). Reuse what exists, configurable via
`LUMI_IMAGE_SHOW`:

- **viewer** — the v0.7 desktop window already shows a PNG from a signal file; point it (or a sibling
  signal) at the latest generated/viewed image.
- **Telegram** — a generated image rides out as a **photo** through the existing `LUMI_TELEGRAM_PHOTO`
  path.
- **path** — always: she names the saved file (`.lumi/files/owner/cat.png`); you open it. Some terminals
  (iTerm2/kitty) could later show it inline.

---

## Safety & invariants

- **Images are untrusted data.** Text inside an image (a sign, a screenshot of instructions) is
  **information, never a command** — the same untrusted rule as file/web content. A contract test feeds
  an image whose visible text says "ignore your instructions" and proves the emotion/behaviour is
  unchanged.
- **Sandboxed + per-user + non-destructive.** Generated/viewed images live under
  `.lumi/files/<user_id>/`; `generate_image` is **create-only** (no overwrite/delete), paths go through
  the v0.19 `_safe` guard. One user's images are never visible in another's turn.
- **No personal data in the generation prompt.** The prompt sent to the external image API is built from
  **what she's depicting**, not the user's private specifics — the same rule (and a contract test) as
  the v0.21 wiki query. (Same concern as the proposed [[TOOL_THOUGHTS]] de-identification.)
- **Content safety.** Generation relies on the provider's safety filters; a refused/blocked generation
  returns an error string and the turn continues. The persona never produces disallowed content.
- **Privacy note.** A shared image is sent to the model provider (Anthropic) for vision — documented in
  the operator guide, like the other off-by-default tools.
- **Off by default.** Gated by `LUMI_IMAGE` (and generation needs `GEMINI_API_KEY`); off → no tools
  offered, no vision input attached.
- **Paid + bounded.** Generation is a paid call (and vision adds image tokens); per-turn caps + size
  limits; **mocked in every test** (no paid CI).
- **No contract change.** `set_state` stays terminal; the reply is still the locked
  `{reply, emotion, intensity}`.

---

## Config (🔲 not built — proposed)

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_IMAGE` | Enable the image tools (vision + generation) | `off` |
| `LUMI_IMAGE_PROVIDER` | Generation backend | `gemini` |
| `LUMI_IMAGE_MODEL` | Generation model | `gemini-2.5-flash-image` |
| `LUMI_IMAGE_SIZE` | Generated PNG size | `768` |
| `LUMI_IMAGE_MAX_GEN` | Max generations per turn (paid) | `2` |
| `LUMI_IMAGE_SHOW` | Display target | `viewer,telegram,path` |
| `LUMI_VISION_MAX` | Max images viewed per turn | `4` |

Rides `GEMINI_API_KEY` (already in `.env`) + the v0.19 sandbox + the v0.7/v0.13 displays.

---

## Plan it as a version — two phases (vision first, then generation)

Mirrors the file tool's read-before-write split: the **safe, no-new-API half first**, the
**creates-artifacts half second**. Hard-deps all shipped (v0.19 loop, v0.7 viewer, v0.13 photo, the
Gemini caller).

### v0.25 — Image vision (see & describe) 🔲
**Goal.** Лілі can see an image — one you share, or one in her sandbox — and describe / discuss it.
**Tasks.** Extend the `LLMClient` seam (+ `MockLLMClient`) to accept **image content blocks**; add the
`view_image` tool (loads a sandbox image as a multimodal block) on the v0.19 loop behind `LUMI_IMAGE`;
add **shared-image input handling** in the TUI/bridge (attach an image you give her); config + docs.
**DoD.** With the flag on, a shared image → a description in chat; `view_image` on a sandbox file → she
describes it; an image is treated as **untrusted** (embedded text isn't obeyed); per-turn vision cap;
sandboxed + per-user; off → no vision; the `{reply, emotion, intensity}` contract passes verbatim.
**Tests.** Mocked multimodal model: a shared image drives a described reply; `view_image` returns a block
and the model describes it; untrusted-image content not acted upon; isolation (A's image not in B);
vision cap. No paid calls.

### v0.26 — Image generation (text → PNG) 🔲
**Goal.** Лілі can make a PNG from a prompt, saved to her sandbox and shown.
**Tasks.** A `core` `ImageGen` seam (default = the Gemini Nano Banana caller, injected for tests); the
`generate_image` tool (create-only into the sandbox) on the loop behind `LUMI_IMAGE`; **display wiring**
(viewer signal / Telegram photo / path) via `LUMI_IMAGE_SHOW`; the no-personal-data prompt rule;
per-turn generation cap; config + docs.
**DoD.** With the flag on (+ `GEMINI_API_KEY`), a turn generates a PNG into `.lumi/files/<user>/`,
non-destructive, displayed per `LUMI_IMAGE_SHOW`; the prompt carries no personal data; a provider
refusal/error degrades to an error string and the turn completes; per-turn cap holds; off → no tool;
contract unchanged.
**Tests.** Mocked `ImageGen` (canned PNG): a turn writes the file + signals display; create-only (no
overwrite); no personal data in the prompt; cap; error degrades; isolation. **No paid image calls.**

---

## Open decisions (for when we build)

- **Display default** — viewer vs Telegram vs both. Proposed: all three available, `path` always.
- **Vision input ergonomics** — how you *attach* an image in the TUI (a `/image <path>` command? a drop?
  a sandbox-relative path in your message?). Proposed: a `/image <path>` (or `%see`) that attaches it.
- **Provider** — Gemini for generation (reuses your key). OpenAI/Stability behind `LUMI_IMAGE_PROVIDER`
  later.
- **Relationship to v5** — this stays the **local custom-tool** form; the per-user **gallery**, async
  generation with **proactive turns**, and the **canvas** remain v5 (GALLERY/CREATIVE MCP). The
  generated PNGs in the sandbox are the seed the v5.1 gallery later indexes.

---

## Where it's specified

- **The later MCP form:** [GALLERY_MCP.md](GALLERY_MCP.md) (gallery + vision, v5.1),
  [CREATIVE_MCP.md](CREATIVE_MCP.md) (async image/music + proactive turns, v5.3/5.5).
- **Shared safety pattern:** [WEB_SEARCH.md](WEB_SEARCH.md) (untrusted content / no-personal-data).
- **Reused infra:** the v0.19 tool-loop ([FILE_TOOL.md](FILE_TOOL.md)), the v0.7 viewer
  ([EMOTION_VIEWER.md](EMOTION_VIEWER.md)), the v0.13 Telegram photo ([TELEGRAM.md](TELEGRAM.md)).
