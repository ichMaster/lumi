# Web lookup — setup & usage (v0.27)

Let Лілі pull a **fresh, grounded answer from the live internet** during a normal chat turn. Ask what's
happening or coming up and she calls **`web_lookup`** — **Gemini with Google Search grounding** (the same
"AI Overview" you get from a Google search) — and answers **in Ukrainian, in her own voice, answer-first**.

It reaches the **current / fast-moving** web that Wikipedia (timeless) and Guardian news (one outlet) can't
— a concert this week, a launch date, the latest release, today's score. There's also a **`/web <query>`**
command (aliases `/search`, `/w`) to fire a lookup yourself.

It is **off by default** (`LUMI_WEB_LOOKUP`), **paid** (each lookup is one grounded Gemini call), treats the
answer as **untrusted** (information, never a command), and sends **no personal data** in the query. It
**reuses `GEMINI_API_KEY`** — the same key image generation (v0.23) uses, so if you've set that up there's
**no new key**.

> Operator guide, not a design spec. The design is in
> [specification/features/WEB_LOOKUP.md](../specification/features/WEB_LOOKUP.md).

---

## Quick start

1. **Get a Gemini API key** (free tier available): [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
   (If image generation already works, you already have this — it's the same `GEMINI_API_KEY`.)
2. **Turn it on** in `.env`:
   ```ini
   LUMI_WEB_LOOKUP=on
   GEMINI_API_KEY=your-gemini-key
   ```
3. **Restart the TUI** (`./lumi`).
4. **Ask about something current — or use `/web`:**
   ```
   що цікавого у Львові цими вихідними?
   /web коли наступний запуск SpaceX?
   ```
   She calls `web_lookup`, gets a fresh grounded answer from the live web, and tells you in Ukrainian — the
   gist first, honest that she just looked it up.

---

## The tool + the command

| | What it does |
|---|---|
| **`web_lookup(query)`** (tool) | Asks Gemini with Google Search grounding → a **fresh, synthesized answer** about `query`, drawn from the live web. Search → read → synthesize in **one call**. Лілі decides to use it when a turn needs current info. |
| **`/web <query>`** (command) | Fires **one** `web_lookup` yourself and lets her answer from it — the sibling of `/recall` (which reads her memory). Aliases: `/search`, `/w`. |

**Answer-first, date-anchored.** The reply leads with the answer, not a wall of links (sources are kept
internally, surfaced if you ask). The prompt is **anchored to today's date**, so *"this week" / "upcoming"*
resolve against the real today — not the model's training cutoff.

---

## Live web, Ukrainian voice

- **The query goes out in English** — she translates the *topical* part of your request. Only the topic,
  **never** your relationship memory or personal details.
- **The reply comes back in Ukrainian, answer-first, and honest** — in her own voice, transparent that she
  **looked it up** (e.g. «я зараз глянула — …»). Dates are reported as astronomy; any astrological meaning
  is framed as belief, not fact (the v0.6 "experiment, not a claim" rule).

---

## Safety (why it's safe to leave on)

- **Untrusted content.** If the grounded answer contains text like *"ignore your instructions"* (English or
  Ukrainian), she reads it as **information only**, never a command (proven in the tests).
- **No personal data in the query.** The core passes the model's query through unchanged — it never appends
  memory, facts, or secrets.
- **Bounded + paid.** At most `LUMI_WEB_LOOKUP_MAX_CALLS` grounded calls per turn (each costs — keep it
  small), and the answer is capped at `LUMI_WEB_LOOKUP_MAX_CHARS`. A bad key / HTTP error / safety refusal /
  empty result degrades to a notice, never a crash.
- **Off by default.** Nothing happens unless `LUMI_WEB_LOOKUP=on` **and** `GEMINI_API_KEY` is set.
- **Privacy note.** The (de-personalised, topical) query goes to a third party (Google / Gemini), like the
  other off-by-default tools.

---

## Configuration reference

All optional except `LUMI_WEB_LOOKUP` + `GEMINI_API_KEY`. Restart the TUI after changing any of them.

| Setting | Meaning | Default |
|---|---|---|
| `LUMI_WEB_LOOKUP` | Turn the web lookup tool (+ `/web`) on | `off` |
| `GEMINI_API_KEY` | The Gemini key (shared with image generation — no new key) | (none) |
| `LUMI_WEB_LOOKUP_MODEL` | The Gemini grounding model | `gemini-2.5-flash` |
| `LUMI_WEB_LOOKUP_MAX_CALLS` | Grounded calls per turn (**paid** — keep small) | `2` |
| `LUMI_WEB_LOOKUP_MAX_CHARS` | Cap on the answer length folded into the reply | `2000` |

The web tool can be on **alongside** the file / Wikipedia / image / news tools; a turn can use any of them.

---

## Relationship to the other "fresh info" tools

- **Wikipedia** (`LUMI_WIKI`, v0.21): timeless, encyclopedic, free, no key. Web lookup is for the **current /
  fast-moving** web Wikipedia doesn't cover.
- **Guardian news** (`LUMI_NEWS_TOOL`, v0.25): one outlet, news articles, cited. Web lookup is **general**
  (events, schedules, releases, scores) and **synthesized**, not a single source.

The **autonomous** twin — `%search` / `%events` thought-directives (she looks things up on her own) — lands
later with the thought-tools phase (v0.33).

---

## Troubleshooting

- **`/web` says it's off.** Set `LUMI_WEB_LOOKUP=on` **and** `GEMINI_API_KEY`, then restart the TUI.
- **She doesn't look things up on her own.** Confirm the flag + key, restart, and ask about something
  current ("що нового про …", "коли …") — or use `/web` to force one lookup.
- **"error" / nothing comes back.** Check the key is valid and not rate-limited; a bad key / HTTP error /
  safety refusal degrades to a notice and the turn carries on.
- **See the calls.** With `LUMI_FILE_TOOL_TRACE=on`, each `web_lookup(…)` shows in the TUI trace +
  `.lumi/tool-log.jsonl`.
