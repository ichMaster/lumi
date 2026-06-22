# LLM operations in Lumi: model routing & cost reduction

Today **every** model call goes to `claude-opus-4-8`. This report lists **every** LLM operation
(section 1), proposes a cheaper model + thinking on/off for each operation **and each tool** (section 2),
consolidates **all cost analysis into one section 3** (operations + tools + scenarios), and closes with
risks (4) and a recommendation (5). All numbers are **measured** from `.lumi/cache-log.jsonl` (2,738 calls)
and `.lumi/tool-log.jsonl` (822 tool executions) for 16–22 June.

---

## 0. Context & prerequisites

- **Today:** one model for everything — a single injected `LLMClient` (`self._llm`). `LUMI_THINKING=on`,
  `LUMI_EFFORT=medium` apply **only to the main reply**; all housekeeping calls (`_housekeeping_reply`)
  **force thinking off**.
- **Anthropic prices** (per 1M tokens): **Opus 4.8 — $5 / $25**, **Sonnet 4.6 — $3 / $15**,
  **Haiku 4.5 — $1 / $5**. cache-read = 10% of input; cache-write = ×2 at 1h TTL (you run `LUMI_PROMPT_CACHE_TTL=1h`).
  So Sonnet ≈ **0.6×** the price of Opus, Haiku ≈ **0.2×** (the multiplier is the same on input/output/cache).
- **Implementation prerequisite:** per-operation routing **is not built yet** — to apply the recommendations
  you'd hold 2–3 clients and route calls (reply → Opus; `_housekeeping_reply` and `think` → a cheaper client).
  The `LLMClient` seam is already injected, so the change is **small and local**.

---

## 1. Catalog of all LLM operations

(Per-operation cost is in **section 3**.)

