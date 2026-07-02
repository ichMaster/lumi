# Models & providers — setup guide (v0.18)

How to switch Лілі to a different model or provider. From **v0.18** the model lives behind one
`LLMClient` seam, so changing it is a **config-only** switch — no code change. Supported:

- **Anthropic** — Claude tiers (Opus / Sonnet / Haiku) — the default
- **OpenAI** — GPT models
- **DeepSeek** — DeepSeek-V3 / reasoner (OpenAI-compatible)
- **MiniMax** — its chat API
- **local** — any OpenAI-compatible local server (Ollama, LM Studio)

> This is an operator guide, not a design spec. The design lives in [ROADMAP.md §v0.18](../specification/ROADMAP.md)
> and the issue breakdown in [v0.18-issues.md](../specification/roadmap/implementation/v0.18-issues.md).

---

## How switching works (the 3 lines)

In `.env` set three things, then **restart the TUI** (`./lumi`). Only **one** provider is active at a
time — whatever `LUMI_PROVIDER` says.

```ini
LUMI_PROVIDER=<provider>     # which backend
LUMI_MODEL=<model id>        # which model on that backend
<PROVIDER>_API_KEY=<key>     # that provider's key
```

The status bar shows the active model, so you can confirm the switch.

**Switch at runtime — no restart (v0.37).** Keep **both** keys in `.env` and flip engines mid-session with
the **`/model`** TUI command:

- `/model` — print the active engine + the available aliases.
- `/model opus` / `/model gpt-5.5` — swap to a configured alias (Opus 4.8 ↔ GPT-5.5), ideal for an A/B on the
  same conversation. `/model openai:gpt-4o` — an explicit `provider:model` for an id without an alias.

Aliases come from config (`LUMI_MODEL_ALIASES=opus=anthropic:claude-opus-4-8,gpt-5.5=openai:gpt-5.5`, with
sensible defaults). The switch is **not** persisted — the next restart uses the `.env` default; the new
engine starts on a cold prompt cache (a one-off first turn). An unknown alias or a missing key is rejected
with a clear message and leaves the current engine in place.

**Two things true for every non-Anthropic provider:**

- **`thinking` / prompt-cache are Anthropic-only.** They are silently ignored elsewhere (no error) — so
  the thinking box stays empty and there's no cache discount. **`effort` is the exception:** as of **v0.37**
  `LUMI_EFFORT` is honored on **OpenAI / DeepSeek** too (mapped to `reasoning_effort` low|medium|high; Lumi's
  `xhigh`/`max` clamp to `high`) — it tunes a GPT-5 / DeepSeek-reasoner's depth, though the reasoning itself
  stays hidden. Still ignored on MiniMax.
- Лілі's persona is **Ukrainian.** Pick a model that handles Ukrainian well (the cloud providers below
  all do; small local models are weaker).

---

## Risks of switching — what you trade away

Лілі was built and tuned on Claude Opus 4.8. Every switch is **reversible** (see *Switching back* below) and
mechanically safe — nothing is corrupted — but each one trades something away. The cross-cutting losses on
**any non-Anthropic provider**:

- **The tool-loop.** As of **v0.37** the OpenAI-compatible adapter (**OpenAI / DeepSeek / local**) has its
  own bounded tool-loop via OpenAI **function calling**, so the **file, Wikipedia, news, web-lookup, image,
  and journal tools** — and the `%`-thought-tools that ride them — **work** on GPT-5.5 / DeepSeek-V4-Pro and
  OpenAI-compatible local servers. **MiniMax** still has no tool-loop (its tools are silently ignored — a
  single plain call). If you depend on tools, stay on Anthropic or an OpenAI-compatible provider.
- **Inner monologue / think box.** On most providers the hidden think-step (v1.3 inner monologue) is empty.
  **Exception:** OpenAI **reasoning models on the Responses API** (`gpt-5.5`, o-series — v0.37) return a
  **reasoning summary** that *does* populate the think-box, and `LUMI_EFFORT` tunes its depth. DeepSeek's
  reasoner reasons but doesn't surface it (Lumi uses Chat Completions there); MiniMax/local have no think box.
- **No prompt caching → more cost + latency.** Лілі's large static prefix (canon + memory digests + mood) is
  cached on Anthropic and re-sent **uncached on every turn** elsewhere — each turn re-bills the whole prompt
  at full input price.
- **The emotion channel leans on a weaker path.** Anthropic emits `{reply, emotion, intensity}` as a tool
  call; the other providers fake it through JSON mode. The v0.3 gate still catches bad output (unknown
  emotion → `calm`, intensity clamped), so a turn never crashes — but weaker models trip it more often, so
  the emotion (and her face) flattens toward `calm`.
