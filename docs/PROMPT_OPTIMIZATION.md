# Prompt Optimization — Analysis & Roadmap

How the system prompt got large, what it costs, and a phased plan to cut it ~10× (facts digest →
config trims → prompt caching → RAG) **without losing character**.

> **Continued in [PROMPT_OPTIMIZATION_II.md](PROMPT_OPTIMIZATION_II.md)** — *Tool-Pull Memory*: now that the
> v0.31 retrieval toolkit (`recall`, by-date tools, `message_context`) + file search have shipped, the next
> lever is moving the verbose memory tiers from *injected* to *pulled* (index in the prompt, body behind a
> tool) + a new `recall_facts` tool. That's the successor to Phase 4 below.

> **Status (2026-06-12):** Phase 0 (facts digest) shipped. Phase 2 (caching) scheduled as roadmap
> **v0.15**; Phase 3 (RAG) is **v0.16–17**. Phases 1–3 proposed below.
> Measurements use `tiktoken o200k_base` (a close proxy for Claude's tokenizer) on two real
> prompt dumps (`core/canon/prompt-2026-06-11` and `…-after_optimization`).

---

## 1. The measurement

### Baseline per-section (BEFORE any optimization)

Total **~40,623 tokens/turn** — most of it re-sent every turn. At Opus input $5/1M ≈ **$0.20/turn**.

| Section | Tokens | % | Nature |
|---|--:|--:|---|
| **Факти (long-term facts)** | 15,893 | 39% | 610 facts, uncapped, dumped wholesale |
| **Останні розмови (sessions, detailed)** | 11,438 | 28% | all sessions in the `LUMI_SESSION_DAYS` window |
| **Що в мене на думці (thoughts, 24h)** | 6,051 | 15% | `LUMI_THOUGHTS_MAX_LINES=150` |
| Canon (lili.md + reasoning) | 2,397 | 6% | static every turn |
| Останні дні (days digest) | 1,885 | 5% | stable within a day |
| Останні тижні (weeks digest) | 1,229 | 3% | stable within a day |
| Як відповідати (emotion/relation instr) | 262 | 1% | static |
| Стиль (mega-style palette) | 396 | 1% | static |
| Настрій (mood) | 290 | 1% | stable within a day |
| Близькість (closeness block) | 150 | 0% | **per-turn** (recomputed each turn) |
| Зараз (ambient now/here) | 179 | 0% | **per-turn** (timestamp) |
| [MESSAGES] | 432 | 1% | per-turn |

**Three blocks = 82%** (facts + sessions + thoughts). Those are the targets.

### Before → After (facts digest live)

Two snapshots (06-11 23:52 vs 06-12 02:50 — *not* a perfectly controlled A/B; sessions/thoughts
also differ by time):

| Section | BEFORE | AFTER | Δ | Cause |
|---|--:|--:|--:|---|
| **Факти** | 15,893 | 2,621 | **−13,272** | **← facts digest (the optimization)** |
| Розмови (sessions) | 11,438 | 5,218 | −6,220 | time drift, not the digest |
| Думки (thoughts) | 6,051 | 1,295 | −4,756 | time drift, not the digest |
| Canon | 2,397 | 2,510 | +113 | closeness budget + relation lines |
| (others) | ~4,844 | ~4,535 | ~−300 | minor |
| **TOTAL** | **40,623** | **16,179** | −24,444 | |

**Facts count: 610 → 62.** The durable, repeatable win from the digest is **~−13K tokens/turn
(≈ −33% of the prompt)**; the rest of the −24K in that snapshot was a quieter moment.

---

## 2. The phases (by impact ÷ effort)

### Phase 0 — Facts digest ✅ shipped
One housekeeping call consolidates the accumulated facts into a compact `FactsDigest{user_id,
summary, count, ts}`, injected **instead of** all raw facts (+ a verbatim tail of facts added
since). **Non-destructive** (raw `LongTermFact`s kept); rebuilt only when facts grow by
`LUMI_FACTS_DIGEST_REFRESH`. Config: `LUMI_FACTS_DIGEST` (on), `LUMI_FACTS_DIGEST_MAX` (200).
→ **15.9K → ~2.6K** for the facts block. Boundaries/identity preserved (verified); only
redundancy + transient crisis episodes drop. See ARCHITECTURE §Identity, users, and memory scopes.

### Phase 1 — Config trims (today, **zero code**, reversible)
The two remaining volatile heavies are pure `.env`:
- `LUMI_THOUGHTS_MAX_LINES` **150 → 40** (−~4.5K on busy nights)
- `LUMI_SESSION_DAYS` **2 → 1** (−~5K; day/week digests still carry the older gist)

→ ~**−10K** on a busy prompt, instant, reversible.

### Phase 2 — Prompt caching · roadmap **v0.15** (**biggest cost lever**, ~1 day code)
Anthropic prompt caching gives **~90% off** the cached prefix on warm turns (5-min TTL — fine for
an active chat). The client already *reads* `cache_read_input_tokens` ([core/llm.py](../core/llm.py))
— it just never *writes* a breakpoint. Two changes:

1. **Reorder `build_system_prompt`** so all *stable-within-a-day* content is the cacheable prefix
   and only per-turn content trails:
   ```
   PREFIX (cache):  canon + instructions + memory(weeks/days/sessions/facts-digest)
                    + mood                            →  [cache_control: ephemeral]
   TAIL (per-turn): # Зараз (ambient time) + closeness (recomputed each turn) + thoughts + # Стиль (last) + [MESSAGES]
   ```
   Today `# Зараз` sits *early* — its per-turn timestamp invalidates everything after it for
   caching. Moving it (the closeness block, which `update_closeness` rebuilds each turn, and the
   thoughts) to the tail is the key fix; the blocks are order-independent.
2. **Set `cache_control` on the prefix** in `AnthropicClient._base_kwargs`.

→ Cached prefix ~10K @ 10% ≈ 1K effective; volatile tail ~4–6K. **No content/quality change.**
*(First turn pays +25% to write the cache; every turn after is the win.)*

#### Refinement (2026-06-15) — the in-session digest off the cached prefix
`## Раніше в цій розмові` (the in-session compaction) was *inside* the cached prefix, but it grows
every `LUMI_COMPACTION_BATCH` messages — so each compaction re-wrote the **whole** ~22K prefix at the
**2× write rate** (~1 write per ~20 turns). Measured: a 179-turn session ≈ 17 compactions ≈ ~18 cache
writes — i.e. **most of that session's cache-write bill** (read:write ratio only ~8:1). Mood (daily)
and the facts/summary digests (session-boundary) are stable mid-session, so they were *not* the cause.
**Fix:** move the in-session digest into the **per-turn tail** in `build_system_prompt`, so a
compaction never re-writes the static head (canon + instructions + facts + mood) — now written **once
per session** (+ the daily mood flip). The digest rides uncached in the tail (full input rate, but a
fraction of the prefix it used to re-cache). Pinned by
`test_in_session_digest_rides_the_tail_not_the_cache_prefix`.

### Phase 3 — RAG / semantic recall (v0.16–17, the **structural** fix)
The sessions block (~5–11K) is "dump *all* recent sessions every turn." RAG replaces it with
**retrieve the relevant past** — the roadmap phase already pulled forward (issues
**LUMI-061…065**, `specification/roadmap/implementation/v0.16-issues.md`):
- **`Embedder` + `VectorStore` seams** → embed every message/session/fact (local model, private,
  mockable; per-user isolation contract).
- Per turn: embed the incoming message → inject **top-K relevant** sessions/facts (~1–2K) instead
  of all (11K). Complements the facts digest (digest = durable gist, RAG = the exact old line).

→ Memory injection **11K → ~1–2K** *and* more relevant. Per-turn, so it lives in the volatile tail.

### Phase 4 — v1.4 facts fade & consolidation (future)
The automated successor to the manual facts digest: weight-fade + merge facts into "understanding"
in the impressions layer. Replaces the digest's hand-run consolidation. See ARCHITECTURE
§Emotional memory.

---

## 3. Target architecture (caching + RAG combine)
Complementary — **cache the static, RAG the dynamic**:
```
CACHED prefix (~10K @10% ≈ 1K):  canon + instructions + facts-digest + mood + weeks/days
VOLATILE tail (~3K):             ambient time + closeness + RAG top-K relevant past + capped thoughts + style + messages
```
Character (canon, boundaries, mood) stays in the cached prefix → cheap *and* stable; the small
closeness block (~150 tok, recomputed each turn) rides in the volatile tail.

---

## 4. Cumulative effect

| Stage | tokens/turn | ~$/turn (Opus in) |
|---|--:|--:|
| Baseline | ~40K | $0.20 |
| + Facts digest *(done)* | ~27K | $0.13 |
| + Phase 1 config | ~17K | $0.085 |
| + Phase 2 caching | ~6K-eq | $0.03 |
| + Phase 3 RAG | ~4K-eq | **$0.02** |

≈ **10× cheaper** end-to-end, plus better relevance, with no loss of character.

---

## 5. Recommended sequence
1. **Phase 1 now** — flip the two `.env` knobs.
2. **Phase 2 next** — prompt caching: highest cost-per-effort, no quality risk, makes everything
   after it cheaper. ~1 day, ships with tests + a contract note.
3. **Phase 3** — execute the v0.16–17 RAG issues already drafted.

---

## Appendix — method
- Tokens: `tiktoken o200k_base` over the `[SYSTEM]…[MESSAGES]` span of each dump (TUI chrome
  excluded). A close proxy for Claude; absolute numbers ±10%.
- Cost: Opus 4.8 input $5/1M; cached reads billed at ~10%, cache writes at ~125% (first turn).
- Facts per-day collection (store `LongTermFact.ts`): ~105 facts/day over 6 days → 633 total →
  the bloat driver the digest addresses.