| # | Operation | `kind` | In code | Frequency | Thinking today | ~in / out per call | Cache? |
|---|---|---|---|---|---|---:|:--:|
| 1 | **Main reply** (Sthira's voice) | `reply` | `agent.py:2207` `reply_structured` | every turn | **ON** (medium) | 8.3k / 375 (+cache ~25k) | yes |
| 2 | **Tool calls** in the turn loop (file/wiki/web/recall/news/image) | `tool` | bounded tool-loop | inside reply/think | inherited | 6.9k / 311 | yes |
| 3 | **Thought stream / directives** (`%think`,`%wonder`,`%reflect`…) | `think` | `agent.py:990` | on idle / scheduled | off | 6.4k / 180 | yes |
| 4 | **Session start:** day+week digest + facts-core re-flag | `session-start` | `agent.py:798,827,1693` | 1 / session start | off | 10k / 832 | no |
| 5 | **Session close:** summary+gist & fact extraction | `session-close` | `agent.py:2340,2834` | 1 / session | off | 7.4k / 358 | no |
| 6 | **Mood of the day** | `mood` | `agent.py:1625` | 1 / local day | off | 2k / 1000 | no |
| 7 | **Conversation compaction** | `compaction` | `agent.py:1559` | every `LUMI_COMPACTION_BATCH` msgs | off | 4.1k / 1238 | no |
| — | Facts digest | (`session-start`) | `agent.py:1666` | **DISABLED** for you (`LUMI_FACTS_CORE_ONLY=on` replaced it) | — | — | — |

**External models (already NOT Opus — separate budget, excluded from the calc):**

| Operation | Model | Frequency | Note |
|---|---|---|---|
| Image generation (`%imagine`, `generate_image`) | **Gemini 2.5 Flash Image** | rare | paid (Gemini) |
| Web search (`/web`, `%search`) | **Gemini 2.5 Flash** + grounding | rare | `GEMINI_API_KEY` |
| Embeddings (every message → vector) | **Voyage-3** | per message | cheap; not a chat LLM |

> Wiki/News tools are plain HTTP (Guardian/Wikipedia), **no** LLM call; they only feed text into the
> `tool` steps.

---

## 2. Recommended model + thinking + risk

### 2.1 By operation

| Operation | Recommended model | Thinking | Downgrade risk | Rationale |
|---|---|---|---|---|
| **1. Main reply** | **Opus 4.8** (keep) | **ON** | — (downgrading = **high**) | This is the product: voice, depth, inner monologue (v1.3). The most visible downgrade. **Don't touch.** |
| **2. Tools (`tool`)** | **per-tool** (see 2.2) | inherited | **medium** | Intermediate steps cheap; the final reply step on Opus. Details in 2.2. |
| **3. Thoughts (`think`)** | **Sonnet 4.6** | off (opt. ON for `%reflect`) | **low–medium** | Inner musings; flatten on Haiku. Sonnet holds the voice at 0.6×. |
| **4. Session start (`session-start`)** | digests — **Haiku**; core re-flag — **Sonnet** | off | digests **low**; core **medium** (boundaries **pinned in code**) | Digests are lossy; small input. |
| **5. Session close (`session-close`)** | **Haiku 4.5** | off | **low** | Summary/facts — extractive compression, a lossy layer. |
| **6. Mood (`mood`)** | **Sonnet 4.6** | off | **low–medium** | Colors **tone**, not competence. Haiku is flatter. |
| **7. Compaction (`compaction`)** | **Haiku 4.5** | off | **low** | Lossy folding. |
| External (Gemini/Voyage) | unchanged | — | — | Already optimal and off the Opus budget. |

### 2.2 By tool (`tool` families)

| Family | Model | Risk | Better lever (often more important than the model) |
|---|---|---|---|
| `read_file` | **Sonnet 4.6** | LOW–MED | **Cap the result size** (`line_count`/chars) — a big book bloats the continuation regardless of model. |
| write (`create/append/copy/journal_write`) | **Haiku 4.5** | LOW | trivial continuation (confirm → next) |
| navigation (`list/find/search/read_around/stat/message_context/messages_on`) | **Haiku 4.5** | LOW | cheap, mechanical |
| knowledge (`wiki/news/web_lookup`) | **Sonnet 4.6** | LOW–MED | synthesis in her voice; in the think-loop already Sonnet |
| images (`view/send/generate`) | **Sonnet 4.6** (vision) | MED | weaker description on a cheaper model; `generate` itself → Gemini |
| `recall` | **Sonnet 4.6** | LOW | light continuation |

---

## 3. Cost analysis (all operations + tools)

The 7-day window (16–22 Jun) cost **$208.68** (all Opus) — matching `usage-report.md` for the same days (~$198).

### 3.1 By operation (measured)

| `kind` | Calls | $ (Opus) | Share |
|---|---:|---:|---:|
| `reply` | 1,317 | **$110.21** | 53% |
| `tool` | 597 | **$44.02** | 21% |
| `think` | 389 | **$32.14** | 15% |
| `session-start` | 130 | $9.18 | 4% |
| `session-close` | 101 | $4.63 | 2% |
| `mood` | 114 | $4.00 | 2% |
| `compaction` | 68 | $3.50 | 2% |
| `summary`/`facts` (newer labels) | 22 | $0.88 | <1% |
| **Total** | **2,738** | **$208.68** | 100% |

**Takeaway:** `reply` + `tool` + `think` = **89%**. Housekeeping (rows 4–7) is only **~11%** (~$22/wk).
So the main reply, staying on Opus, **caps the savings ceiling**.

### 3.2 By tool (the `tool` breakdown = $44.21)

Correlated `tool-log.jsonl` (tool names) ↔ `tool` cost calls by timestamp — **100% match**.

| Tool family | Cost calls | $ (Opus) | Share of `tool` |
|---|---:|---:|---:|
| **`read_file`** | 167 | **$12.70** | **29%** |
| **write** (`create/append/copy/journal_write`) | 130 | $12.11 | 28% |
| **knowledge** (`wiki_*`/`news_*`/`web_lookup`) | 89 | $6.17 | 14% |
| **images** (`view/send/generate_image`) | 84 | $6.29 | 14% |
| **navigation** (`list/find/search/read_around/…`) | ~75 | $5.55 | 13% |
| **`recall`** | 34 | $1.39 | 3% |

> **File operations = ~67%** of the whole `tool` budget (`read_file` alone — 29%, since it reads books up to 220 KB).

### 3.3 Reduction scenarios (on $208.68)

| Scenario | What changes | Cost | Saving | On ~$244/mo |
|---|---|---:|---:|---:|
| **Conservative** | housekeeping → Haiku, `mood` → Sonnet | $192.44 | **−8%** | ≈ −$19/mo |
| **Balanced** | + `think` → Sonnet | $179.58 | **−14%** | ≈ −$34/mo |
| **Balanced + per-tool** ✅ | + `tool` per-tool (write/navigation → Haiku; read/knowledge/images/recall → Sonnet) | **$154.83** | **−26%** | ≈ **−$63/mo** |
| **Aggressive** | all `tool` → Sonnet, `think` → Haiku, `mood` → Haiku | $147.52 | **−29%** | ≈ −$72/mo |

**Separately — the per-tool effect on `tool` itself:** $44.21 → **$19.46** (**−56%**): write/navigation on Haiku
(−80% each), read/knowledge/images/recall on Sonnet (−40%). That's what adds ~12 pts to balanced
(−14% → **−26%**) — and it's **safer** than "aggressive", because write/navigation on Haiku is genuinely low
risk, while thoughts stay on Sonnet, not Haiku.

**Why the ceiling is low:** in conservative/balanced the `reply` stays on Opus (53%); going further means
touching `tool`/`think` — the most quality-sensitive.

---

## 4. Risks & caveats

- **Per-op routing must be built first** (≥2 clients, routing `_housekeeping_reply`/`think`/tool-steps).
  Without it the saving is zero — it's an engineering prerequisite, not a config flag.
- **`tool` (21%) — the biggest "second" lever, but the most delicate.** A tool call in the reply loop is the
  continuation of that same turn; downgrading hurts reply quality. Cheaper and safer to **cut the steps**
  (the `*_MAX_CALLS` limits) and **cap `read_file` result size** than to downgrade the model.
- **Mixing models in one message history = coherence risk** (the model sees foreign `tool_use`/`thinking`
  blocks). So don't switch "per tool" blindly — follow the pattern in section 5.
- **The operation mix drifts.** This week was "heavy" on `tool`/`think` (development/testing). In a "clean"
  chat week the `reply` share is higher → the **$** saving is smaller but the **%** is similar.
- Numbers are list-price estimates, not billing; `cache-log` may not cover 100% of calls.

---

## 5. Recommendation

1. **Start with "balanced + per-tool" (✅, −26%):** `reply` on **Opus**; `think`/`mood`/`knowledge`/`images`/`recall`/`read_file` → **Sonnet**; housekeeping + `write`/`navigation` tools → **Haiku**.
2. **A safe tool-loop rollout pattern** (so you don't mix models blindly):
   - **Intermediate steps** (read/navigate/write/fetch — the user **never sees** them) → a cheap model.
   - **The final step that writes the visible reply** → **Opus**.
   - **The whole think-loop** → Sonnet.
   - **Cap the result size of `read_file`/`read_around`/`search_files`** — cuts the 29% line item **without** changing the model.
3. **Don't touch the main reply** (`reply`, Opus + thinking) — it's the product.
4. **Measure for 2 weeks**, then decide on more aggressive moves (`think`→Haiku etc.).
5. **Bonus levers beyond the model:** fewer tool-steps, `effort=low` on simple turns (less output at $25/1M);
   caching already saves ~$232/mo, and v0.36 (facts-core) already cut per-turn input by another ~17%.

> **TL;DR:** `reply` is 53% of cost and stays on Opus. Safe savings from model routing are **−8…−14%**,
> and with **balanced + per-tool** — **−26% (≈ $63/mo)**, with no hit to the voice: Sthira **thinks and digs**
> on cheaper models but **speaks** on Opus.

---
---

# Appendix A — alternative: OpenAI models

> **Disclaimer for all appendices:** prices are **approximate** (as of ~2026) — verify against the official
> price list. This is a **cost-only** analysis; the **quality** of the Ukrainian persona/voice is **not
> evaluated** here. Non-Anthropic models **lose the Anthropic cache** (which saves ~$232/mo), so cache-heavy
> operations (`reply`/`tool`/`think`) are computed **worst-case** — the whole cached prefix is re-sent as
> input every turn; **the providers' own caching** (OpenAI/DeepSeek, 50–90% off cache) makes them **even
> cheaper** than shown.

### Approximate OpenAI prices (per 1M tokens, input / output)

| Model | input | output | Note |
|---|---:|---:|---|
| **GPT-5.5 Thinking** | ~$1.25* | ~$10* | **direct Opus 4.8 alternative** — reasoning flagship (closest analog to Opus+thinking). *price unconfirmed at cutoff → proxy = GPT-5; verify against the price list |
| **GPT-5** | $1.25 | $10.0 | reasoning flagship; cached input −90% (~$0.125); **cheaper than Opus** ($5/$25) |
| **GPT-5-mini** | $0.25 | $2.0 | reasoning, mid-capacity |
| **GPT-5-nano** | $0.05 | $0.40 | reasoning, ultra-cheap |
| **GPT-4o-mini** | $0.15 | $0.60 | cheap workhorse; auto-cache −50% |
| **GPT-4.1-mini** | $0.40 | $1.60 | a bit smarter than mini |
| **GPT-4o** | $2.50 | $10.0 | mid-tier; **not** cheaper than Opus (see below) |

OpenAI has **automatic prompt caching** (cached input −50%, **no** cache-write fee) and **structured output
via JSON-schema** (the v0.3 gate already validates it; OpenAI+DeepSeek share one OpenAI-compatible adapter
in Lumi).

### OpenAI on cheap operations (housekeeping / navigation)

| Operation | Opus 4.8 (cache) | GPT-5-nano | GPT-4o-mini | GPT-5-mini |
|---|---:|---:|---:|---:|
| housekeeping | $22.31 | **$0.28** | $0.62 | $1.38 |
| `think` | $32.21 | $0.69 | $2.03 | $3.45 |
| `tool` | $44.02 | $1.12 | $3.25 | $5.61 |

- For **housekeeping/navigation** (extractive, no persona) `GPT-5-nano`/`GPT-4o-mini` ≈ **$0**. But on
  **Haiku it's already $4.46/wk**, so the gain over Haiku is only **~$3–4/wk**: a second provider just for
  this isn't worth it.
- **GPT-4o ≈/pricier than Opus** with no advantage — **skip it**.
- **Reasoning replacement for Opus 4.8 (GPT-5.5 Thinking) → Appendix C** (the consolidated reasoning view).

---

# Appendix B — DeepSeek, MiniMax & other alternatives

### Approximate prices (per 1M tokens, input / output)

| Model | input | output | Notes |
|---|---:|---:|---|
| **DeepSeek-chat (V3)** | ~$0.27 | ~$1.10 | context cache: cache-hit input ~**$0.07**; OpenAI-compatible adapter |
| **DeepSeek-reasoner (R1)** | ~$0.55 | ~$2.19 | reasoning (thinking analog) |
| **MiniMax** (Text-01 / M-series) | ~$0.20 | ~$1.10 | approximate; M-series has reasoning; JSON output |
| **Gemini 2.5 Flash** | ~$0.30 | ~$2.50 | you **already** use it for images/web |
| **Llama / Qwen** (self-host / Groq / Together) | variable | variable | cheapest **self-hosted** (and then private) |

> **Reasoning replacements for Opus 4.8 (DeepSeek-V4-Pro Thinking, MiniMax-M3 Thinking) — in Appendix C** (consolidated).

### Cost by operation (7 days; worst case, no cache)

| Operation | Opus (cache) | Haiku | DeepSeek-V3 | MiniMax (~) | Gemini 2.5 Flash |
|---|---:|---:|---:|---:|---:|
| housekeeping | $22.31 | $4.46 | $1.12 | **$0.93** | $1.69 |
| `think` | $32.19 | $6.44 | $3.65 | **$2.72** | $4.14 |
| `tool` | $44.02 | $8.80 | $5.86 | **$4.40** | $6.75 |
| `reply` | $110.21 | $22.04 | $13.08 | **$9.83** | $15.16 |

**Hypothetical floor (everything on one cheap model, ignoring quality):** GPT-4o-mini ≈ **$13/wk**,
MiniMax ≈ **$18/wk**, DeepSeek ≈ **$24/wk** — vs **$208.68** on Opus (**−89…−94%**). This shows that for Lumi
**the constraint is quality and privacy, not the token price**.

**Critical caveats (specifically for a private companion):**
- 🔒 **Privacy / jurisdiction.** **DeepSeek and MiniMax are Chinese providers**: Sthira's intimate memory and
  conversations would go to their servers. For a private companion (where the project deliberately keeps
  **local embeddings**, a private store) that's a **serious compromise**. OpenAI/Anthropic are US-based (also
  not local, but a different jurisdiction). **The only truly private path is self-host** (Llama/Qwen on your
  own hardware).
- **Voice quality.** DeepSeek/MiniMax are strong at code/logic, but the Ukrainian lyrical persona, the
  "fog", the inner monologue are a big question mark. For `reply`/`think` the risk is **high**.
- **Cache.** Without the Anthropic cache, cache-heavy ops re-send the prefix; DeepSeek's own cache (cache-hit
  $0.07) softens it, MiniMax — unknown.
