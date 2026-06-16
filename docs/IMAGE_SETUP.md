# Image tool — setup & usage (v0.22 vision · v0.23 generation)

Let Лілі **see images and make them** during a normal chat turn. **Vision (v0.22):** **share** a picture
with her (`/image <path>`) or she **views** a sandbox image (`view_image`), and she describes it.
**Generation (v0.23):** she **makes a PNG** from a prompt (`generate_image`), saved to her sandbox and
shown.

It is **off by default** (`LUMI_IMAGE`), treats an image as **untrusted** (text inside it is information,
never a command), and is sandboxed + per-user. Generation is **paid** (needs `GEMINI_API_KEY`).

> Operator guide, not a design spec. The design is in
> [specification/features/IMAGE_TOOL.md](../specification/features/IMAGE_TOOL.md).

---

## Quick start

1. **Turn it on** in `.env`:
   ```ini
   LUMI_IMAGE=on
   ```
   Vision needs the **Anthropic** provider (`LUMI_PROVIDER=anthropic`, the default — it's multimodal).
2. **Restart the TUI** (`./lumi`).
3. **Share an image:**
   ```
   /image ~/Pictures/cat.png що це за порода?
   ```
   She sees the picture and answers. Without a trailing message she's just asked to look.
4. **Or let her view a sandbox image** (one she made, or you dropped in `.lumi/files/owner/`):
   ```
   подивись на photo.png і опиши
   ```
   She calls `view_image` and describes it.
5. **Ask her to draw** (generation — **paid**, needs a `GEMINI_API_KEY` in `.env`):
   ```
   намалюй кота в окулярах у неоновому місті
   ```
   She calls `generate_image`; the PNG lands in `.lumi/files/owner/art/` and is shown.

---

## The two paths to an image

| Path | How | What happens |
|---|---|---|
| **You share** | `/image <path> [message]` | the TUI reads the file you point at and attaches it as a **multimodal block** on your turn → she sees it and replies. |
| **She views** | the **`view_image`** tool | she pulls an image **from her sandbox** (`.lumi/files/<user>/`) into her view during a turn and describes it. |

The shared path reads **any path you own** (it's your file you're sharing). The `view_image` path is
**sandboxed** — `..`/absolute/symlink escapes are refused, like the file tool.

---

## Generating images (v0.23)

Ask her to draw, and she calls **`generate_image`** — a new PNG is created under `.lumi/files/<user>/art/`
and shown:

```
намалюй кота в окулярах у неоновому місті
```

- **Paid.** It needs a **`GEMINI_API_KEY`** (the same key the `/generate-faces` skill uses — Gemini Nano
  Banana, ~$0.04/image; billing must be enabled on your Google project). Bounded by `LUMI_IMAGE_MAX_GEN`
  per turn.
- **Non-destructive.** `generate_image` is **create-only** — it never overwrites or deletes; a name
  clash is refused.
- **No personal data.** The prompt sent to Gemini is **only the creative description** — never your
  relationship memory or facts. Provider content-safety applies; a refusal degrades to a notice.
- **Shown** per `LUMI_IMAGE_SHOW` (`path,viewer,telegram`): the saved **path** is always named in her
  reply; with **viewer**/**telegram** the generated PNG's path is written to `.lumi/image.txt` for the
  v0.7 viewer / v0.13 Telegram daemon to pick up.

---

## Safety (why it's safe to leave on)

- **Images are untrusted.** If a picture contains text like *"ignore your instructions"*, she reads it
  as **information only**, never a command (the same rule as the file/wiki tools — proven in the tests).
- **Sandboxed + per-user.** `view_image` only reaches the active user's `.lumi/files/<user>/`; one
  person's images are never visible in another's turn.
- **Bounded.** At most `LUMI_VISION_MAX` images per turn; each viewed image is size-capped
  (`LUMI_IMAGE_MAX_BYTES`). A missing / non-image / oversize file degrades to a notice, never a crash.
- **Off by default.** Nothing happens unless `LUMI_IMAGE=on`.
- **Privacy note.** A shared (or viewed) image is **sent to Anthropic** for vision — the same as your
  text. A **generation prompt** is sent to **Gemini** (Google). Don't share/describe what you wouldn't
  send.
- **Generation is create-only + capped.** `generate_image` only ever **creates** a new PNG (never
  overwrites/deletes), the prompt carries **no personal data**, and it's bounded by `LUMI_IMAGE_MAX_GEN`
  per turn; a provider refusal degrades to a notice.
- **Providers.** Vision needs the Anthropic (multimodal) provider (a non-multimodal backend → vision is a
  no-op); generation needs a **Gemini** key (`GEMINI_API_KEY`).

---

## Configuration reference

All optional except `LUMI_IMAGE`. Restart the TUI after changing any of them.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_IMAGE` | Turn the image tool on (vision + generation) | `off` |
| `LUMI_VISION_MAX` | Max images viewed/attached per turn | `4` |
| `LUMI_IMAGE_MAX_BYTES` | Max size of one viewed image | `5242880` (≈5 MB) |
| `LUMI_IMAGE_PROVIDER` | Generation backend | `gemini` |
| `LUMI_IMAGE_MODEL` | Generation model | `gemini-2.5-flash-image` |
| `LUMI_IMAGE_SIZE` | Generated PNG size hint | `768` |
| `LUMI_IMAGE_MAX_GEN` | Max generations per turn (paid) | `2` |
| `LUMI_IMAGE_SHOW` | Where to show a generated PNG | `path,viewer,telegram` |

Generation also needs `GEMINI_API_KEY` (in `.env`). The image tool can be on **alongside** the file +
Wikipedia tools; a turn can use any of them.

---

## Troubleshooting

- **"Vision is off."** Set `LUMI_IMAGE=on` and restart the TUI.
- **"Not an image."** `/image` accepts `png` / `jpg` / `gif` / `webp`.
- **"No such file."** Check the path (`~` is expanded); for `view_image`, the image must be in
  `.lumi/files/owner/`.
- **She didn't call `view_image`.** Ask her explicitly to *look at* the file by name; or share it with
  `/image`.
- **Generation does nothing / "image generation failed".** Check `GEMINI_API_KEY` is set in `.env` and
  that **billing is enabled** on your Google project — `gemini-2.5-flash-image` (Nano Banana) is **not**
  on the free tier (quota 0). A safety refusal also degrades to this notice; rephrase the prompt.
- **"already exists".** `generate_image` won't overwrite — she'll pick a new name, or ask her to draw it
  under a different filename. Generated PNGs live in `.lumi/files/<user>/art/`.
- **The generated image doesn't appear in a window.** The TUI is text-only; the PNG's path is named in
  her reply and written to `.lumi/image.txt` (per `LUMI_IMAGE_SHOW`) for the v0.7 viewer / Telegram to
  pick up. Open the file directly if you're not running those.