- **Ukrainian + persona depth.** Her canon and voice are Ukrainian; as you leave Opus 4.8, capability and
  Ukrainian fluency drop, so canon adherence and the layered states (mood / closeness / needs) get shallower.
- **Data leaves differently.** Each cloud provider sees the conversation under its own data policy; the cost
  report estimates non-Claude pricing (unknown models fall back to an Opus-tier estimate, so the figure may
  be off).

Per-provider specifics are in each section below; *Switching back to Opus 4.8* covers the round trip.

---

## 1. Anthropic — Claude tiers (the default)

Already working. Change only the model id to switch tier:

```ini
LUMI_PROVIDER=anthropic         # or leave unset — anthropic is the default
LUMI_MODEL=claude-opus-4-8      # or claude-sonnet-4-6 (cheaper) / claude-haiku-4-5 (cheapest)
ANTHROPIC_API_KEY=sk-ant-...
```

- No install needed.
- Anthropic is the **only provider** with extended thinking, `effort`, and prompt caching — but within it,
  **Haiku 4.5 is the exception**: no extended thinking and no `effort` (the think box stays empty on Haiku),
  though it keeps prompt caching.
- Restart `./lumi`.

**Risks (staying on Claude) — the lowest-risk switches.** **Sonnet 4.6** keeps everything Opus has — 1M
context, thinking, `effort`, caching, **and the tool-loop** — at roughly ⅗ the price; you mainly lose some
depth of persona and literary nuance. **Haiku 4.5** is cheapest and fastest but the biggest step down: a
**200K context window** (vs 1M — a long history + RAG + tool results can overflow it), no thinking/`effort`,
and weaker adherence to the canon and the structured emotion field (more `calm` fallbacks). Tier down for
cost/speed, not for the fullest Лілі.

### The three-tier dial (`/model opus ⇄ sonnet ⇄ haiku`) + per-operation routing (v0.40)

**Swap the reply tier mid-session, no restart** — the built-in aliases resolve to the Claude tiers:

```
/model            → show the active engine + the configured aliases
/model opus       → quality (the default voice)
/model sonnet     → balanced (~⅗ the price)
/model haiku      → cheapest — *speak on Haiku* to save most
```

The swap re-points the **reply** model for this run only (nothing persists — the next start uses
`LUMI_MODEL`), the status bar reflects it, and the new tier starts on a cold prompt cache (one-off).
A tier swap is always an **explicit, reversible choice** — Lumi never downgrades the reply on its own.
A **bare full id** also works — `/model claude-haiku-4-5-20251001`, `/model gpt-5.5-mini` — the
provider is inferred by prefix (`claude-*`/`gpt-*`/`o*`/`gemini-*`/`deepseek-*`); anything else still
needs `provider:id` (v0.41).

**Per-operation routing (`LUMI_MODEL_*`, off by default).** Independently of the dial, the internal
operations can run on cheaper tiers while the visible reply stays on `LUMI_MODEL`:

```ini
LUMI_MODEL_THINK=claude-sonnet-4-6         # the thought stream (%think and friends)
LUMI_MODEL_MOOD=claude-sonnet-4-6          # the daily mood call
LUMI_MODEL_HOUSEKEEPING=claude-haiku-4-5   # session summaries, facts, compaction
```

Unset → everything runs on `LUMI_MODEL` (byte-identical). The two **compose**: `/model haiku` moves the
reply (and any *unset* tier), while an op with a configured tier keeps it. The tier vars name **Claude**
ids — on a non-Anthropic engine (`/model gpt-5.5` / `gemini`) routing is a no-op and every call uses the
active model (a Claude id never reaches a foreign API). A routed op's tool-loop follows its model (one
call, one tier).

### Model profiles (`/model-set`, v0.41) — the whole stack per provider

A **profile** is a named, provider-homogeneous set `{reply, think, mood, housekeeping}`; three ship
built-in and `LUMI_MODEL_PROFILES` (`name=provider:reply,think,mood,housekeeping;…`) overrides/extends:

| profile | reply | think / mood | housekeeping |
|---|---|---|---|
| `anthropic` | claude-opus-4-8 | claude-sonnet-4-6 | claude-haiku-4-5-20251001 |
| `openai` | gpt-5.5 | gpt-5.5-mini | gpt-5.5-nano |
| `gemini` | gemini-3.1-pro-preview | gemini-2.5-flash | gemini-2.5-flash-lite |

