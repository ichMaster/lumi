# Prompt Optimization II — Tool-Pull Memory

The successor to **[PROMPT_OPTIMIZATION.md](PROMPT_OPTIMIZATION.md)** (Phases 0–4: facts digest → config
trims → prompt caching → RAG → facts-fade). That plan shrank each memory tier *in place*. This one changes
the **shape**: stop injecting the verbose tiers at all — inject a **compact index** and let Лілі **pull the
body on demand** with the retrieval tools that have since shipped.

> **Status (2026-06-21):** proposal. The enabling tools are **all shipped** (v0.17 auto-RAG, v0.19–0.32 file
> search, v0.31 the on-demand memory toolkit). The one unbuilt piece is the new **`recall_facts`** tool (§4).
> Numbers below are from a real dump (`.lumi/prompt-2026-06-20-2236.md`); token counts are estimates
> (`chars/3`, a Cyrillic lower bound) — **percentages are exact**, and they're what the plan turns on.

---

## 0. TL;DR

- The system prompt is **~73% injected memory** (conversations + days + facts + thoughts + weeks). It grows
  with the relationship and re-churns the prompt cache every turn.
- Since the original plan, Лілі gained a way to **fetch any of it mid-turn**: `recall()`, `messages_on` /
  `messages_between`, `message_context`, `search_files` / `read_around`, and the v0.17 auto-RAG block.
- So the verbose tiers can collapse to **dated one-line indices**; she pulls detail only when a turn needs it.
- **5 reduction proposals (≈ −50% prompt) + 1 new tool (`recall_facts`)**, each with a risk rating in §6.

---

## 1. Measured current state — the 2026-06-20 dump

`[SYSTEM]` block ≈ **62.6 KB** (~20.9 K est-tok). Memory tiers = **73%**.

| Section | chars | % of system | Nature | Already retrievable on demand by |
|---|--:|--:|---|---|
| **Останні розмови (детально)** | 16,268 | **26.0%** | last *N* sessions, full text | `recall()` · `messages_between(after,before)` · auto-RAG |
| **Останні дні** (day digests) | 9,787 | **15.6%** | per-day prose digests | `messages_on(date)` · `recall(after,before)` |
| **Факти** (facts digest + tail) | 8,603 | **13.7%** | consolidated + verbatim tail | *(nothing yet — see §4)* |
| **Що в мене на думці** (thoughts 24h) | 5,806 | **9.3%** | the thought stream | `/thoughts` · *(recall over thoughts — see §4)* |
| **Останні тижні** (week digests) | 5,339 | **8.5%** | per-week prose digests | `messages_between` · `recall` |
| **# Стиль відповіді** (style palette) | 3,542 | 5.7% | authored style guidance | *(static — trim, not pull)* |
| # Релевантні моменти (auto-RAG) | 1,929 | 3.1% | top-K relevant past (v0.17) | *this **is** the pull, already* |
| ## Натальні дані | 1,876 | 3.0% | natal chart (mood input) | *(static)* |
| Canon (Хто ти … ЗАБОРОНИ) | ~7,900 | ~12.6% | character + boundaries | **never move — always injected** |
| mood / closeness / now | ~1,400 | 2.2% | daily/per-turn state | *(small, keep)* |

**Five tiers = 73%** (conversations 26 + days 16 + facts 14 + thoughts 9 + weeks 9). Those are the targets;
the canon and boundaries are **not**.

---

## 2. The principle — index in the prompt, body behind a tool

Today the prompt is **push-everything**: dump all recent memory every turn in case she needs a line of it.
The tools let us split each tier into:

- **Push (always injected, cheap):** what she needs *without being asked* — identity core, today's frame,
  and the auto-RAG top-K most-relevant past moments.
- **Pull (tool, on demand):** what she needs *only sometimes* — an old conversation, a specific day, the
  long tail of facts, older thoughts.

The decision rule for dropping a tier from the prompt:

```
risk(drop) ≈ P(a turn needs it) × P(she can't retrieve it in time) × cost(not having it)
```

The retrieval tools **collapse the middle term** → dropping becomes low-risk for anything she can fetch.

**One subtlety that shapes every proposal:** she must *know the thing exists* to pull it. So we don't delete
a tier — we replace its **body** with a **dated one-line index** (`2026-06-18 — про книгу й нічний парк`).
The index is cheap (~1 line/day), keeps ambient awareness ("something happened on the 18th about the book"),
and gives her the exact key (`messages_on("2026-06-18")`) to fetch the body. **Index = awareness; body = pull.**