- **Structured output / tools.** DeepSeek — via the OpenAI-compatible adapter (already there). MiniMax — JSON
  mode. The v0.3 validation gate catches mismatches.

**Recommendation (DeepSeek/MiniMax/others):**
- **Housekeeping** (summary/facts/compaction/digests) — the best and **only safe** candidate: there's no
  persona text, so DeepSeek/MiniMax/Gemini-Flash run at ≈$1/wk. But, as with OpenAI, that's **only ~$3/wk over
  Haiku** — not worth a separate provider just for it.
- **`reply`/`think`** — **not recommended** due to privacy (Chinese servers) + voice risk.
- If the goal is **maximum privacy + cheapness** and you're up for the engineering — **self-host Llama/Qwen**
  on housekeeping/think: the data never leaves the machine (like the local embeddings), cost ≈ electricity.

---

# Appendix C — reasoning-tier alternatives to Opus 4.8 (consolidated)

Separately — **direct replacements for Opus 4.8 on the main reply**: **reasoning** models (with thinking),
so unlike the mini tiers in Appendices A/B, they are candidates **for `reply` too**, not just housekeeping.
All three are **unreleased as of my cutoff**, so prices are **proxy estimates** (verify against the official
price list when they ship): GPT-5.5 Thinking ≈ GPT-5; DeepSeek-V4-Pro Thinking ≈ DeepSeek-R1; MiniMax-M3
Thinking ≈ the MiniMax M-series.