- **`/model-set`** lists the profiles (the active one marked); **`/model-set gemini`** switches the
  engine **and** all tiers in one atomic step — so per-operation routing works **on every provider**
  (unlike the env tier vars, which are Claude-only). The status bar shows `profile:reply-model`.
- **`/model <tier|full-id>`** afterwards moves the **reply only** and drops the profile mark (the
  stack no longer matches a named set; the tiers keep their values).
- A failed switch (missing key) leaves the old stack untouched; nothing persists across restarts
  (the next start reads `.env`).
- **`LUMI_MODEL_PROFILE=anthropic`** boots the whole stack from a profile — one `.env` line instead of
  five. Any explicitly set `LUMI_PROVIDER`/`LUMI_MODEL`/`LUMI_MODEL_*` var **wins over its profile
  field** (expert overrides); an unknown/unset name → the plain env-var mode.

---

## 2. OpenAI

**One-time install** — the OpenAI SDK is an optional extra, shared by OpenAI / DeepSeek / local:

```bash
uv sync --extra models
```

Get a key at <https://platform.openai.com/api-keys>, then in `.env`:

```ini
LUMI_PROVIDER=openai
LUMI_MODEL=gpt-5.5            # a reasoning model — or gpt-4o / gpt-4.1 (non-reasoning, cheaper)
OPENAI_API_KEY=sk-...
```

Restart `./lumi`.

**Two paths, picked automatically by model id (v0.37):**

- **Reasoning models — `gpt-5.5`, the o-series** → the **Responses API**. This is the one OpenAI path where
  the **tool-loop + `LUMI_EFFORT` + a think-box all work together**: tools fire, `reasoning_effort` is
  honored, and a **reasoning summary** populates the think-box (the same seam Opus uses — so the v1.3 inner
  monologue shows on GPT-5.5 too). `LUMI_EFFORT` (low/medium/high; Lumi's xhigh/max → high) tunes depth.
- **Non-reasoning models — `gpt-4o`, `gpt-4.1`** → Chat Completions with JSON-mode structured output. The
  tool-loop works; there's no think box (these models don't reason).

Knobs (defaults are fine): `LUMI_OPENAI_RESPONSES=auto|on|off` forces the path; `LUMI_OPENAI_SUMMARY=auto|concise|detailed|off`
sets the reasoning-summary detail. Give reasoning models output room: `LUMI_MAX_TOKENS=16000` (reasoning
tokens count toward output).

**If the think-box stays empty** (but replies work): reasoning is happening, the *summary* just isn't coming
back. Lumi logs `responses.output items=… summary_parts=… reasoning_chars=…` to `.lumi/lumi.log` so you can
see which case it is — `summary_parts=0` means OpenAI returned no summary for that request. Common reasons:
**organization not verified** (verify at platform.openai.com → Settings → Organization), the model returning
no summary for a given turn, or `LUMI_OPENAI_SUMMARY=off`. It's not a single universal gate — check the log.