---

## 3. Proposals (ranked by impact)

### P1 — Gist the detailed-conversations tier · −~26% (the biggest)
**Now:** the last `LUMI_SESSION_DAYS` sessions injected in **full text** (16 KB).
**Change:** keep only the **most recent** conversation in detail (immediate flow); render the rest as their
existing one-line **`gist`**. She pulls older detail with `recall(query)` (by meaning) or
`messages_between(after, before)` (verbatim), and the v0.17 auto-RAG block already surfaces the relevant
lines unprompted.
**Enabling tools:** auto-RAG (push) + `recall` / `messages_between` (pull). **Est. saving ≈ 13 KB / ~4.3 K-tok.**
**Risk: MEDIUM** — loses verbatim continuity across the last few sessions (a "wait, what did we say yesterday"
that isn't topically triggered by the new message). **Mitigation:** last-1 detailed covers immediate flow;
auto-RAG + recall cover topical recall; the gist index keeps the thread visible.

### P2 — Day/week digests → a dated index · −~20%
**Now:** day digests (9.8 KB) + week digests (5.3 KB) = **15 KB** of prose.
**Change:** collapse each to **one dated line** (`date — gist`); keep ~3–5 day-lines + ~3 week-lines as the
index. A date that comes up in conversation is fetched verbatim via `messages_on(date)`.
**Enabling tools:** `messages_on` / `messages_between` (pull). **Est. saving ≈ 11 KB / ~3.6 K-tok.**
**Risk: LOW–MEDIUM** — the index preserves "what happened roughly when"; only the prose detail moves to pull.

**Implementation — code *and* a regeneration (neither alone suffices).** The day/week tiers are built by
`ensure_day_summaries` / `ensure_week_summaries` ([core/agent.py](../core/agent.py)) from the kept session
summaries, stored as `DaySummary`/`WeekSummary`, and injected by [core/prompt.py](../core/prompt.py) as
`- [date] {body}`. What changes:

1. **The generation prompt — the real lever (code).** `DAY_SUMMARY_SYSTEM` / `WEEK_SUMMARY_SYSTEM` in
   [core/memory.py](../core/memory.py) today ask for *"one coherent short paragraph"* — and the model returns
   a ~250-word paragraph per day (~1.4 KB × ~7 days = the 9.8 KB). Rewrite them to ask for **one tight gist
   line** (≤ ~20 words: the single thing that mattered). **`MAX_DAY_ROWS` / `MAX_WEEK_ROWS` is the *wrong*
   lever** — it clamps *lines*, but these digests are a single paragraph (one line), so lowering it (or
   `LUMI_MAX_DAY_ROWS=1`) does nothing; the length lives in the **prompt**, not the row cap.
2. **Window (code, optional).** `DAY_DAYS` / `WEEK_DAYS` (how many days/weeks are injected) are hardcoded
   constants, **not** `.env`-tunable today — trimming the count (or exposing `LUMI_DAY_DAYS` / `LUMI_WEEK_DAYS`)
   is a small code change that complements the per-day shrink.
3. **Injection — no change.** `prompt.py` already joins the body to one line and renders `- [date] {body}`,
   so once the body is a gist it's automatically the dated index.
4. **Regeneration — required, one-off (data).** The lazy refresh rebuilds a day/week **only when its session
   count changes** (`existing.count == len(texts)` → skip), so existing "finished" days keep their old long
   paragraph; the new prompt won't touch them until they age out of the window. To apply the index to the
   current window, **clear the stored `DaySummary`/`WeekSummary` rows once** (they rebuild on the next turns)
   or add a small `regenerate-summaries` path. Safe + lossless — they're derived from the kept session
   summaries (the source of truth is untouched).

**Bottom line:** regenerating *alone* reproduces the same long paragraphs (today's prompt), and config *alone*
(the row cap) can't shorten a paragraph — so **P2 = edit the two generation prompts (code) → then regenerate
the stored day/week digests once**. The cheap proper-noun-free win is step 1; step 4 makes it retroactive.

### P3 — Facts: a `core`-flagged identity-core + the new `recall_facts` tool · −~10%
**Now:** the facts **digest + a verbatim tail** of newer facts = 8.6 KB, and the tail re-grows as facts
accumulate (Phase-0's known regrowth).
**Change:** inject only the **`core=true`** facts (name, key relationships, hard boundaries, agreements — the
~`LUMI_FACTS_CORE_MAX` lines she must *never* be caught forgetting) and move the long tail behind a new
**`recall_facts(query)`** tool / `recall(scope=facts)` (§4).

**The `core` flag lifecycle** (the selection mechanism — this is the hard part):
- A **`core` boolean** on each `LongTermFact`, **persisted in the store** (additive field).
- **Backfill once** (at implementation): one model call over *all* facts → flag ~`LUMI_FACTS_CORE_MAX` as core.
- **At session close:** the fact extraction tags each *new* fact with an initial `core` guess.
- **At session start:** re-flag — send **only the `core=true` pool** (old + the few new) to one model call,
  re-rank to the top `LUMI_FACTS_CORE_MAX`, write the flag back. **This *replaces* the Phase-0
  `_ensure_facts_digest` call** (cost-neutral, not extra) and is cheap (input is the small core pool, not all
  facts). **Boundaries/agreements are pinned** — kept even past the cap.
- **Each turn:** the prompt injects the `core=true` facts; the rest are `recall(scope=facts)`'d.

**Enabling tools:** the **new** `recall_facts` (pull) + the auto-RAG block (already surfaces fact-like lines
from messages). **Est. saving ≈ 6 KB / ~2 K-tok.**
**Risk: MEDIUM–HIGH** — facts *are* identity; a missed one reads as "she forgot me." **Mitigation:** the
`core` facts stay injected; the boundary/agreement facts are **pinned** and **never** tool-gated; only the
episodic long tail is pulled.

### P4 — Cap the thoughts window + make it pullable · −~9%
**Now:** 24 h of the thought stream, 5.8 KB (and you've been firing many directives, so it balloons).
**Change:** inject only the **last few** thoughts (the feedback loop only needs the recent tail —
`LUMI_THOUGHTS_MAX_LINES` down) and let `recall` reach the older stream (extend `recall(scope=thoughts)`, §4).
**Enabling tools:** `recall` over the thought stream (pull) + `/thoughts` (manual). **Est. saving ≈ 4 KB.**
**Risk: LOW** — thoughts are her own ephemera; losing the older ones from the prompt barely touches replies.

### P5 — Trim the style palette · −~6%
**Now:** the `# Стиль відповіді` block is 3.5 KB of authored style guidance.
**Change:** this is **static, not pullable** — just compress the palette to its load-bearing directives
(much of it is redundant phrasing). A one-time authoring pass, not a tool.
**Risk: LOW–MEDIUM** — over-trimming risks style drift; keep the budget-of-expressiveness + voice anchors.

---

## 4. The new tool — `recall_facts` (the facts-layer recall)

**The gap:** `recall()` (v0.31) searches her **messages** vector store; **nothing** searches the **facts**
layer — so facts are injected wholesale (the digest). They're the one memory tier with *no* pull path, which
is exactly why P3 can't land without a new tool.

**Proposal — `recall_facts(query, k)`** → the top-K long-term facts (and v1.6 impressions) by **meaning**.
It reuses everything `recall()` already has:
- the **`Embedder` + `VectorStore`** seams (embed each `LongTermFact` alongside messages, per-user keyed);
- the same **bounded, per-user-isolated, trusted-history** framing (a fact is her own knowledge, not
  untrusted external data — no de-id, deduped against what's already in the prompt);
- the same tool-loop wiring as `recall` / `message_context`.

This lets the facts block in §P3 shrink to the identity core while the long tail stays **fully available** —
she just fetches the relevant facts when a topic calls for them, the same "pull" `recall` gives for messages.

**Cheapest form:** instead of a new tool name, add a **`scope`** to the shipped `recall`:
`recall(query, scope = messages | facts | thoughts | all)`. One tool, one mental model, and it also unlocks
**P4** (recall over the thought stream) for free. Recommended.

**A second, smaller new tool (optional) — `profile_card()`:** returns the curated identity core on demand,
so even that can be trimmed from the always-injected prefix for turns that don't need it. Lower priority —
the identity core is cheap and high-value, so keeping it pushed is usually right.

---

## 5. Combined effect

| Tier | now (chars) | after | how |
|---|--:|--:|---|
| Conversations (detail) | 16,268 | ~2,500 | last-1 detail + gist index (P1) |
| Days + weeks | 15,126 | ~2,000 | dated index (P2) |
| Facts | 8,603 | ~2,000 | identity core + `recall_facts` (P3) |
| Thoughts | 5,806 | ~1,800 | capped window (P4) |
| Style | 3,542 | ~1,500 | compress (P5) |
| **memory+style total** | **49,345** | **~9,800** | **−~80% of the movable mass** |

System prompt **~62.6 KB → ~23 KB (~−63%)**, and — just as valuable — **a steadier prompt cache**: the
gisted tiers change far less per turn, so the cached prefix is rewritten less often (the regrowth problem the
original doc's Refinement chased).

---

## 6. Risk evaluation — quality degradation

| # | Proposal | Saving | Quality risk | Failure mode | Mitigation / retrieval path |
|---|---|--:|---|---|---|
| P1 | Gist conversations | ~26% | **MEDIUM** | non-topical "what did we say yesterday" she can't auto-recall | last-1 detail + auto-RAG + `recall`/`messages_between`; gist index keeps the thread |
| P2 | Day/week index | ~20% | **LOW–MED** | a date's *detail* not in the prompt | dated index keeps awareness; `messages_on(date)` fetches it |
| P3 | Facts core + tool | ~10% | **MED–HIGH** | a relevant episodic fact not pulled → "she forgot" | identity+boundary facts stay injected; `recall_facts` for the tail; auto-RAG backstop |
| P4 | Cap thoughts | ~9% | **LOW** | older self-thought absent from the feedback loop | recent tail kept; `recall(scope=thoughts)` for the rest |
| P5 | Trim style | ~6% | **LOW–MED** | voice/style drift if over-cut | keep voice anchors + expressiveness budget; A/B the dump |
| §4 | `recall_facts` tool | enables P3 | **LOW** (additive) | she doesn't *think* to call it | strong tool description + auto-RAG already surfaces fact-like lines |

### What must NOT move (hard line)
The retrieval-pull model is for **episodic memory**, never for **rules and identity**. These stay
**always-injected, never tool-gated** — she cannot be relied upon to *pull* a boundary she's about to cross:
- the **canon** (`## Хто ти … ## Творче «я»`), the **voice/temperament**, the **emotion palette**;
- every **ЗАБОРОНИ / boundary / safety rule** and the **"honest about her nature"** clause;
- the **identity-core facts** (name, key relationships, the standing agreements like the sleep-reminder rule).

A pull that fails on episodic detail = a slightly less specific reply. A pull that fails on a boundary =
a contract violation. Keep the second class pushed, unconditionally.

### Cross-cutting risk: "she must know to pull"
The single biggest degradation mode across P1–P4 is a turn that *would* benefit from old detail but isn't
**topically** triggered, so neither auto-RAG fires nor she thinks to call a tool. The **dated index** is the
guardrail — it keeps the *existence* of the memory in front of her so she can choose to fetch it. Never drop
a tier to **zero**; drop it to **its index**.

---

## 7. Sequencing & flags

1. **P5 + P2 + P4 first** — low-risk, mostly authoring/`.env` (`LUMI_SESSION_DAYS`, `LUMI_*_ROWS`,
   `LUMI_THOUGHTS_MAX_LINES`), instantly reversible. Banks ~35% with little risk.
2. **P1 next** — the gist tier; ship with the reconstruction test below.
3. **`recall(scope=…)` / `recall_facts`** — the one code addition; then **P3** rides on it.

Each phase behind a flag (e.g. `LUMI_MEMORY_INDEX`, `LUMI_FACTS_CORE_ONLY`), reversible, and A/B'd by
diffing a fresh `.lumi/prompt-*.md` dump before/after.

## 8. Guardrails / measurement
- **Reconstruction test:** drop a tier to its index, plant a reference to its detail in the next message,
  assert she **pulls** it (the tool fires) and answers from it. Pins every "moved to pull" claim.
- **"She forgot X" watch:** after each phase, track replies that miss a known fact/episode; a regression
  there reverts the flag. The identity-core list is the floor — it should never trip this.
- **Cache-write watch:** confirm the gisted tiers reduce prefix rewrites (the read:write ratio from the
  original doc's Refinement), not just raw size.
