# "Balanced + per-tool" — implementation guide

This is the engineering plan for the **balanced + per-tool** model-routing scenario from
[LLM_OPERATIONS_COST.md](LLM_OPERATIONS_COST.md) (−26%, ≈ −$63/mo, **no hit to the voice**): keep the
visible `reply` on **Opus 4.8**, run thoughts/mood/synthesis on **Sonnet 4.6**, and the bookkeeping +
mechanical tools on **Haiku 4.5**.

Target routing:

| Operation / tool | Model | Thinking |
|---|---|---|
| `reply` (the visible answer) | **Opus 4.8** | ON (medium) |
| `think`, `mood`; tools: `knowledge` (wiki/news/web), `images`, `recall`, `read_file` | **Sonnet 4.6** | off (opt. ON for `%reflect`) |
| housekeeping (`summary`/`facts`/`compaction`/`session-start` digests + core re-flag); tools: `write`/`navigation` | **Haiku 4.5** | off |
| external (image gen, web grounding, embeddings) | unchanged (Gemini/Voyage) | — |

---

## 1. The decisive architectural fact

Three things in the current code make this **small**:

1. **`model` is already a per-call argument.** Both `LLMClient.reply(...)` and `.reply_structured(...)`
   (`core/llm.py`) take `model: str` per call. The reply turn passes `model=self._model`
   (`agent.py:2207`), and `_housekeeping_reply` passes `model=self._model` too. Nothing is hard-bound to
   one model.