**Risks:** no Anthropic-style prompt caching (OpenAI caches input automatically, but the big static prefix
isn't a guaranteed discount). The emotion field comes through JSON mode rather than a tool call — reliable on
GPT-5.5/gpt-4o but trips the `calm` fallback more than Claude. Her voice shifts toward GPT's, Ukrainian is
good-but-not-Claude, and the whole conversation is sent to OpenAI under its data policy (the Responses path
stores turn state server-side via `previous_response_id`).

---

## 3. DeepSeek

Uses the **same `openai` SDK** (`uv sync --extra models` once, if you haven't). DeepSeek is
OpenAI-compatible — the code points it at DeepSeek's base URL automatically. Get a key at
<https://platform.deepseek.com>, then:

```ini
LUMI_PROVIDER=deepseek
LUMI_MODEL=deepseek-chat       # DeepSeek-V3; or deepseek-reasoner
DEEPSEEK_API_KEY=sk-...
```

Restart `./lumi`. You do **not** set a base URL — `deepseek` already maps to `https://api.deepseek.com`.

**Risks:** same as OpenAI (shared adapter — tool-loop works as of v0.37; no think box, no caching, JSON-mode emotion). Two
extras: **`deepseek-reasoner`** is a reasoning model whose chain-of-thought the OpenAI-compatible path
doesn't surface and which adds latency — prefer **`deepseek-chat`** (V3) for Лілі; and the conversation is
sent to DeepSeek's servers (weigh the privacy/compliance implications for a private companion).

---

## 4. MiniMax

**No install needed** — MiniMax uses plain HTTP, not an SDK. Get a key at <https://www.minimax.io>
(API / developer console), then:

```ini
LUMI_PROVIDER=minimax
LUMI_MODEL=MiniMax-Text-01     # or abab6.5s-chat
MINIMAX_API_KEY=...
```

Restart `./lumi`. If your MiniMax account is on a different region/host, set `LUMI_LLM_BASE_URL` to
override (default is `https://api.minimax.io/v1`).

**Risks:** the **least-exercised path** in Lumi (plain HTTP, no SDK), so the highest chance of contract
drift. Same cross-cutting losses (no tool-loop, no think box, no caching), and the emotion field rides plain
JSON — the weakest structured-output mode, so expect more `calm` fallbacks. Ukrainian quality is uncertain;
sanity-check the voice on a few turns before relying on it.

---

## 5. Local model (Ollama / LM Studio — free, private, offline)

Uses the **`openai` SDK** path (`uv sync --extra models` once). First run a local server:

```bash
# Ollama:
ollama pull qwen2.5            # or llama3.1 / mistral — pick one that knows Ukrainian
ollama serve                  # serves an OpenAI-compatible API on :11434
```

Then in `.env`:

```ini
LUMI_PROVIDER=local
LUMI_MODEL=qwen2.5                              # the model you pulled
LUMI_LLM_BASE_URL=http://localhost:11434/v1    # Ollama; LM Studio is usually :1234/v1
# no key needed for local
```

Restart `./lumi`. (LM Studio: start its local server, use its base URL — same idea.)

**Risks (highest).** Free, private, and offline — but a small local model is the furthest from the tuned
Лілі: weakest at **Ukrainian**, at the **structured emotion field** (frequent `calm` fallbacks, occasional
malformed output), and at holding the **canon/persona** across a long prompt. Local context windows are
often small (4K–32K) while **Лілі's prompt is large** (canon + memory digests + mood + RAG + tool results),
so it can overflow and truncate — incoherence or errors. No tool-loop, no think box, no caching; slow on CPU
without a GPU. Good for testing the plumbing offline; not for the real relationship.

---

## 6. Google Gemini (v0.39)

**No install** — Gemini uses plain HTTP (stdlib `urllib`), not an SDK, and the **key already exists**
(`GEMINI_API_KEY` powers image gen + web lookup). In `.env`:

```ini
LUMI_PROVIDER=gemini
LUMI_MODEL=gemini-2.5-pro            # recommended: stable, a thinking model, a large daily quota
# LUMI_MODEL=gemini-3.1-pro-preview # newest, but a tight 250 req/day cap on Tier 1 (preview; gemini-3.1-pro 404s)
GEMINI_API_KEY=...                  # already present
```

Restart `./lumi`, or swap at runtime: `/model gemini` ↔ `/model opus` (the alias is built in).

**What works (a near-full engine):**
- **The tool-loop** — file / wiki / news / web / journal / image tools + the `%`-thought-tools, via Gemini
  **function calling**. (Intermediate rounds offer tools; the final round forces the JSON answer — the
  schema-vs-tools split.)
- **A visible think-box** — with `LUMI_THINKING=on`, Gemini returns a **reasoning summary**
  (`includeThoughts`) that fills the Thinking box, and **`LUMI_EFFORT`** tunes the thinking budget
  (low/medium/high/xhigh/max → a token budget; `max` = dynamic). Pairs with the v0.38 inner voice — the
  three-voice torg shows. Gemini surfaces thoughts more readily than OpenAI's often-withheld summary.
- The `{reply, emotion, intensity}` field via JSON mode (`responseSchema`), validated by the v0.3 gate.

**Safety:** Лілі's intimate register is sent with the most permissive `safetySettings` (`BLOCK_NONE`) — the
v0.39 probe confirmed Gemini returns her tender voice cleanly. A still-blocked response degrades to a calm
placeholder (never a crash).

**Risks:** the conversation is sent to **Google** under its data policy (weigh it for a private companion);
no Anthropic-style prompt-cache discount (Gemini has its own context caching, different economics); and as
with any non-Claude engine, Ukrainian fluency and canon depth shift from the Opus-tuned baseline. Note the
`-preview` model id may change as Google promotes it — verify with `ListModels` if a turn 404s.

**Known quirks (and the nets in place).** Gemini — especially the **2.5 family** — leans into an *agentic
text* format that needs guarding. The app handles the common cases; **`/model opus` is the clean escape** if a
new shape surfaces (Opus 4.8 does none of these):

- **Rate limit (HTTP 429) → "unavailable" line.** Preview ids (`gemini-3.1-pro-preview`) carry a tight
  **250 requests/day** on Tier 1; a heavy session (tool-loop turns + image gen + scheduled `%`-thoughts)
  exhausts it. Fixes: use a **stable** id (`gemini-2.5-pro` / `gemini-2.5-flash` — far larger RPD), **lower
  `LUMI_TOOL_MAX_STEPS`** (on Gemini *each tool round is a separate request*, so a high cap burns the daily
  budget fast), or wait — **RPD resets at midnight Pacific** (≈10:00 Kyiv). Raise the ceiling by upgrading the
  Gemini API tier (link a billing account to the Cloud project behind the key; see the AI Studio rate-limit
  page).
- **Empty reply (only the emoji).** Gemini counts *thinking* tokens against `maxOutputTokens`, so a deep think
  at `LUMI_EFFORT=high` can consume the whole budget and leave no answer (`finishReason: MAX_TOKENS` → the
  gate fills `calm`). The client now reserves the answer budget **on top of** the thinking budget, so this is
  handled — if it ever recurs, lower `LUMI_EFFORT` or raise `LUMI_MAX_TOKENS`.
- **Leaked `tool_code` / `api_response`.** The 2.5 models sometimes write a tool call as a
  `` ```tool_code `` / `<tool_code>` block — and even hallucinate an `<api_response>` — instead of a native
  function call. The client **salvages** offered-tool code calls into real calls and **strips** any leftover
  simulation from the visible reply, so neither leaks to the user; but the format is unstable, so `/model
  opus` is the reliable fallback if it keeps appearing.

---

## Switching away — and back to Opus 4.8

Switching is **always reversible** and mechanically safe: set `LUMI_PROVIDER=anthropic` +
`LUMI_MODEL=claude-opus-4-8` and restart. The contracts that matter are provider-neutral — the locked
emotion enum and the memory record shapes — and **embeddings / RAG are independent** of the chat model
(`LUMI_EMBED_PROVIDER`), so your vector store is untouched by the round trip. Nothing is corrupted.

But the round trip is **not behaviorally lossless** — some of the weaker model's residue persists *after* you
switch back:

- **Memory it wrote stays.** While off Opus, Лілі still writes short summaries, long-term facts, impressions,
  inner-life / journal entries and thoughts — in the *other* model's voice and quality. Those records remain
  in the store and are injected after you return, so a stint on a weaker model leaves a fainter, blander
  trail in her memory of you that outlives the switch.
- **The day's mood is cached.** Mood is generated once per local day and cached until local midnight
  (`/mood`). If today's reading was written by the other model, it stays in force after you switch back,
  until it recomputes tomorrow.
- **A tool-loop gap.** Anything the tools would have done off Anthropic (journal entries, `%`-thoughts,
  file / news / web actions) simply didn't happen during that window — not an error, just missing.
- **Cold cache on return.** Prompt caches are model-scoped, so the first turn back on Opus 4.8 re-writes the
  cache (a normal one-off).

None of this needs cleanup — it fades as new Opus-written memory accrues. For a clean slate after a long
stint on another model, `/forget` clears that user's memory (drastic; per-user).

---

## Verifying a switch

1. Restart `./lumi` after editing `.env`.
2. Look at the **status bar** — it shows the active model id (e.g. `gpt-4o`, `deepseek-chat`).
3. Send a message — if Лілі replies in character, the provider works.
4. If you mistyped the provider or forgot the key, the TUI **fails fast at startup** with a clear
   message naming the missing variable (e.g. *"LUMI_PROVIDER=openai needs OPENAI_API_KEY"*).

**To go back to your normal setup:** `LUMI_PROVIDER=anthropic` and `LUMI_MODEL=claude-opus-4-8`.

---

## Notes & caveats

- **One active provider.** `LUMI_PROVIDER` selects exactly one backend; only that provider's key is
  required. The others can stay blank.
- **Malformed JSON → `calm`.** If a non-Anthropic model returns badly-formatted JSON, the emotion
  safely falls back to `calm` — the turn never crashes. Strong models rarely do this; weak local
  models occasionally will.
- **Cost report** still works on every provider — `.lumi/usage-report.md` estimates cost using each
  model's pricing (unknown models fall back to an Opus-tier estimate).
- **Embeddings (RAG) are separate.** `LUMI_EMBED_PROVIDER` (local / Voyage / OpenAI) is independent of
  `LUMI_PROVIDER`; switching the chat model does not change recall.
</content>
</invoke>
