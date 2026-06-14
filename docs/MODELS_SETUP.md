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

**Two things true for every non-Anthropic provider:**

- **`thinking` / `effort` / prompt-cache are Anthropic-only.** They are silently ignored elsewhere
  (no error) — so the thinking box stays empty and there's no cache discount.
- Лілі's persona is **Ukrainian.** Pick a model that handles Ukrainian well (the cloud providers below
  all do; small local models are weaker).

---

## 1. Anthropic — Claude tiers (the default)

Already working. Change only the model id to switch tier:

```ini
LUMI_PROVIDER=anthropic         # or leave unset — anthropic is the default
LUMI_MODEL=claude-opus-4-8      # or claude-sonnet-4-6 (cheaper) / claude-haiku-4-5 (cheapest)
ANTHROPIC_API_KEY=sk-ant-...
```

- No install needed.
- This is the **only** tier with extended thinking, `effort`, and prompt caching.
- Restart `./lumi`.

---

## 2. OpenAI

**One-time install** — the OpenAI SDK is an optional extra, shared by OpenAI / DeepSeek / local:

```bash
uv sync --extra models
```

Get a key at <https://platform.openai.com/api-keys>, then in `.env`:

```ini
LUMI_PROVIDER=openai
LUMI_MODEL=gpt-4o              # or gpt-4.1 / gpt-4o-mini (cheaper)
OPENAI_API_KEY=sk-...
```

Restart `./lumi`. Structured output uses JSON mode (gpt-4o handles it well).

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