2. **One Anthropic client serves all Claude tiers.** Opus 4.8 / Sonnet 4.6 / Haiku 4.5 are the same
   provider — a different `model` string on the same injected `self._llm` is all it takes. **No second
   client, no new adapter** (that's only for cross-provider, Appendices A/B of the cost doc).
3. **The tool-loop lives inside the client and reuses the call's `model`.** `reply_structured`/`reply` run
   the bounded tool-loop internally (`tool_executor` + `max_steps`), making every continuation call with the
   **same `model`** they were handed. So **routing an operation automatically routes its whole tool-loop** —
   a `think` on Sonnet runs *its* tool steps on Sonnet; a `reply` on Opus runs *its* tool steps on Opus.

**Consequence:** the bulk of the plan is a per-operation `model` swap (Layer 1, below) — no client change.
Only varying the model *within a single loop* by tool family (Layer 2) needs a client change.

---

## 2. Two layers

| Layer | What | Client change? | Risk | Gets you |
|---|---|---|---|---|
| **Layer 1 — per-operation routing** | `reply`→Opus, `think`/`mood`→Sonnet, housekeeping→Haiku. Each op's tool-loop follows automatically. | **No** | low | most of the win, safely |
| **Layer 2 — per-step routing inside the reply loop** | within the `reply` tool-loop, run *intermediate* read/write/nav steps on a cheaper model, the *final visible* step on Opus | **Yes** | medium (coherence) | the last squeeze toward −26% |

Ship **Layer 1 first** (off by default → byte-identical). Measure 2 weeks. Treat **Layer 2 as optional**.

---

## 3. Layer 1 — per-operation routing (the foundation)

### 3.1 Config (`core/config.py`)

Add per-tier overrides; each **defaults to the existing `LUMI_MODEL`**, so unset = today's behavior exactly.

```python
# in Config
model: str = "claude-opus-4-8"        # existing — the default / reply model
model_think: str | None = None        # LUMI_MODEL_THINK         (→ model when None)
model_mood: str | None = None         # LUMI_MODEL_MOOD          (→ model when None)
model_housekeeping: str | None = None # LUMI_MODEL_HOUSEKEEPING  (→ model when None)
# (optional) model_reply: str | None = None  # LUMI_MODEL_REPLY — usually leave = model
```

```python
# in from_env()
model_think=os.getenv("LUMI_MODEL_THINK") or None,
model_mood=os.getenv("LUMI_MODEL_MOOD") or None,
model_housekeeping=os.getenv("LUMI_MODEL_HOUSEKEEPING") or None,
```

`.env.example` snippet:

```bash
# Per-operation model routing (all default to LUMI_MODEL when unset → unchanged).
# Balanced + per-tool plan: keep the visible reply on Opus; thoughts/synthesis on Sonnet; bookkeeping on Haiku.
# LUMI_MODEL_THINK=claude-sonnet-4-6
# LUMI_MODEL_MOOD=claude-sonnet-4-6
# LUMI_MODEL_HOUSEKEEPING=claude-haiku-4-5
```

### 3.2 A `_model_for(kind)` resolver (`core/agent.py`)

Store the overrides in the constructor (beside `self._model = model` at `agent.py:481`):

```python
self._model = model
self._model_think = model_think or model
self._model_mood = model_mood or model
self._model_housekeeping = model_housekeeping or model
```

```python
_HOUSEKEEPING_KINDS = frozenset({
    "session-start", "session-close", "summary", "facts", "compaction",
})

def _model_for(self, kind: str) -> str:
    """Map a call's `kind` to its routed model (Balanced+per-tool). Unset overrides → self._model."""
    if kind == "think":
        return self._model_think
    if kind == "mood":
        return self._model_mood
    if kind in self._HOUSEKEEPING_KINDS:
        return self._model_housekeeping
    return self._model            # reply + anything else
```

### 3.3 Route the calls

**Housekeeping + think** — one line in `_housekeeping_reply` (it already builds `kwargs["model"]`):

```python
# was: "model": self._model,
"model": self._model_for(kind),
```

Because `kind` is already passed by every caller (`summary`, `facts`, `compaction`, `session-start`,
`mood`, `think`), this single change routes **all** of them — *and* their tool-loops (§1.3).

**The reply** (`agent.py:2207`) — explicit, even though it resolves to the default today:

```python
raw = self._llm.reply_structured(
    system=system, messages=messages, model=self._model_for("reply"),
    ...
)
```

### 3.4 Wire `build_core`

```python
return Core(
    ...,
    model=cfg.model,
    model_think=cfg.model_think,
    model_mood=cfg.model_mood,
    model_housekeeping=cfg.model_housekeeping,
    ...
)
```

That's Layer 1. With the three env vars set, `think` (+ its tools), `mood` → Sonnet; `summary`/`facts`/
`compaction`/`session-start` → Haiku; `reply` (+ its tools) stays Opus. Unset → unchanged.

---

## 4. Layer 2 — per-step routing inside the reply loop (optional)

Layer 1 leaves the **reply's** tool steps on Opus (the whole reply loop is one model). To make the reply's
*intermediate* read/write/navigation steps cheaper while keeping the **final visible** step on Opus, the
loop — which lives **inside** `LLMClient.reply_structured` — must vary the model per step.

**The terminal-step problem:** the loop can't know in advance which step is the last (the model decides to
stop calling tools). The step that writes the visible reply is also the one that processed the last tool
result. So you can't cleanly say "all tool steps cheap, final step Opus" without a heuristic.

**Safe heuristic (recommended if you do Layer 2):** add a `step_model` callback to the client loop:

```python
# core/llm.py — reply_structured / reply loop
def reply_structured(self, *, system, messages, model, ..., step_model=None):
    cur_model = model
    while steps < max_steps:
        resp = self._call(model=cur_model, ...)          # one step
        if not resp.tool_calls:                          # terminal → the visible reply
            return resp                                  # produced on `model` (Opus)
        for tc in resp.tool_calls:
            result = tool_executor(tc.name, tc.input)
            ...
        # pick the model for the NEXT (continuation) step from the tool we just ran
        cur_model = step_model(last_tool=resp.tool_calls[-1].name) if step_model else model
        # ...but force the FINAL synthesis back to `model`: keep a 1-step lookahead OR
        # always run the step that emits no tool on `model` (Opus) — see note below.
```

Two workable rules (pick one, test on the voice):

- **R1 — cheap intermediate, Opus final (lookahead by tool):** route a continuation by the tool it's about
  to digest (`write`/`navigation` → Haiku; `read`/`knowledge`/`image`/`recall` → Sonnet). When the model
  emits **no** tool call, that step is already `model` (Opus) because it was the *previous* continuation's
  target — so keep continuations one step behind: the step that finally writes prose ran on the model
  chosen after the *previous* tool. Simplest correct form: **only downgrade a step when the previous step's
  result was a `write`/`navigation` tool; otherwise keep `model`.** Conservative, protects prose.