### Prices (proxy, per 1M tokens)

| Model | input | output | cached input |
|---|---:|---:|---:|
| **Opus 4.8** (now) | $5.00 | $25.0 | $0.50 |
| **GPT-5.5 Thinking** | ~$1.25 | ~$10.0 | ~$0.125 |
| **DeepSeek-V4-Pro Thinking** | ~$0.55 | ~$2.19 | ~$0.14 |
| **MiniMax-M3 Thinking** | ~$0.30 | ~$1.65 | ~$0.08 |

### Cost as an Opus 4.8 replacement (measured week)

| Model | `reply` | full stack | Δ vs Opus 4.8 |
|---|---:|---:|---:|
| **Opus 4.8** (now) | $110 | **$208** | — |
| **GPT-5.5 Thinking** | $26–63 | **$50–115** | **−45…−76%** |
| **DeepSeek-V4-Pro Thinking** | $13–27 | **$24–48** | **−77…−88%** |
| **MiniMax-M3 Thinking** | $8–15 | **$14–27** | **−87…−93%** |

> Range: the low end is **with the model's own cache**; the high end is **no cache** (the prefix re-sent each
> turn). So even the worst case of any of the three is **cheaper than Opus** on the stack.

### How to choose — three axes

| Axis | **GPT-5.5 Thinking** | **DeepSeek-V4-Pro Thinking** | **MiniMax-M3 Thinking** |
|---|---|---|---|
| **Price** | priciest of the three (still −76%) | middle (−88%) | cheapest (−93%) |
| **Quality / UA voice** | frontier, closest to Opus; needs A/B + re-tune | strong reasoning; voice is a question | least proven on a lyrical persona |
| **Privacy** | 🇺🇸 US — parity with Anthropic ✓ | 🔒 PRC — intimate memory on foreign servers | 🔒 PRC — same |