- **R2 — two-pass:** run the tool-gathering loop entirely on Sonnet/Haiku to collect all tool results, then
  make **one final** `reply_structured` call on Opus with the gathered context. Cleanest separation
  (digging vs speaking), at the cost of one extra Opus call. Easiest to reason about; recommended over R1.

**Routing key — the tool→tier map** (mirror cost-doc §2.2):

```python
_TOOL_TIER = {
    # write / navigation → Haiku
    "create_file":"hk","append_file":"hk","copy_file":"hk","journal_write":"hk",
    "list_files":"hk","find_in_file":"hk","search_files":"hk","read_around":"hk",
    "stat_file":"hk","message_context":"hk","messages_on":"hk","journal_list":"hk","journal_read":"hk",
    # read / knowledge / images / recall → Sonnet
    "read_file":"sonnet","wiki_search":"sonnet","wiki_read":"sonnet","news_search":"sonnet",
    "news_read":"sonnet","web_lookup":"sonnet","view_image":"sonnet","send_image":"sonnet",
    "generate_image":"sonnet","recall":"sonnet",
}
```

> **Caveat (cost-doc §4):** mixing models inside one message history has a **coherence risk** (a model sees
> `tool_use`/`thinking` blocks emitted by another tier). **R2** sidesteps it (the prose step is a clean Opus
> call). Also note: `read_file` cost is driven by **result size** — capping `line_count`/chars (cost-doc §5)
> cuts ~29% of the `tool` bill with **no** model change and **no** coherence risk; do that **first**.

### Shipped form (v0.40 LUMI-158) — R2, gated, Anthropic-only

`AnthropicClient` takes `step_routing` + `step_model` (config: `LUMI_TOOL_STEP_ROUTING` +
`LUMI_MODEL_TOOL_STEP`; both required, **off by default**). Semantics in both loops (`_tool_loop` +
`_text_tool_loop`):

- **Round 0 always runs on the call's model** — a no-tool turn is untouched (no extra call, no voice change).
- **Continuation rounds** (digesting tool results) run on `step_model`.
- When a continuation **tries to answer**, its terminal is **discarded** (logged as a `tool` round — still
  paid) and **one clean final call on the call's model** produces the visible answer (forced `set_state` in
  the reply loop; tool-less text in the think loop). The per-round cache log tags each round's **actual**
  model, so cost attribution stays correct.
- The OpenAI/Gemini loops are untouched (single-model, as before).

**A/B before enabling:** run two matched weeks (`LUMI_TOOL_STEP_ROUTING=on` vs off) with the same tier vars;
compare (1) the per-`kind` `tool` cost from `cache-log.jsonl`, and (2) reply quality on tool-heavy turns —
does the final answer still weave the dug-up material in her voice, or does it read like a summary of notes?
The cost win must beat the discarded-terminal overhead (~one cheap synthesis per tool-using turn) and the
cold step-tier cache. Revert = flip one env var.

---

## 5. Caching considerations

- **Per-model caches are separate.** A cache breakpoint is per (model, prefix). Routing splits the cache by
  tier — which is fine because each tier has its own prefix anyway.
- **`think` loses the reply-cache reuse.** Today `_housekeeping_reply` can pass the reply's `cache_prefix`
  so a full-mode think reuses the **reply's** (Opus) cached prefix. If `think` → Sonnet, that cross-model
  reuse is gone; the think builds its **own** Sonnet cache. The cost-doc Sonnet numbers already assume the
  Sonnet rate, so the estimate holds; just don't expect the Opus-cache reuse. (Low impact: think input is
  small and Sonnet cache-read is $0.30/1M.)
- **Housekeeping has no cache** (`cache_read=cache_write=0` in the measured data) — so Haiku there is a
  clean input/output swap, no caching subtlety.
- **`reply` is untouched** → its Opus cache (the big saver, ~$232/mo) is fully preserved.

---

## 6. Compatibility