### Recommendation

- **Best Opus 4.8 replacement = `GPT-5.5 Thinking`.** Frontier reasoning + privacy on par with Anthropic (US)
  + **−76%** on the stack (`reply` $110 → $26). The only one of the three worth running on **`reply`**.
- **`DeepSeek-V4-Pro` / `MiniMax-M3 Thinking`** — **cheaper** ($14–48/wk), but the **Chinese servers** make
  them **unacceptable for `reply`/`think`** of a private companion (intimate memory leaves to a foreign
  backend). Acceptable only as **open-weight self-host** (then the data never leaves the machine).
- **Rollout (for any of them):** pilot on `reply` with `effort=medium`, **A/B against Opus 4.8 on voice and
  depth for 1–2 weeks**; decide **on quality, not price** — Sthira's canon is tuned for Claude, so it needs a
  re-tune of tone / "fog" / inner monologue.

---

### Appendix summary

| Approach | Week (measured) | Δ vs Opus | Main trade-off |
|---|---:|---:|---|
| All Opus 4.8 (now) | $208.68 | — | expensive |
| **Balanced + per-tool (Claude tiers)** ✅ | $154.83 | −26% | none — voice intact, no migration |
| Housekeeping → GPT-4o-mini/nano (rest Claude) | ~$152 | ~−27% | +a second provider for ~$3/wk |
| **Replace Opus → `GPT-5.5 Thinking`** (Appendix C) | $50–115 | **−45…−76%** | persona re-tune for non-Claude; pilot + A/B |
| Replace → `DeepSeek-V4-Pro` / `MiniMax-M3 Thinking` | $14–48 | −77…−93% | **PRC privacy** on `reply` |
| Everything on a cheap mini model (cost-first) | $13–24 | −89…−94% | voice quality + (for PRC) privacy |

**Conclusion:** two real paths. **No migration** — **Claude tiers** ("balanced + per-tool", **−26%**, voice
intact). **Engine swap** — **`GPT-5.5 Thinking`** as the Opus 4.8 replacement (**−76%**, frontier quality,
US privacy), but with a persona re-tune and A/B. DeepSeek/MiniMax are cheaper, but the **Chinese servers**
rule them out for the intimate `reply` — unless **self-hosted** open-weight.