- **Thinking.** Adaptive extended thinking is Opus 4.8 / Sonnet 4.6. Housekeeping already forces thinking
  **off** → Haiku is fine there (no thinking needed). `think` runs via `_housekeeping_reply` (thinking off)
  → Sonnet with thinking off is fine; if `%reflect` later wants thinking on, Sonnet supports it.
- **Structured output.** The `reply` emotion contract `{reply, emotion, intensity}` is enforced by the v0.3
  validation gate regardless of model — and `reply` stays Opus anyway. Housekeeping/think outputs are
  parsed leniently (`parse_facts`, `parse_summary`, etc.), so Haiku/Sonnet formatting variance is absorbed.
- **No SDK change.** Same Anthropic client; only the `model` string varies.

---

## 7. Observability

`.lumi/cache-log.jsonl` **already records `model` per call** (and `kind`). After Layer 1, the same per-kind
analysis from the cost doc will show `think`/`mood` rows as Sonnet and housekeeping as Haiku — so you can
**verify the routing landed** and **re-measure the real saving** with the same script. The per-session
`usage-ledger.jsonl` records one `model` per session; with mixed tiers that field becomes the dominant tier
— consider logging cost per `kind` there too if you want exact per-session attribution.

---

## 8. Testing (no paid calls)

- **Routing unit test:** a `MockLLMClient` that **records the `model` it was called with**; drive a turn +
  a `think` + a session close, assert each call used the configured tier (`reply`→opus, `think`→sonnet,
  `summary`/`facts`→haiku). Unset overrides → every call uses `self._model` (byte-identical guard).
- **Tool-loop follows the op:** with `think` routed to Sonnet, assert the think's tool continuations are
  also Sonnet (proves §1.3).
- **(Layer 2)** assert the final/visible reply step uses Opus while intermediate steps use the tier from
  `_TOOL_TIER`; assert R2's separate final Opus call.
- Mock the model — never call paid APIs (the existing CI rule).

---

## 9. Rollout

1. **PR 1 — Layer 1 + config + tests.** Defaults unset → no behavior change. Land green.
2. **Enable in your `.env`:** `LUMI_MODEL_THINK=claude-sonnet-4-6`, `LUMI_MODEL_MOOD=claude-sonnet-4-6`,
   `LUMI_MODEL_HOUSEKEEPING=claude-haiku-4-5`. Restart.
3. **Measure 2 weeks** with the cost-doc script over `cache-log.jsonl`; confirm the saving and watch for any
   quality regressions in thoughts/mood (the only persona-adjacent ops downgraded).
4. **Cheap win, no model change:** cap `read_file`/`read_around`/`search_files` result size (cuts ~29% of
   `tool`).
5. **PR 2 — Layer 2 (optional)** behind a flag (`LUMI_TOOL_STEP_ROUTING`), prefer **R2** (two-pass). A/B the
   reply quality before defaulting it on.
6. **Never route `reply` off Opus** here — that's the separate "engine swap" question (cost-doc Appendix C,
   GPT-5.5 Thinking), not this plan.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Thoughts/mood feel flatter on Sonnet | They're persona-*adjacent*, not the reply; A/B 2 weeks; revert a single env var if off |
| Haiku mangles a summary/fact line | Parsers are lenient + these are lossy tiers; the v0.36 session-start re-flag re-ranks the core anyway |
| Layer 2 coherence (mixed models in one history) | Prefer **R2** (clean final Opus call); or skip Layer 2 and take the `read_file` size cap instead |
| Cache fragmentation across tiers | Expected; `reply`'s Opus cache (the big one) is untouched |
| Per-session ledger attributes one model | cache-log already per-call; add per-`kind` cost if exact attribution needed |

---

## 11. Summary

The visible answer never leaves Opus. Layer 1 is a **~20-line, no-client-change** patch (a `_model_for(kind)`
resolver + one line in `_housekeeping_reply` + config), off by default, that routes thoughts/mood to Sonnet
and bookkeeping to Haiku — and, because the tool-loop reuses the call's model, each operation's tools follow
for free. That captures most of the **−26%** safely. Layer 2 (per-step routing in the reply loop) and the
`read_file` size cap are the optional last squeeze. Sthira keeps **thinking and digging cheaply, but
speaking on Opus**.
