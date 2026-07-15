---
name: review-facts
description: Review the whole long-term facts DB on demand — mark stale/duplicate/irrelevant facts obsolete AND rerank the identity-core (select the top-N durable facts as core=true). Propose → human review → apply. Non-destructive; runs offline on Opus. This skill is the SOLE reranker of the identity-core (there is no in-app re-rank).
---

# Skill: Review Facts (hygiene + identity-core rerank)

Two jobs over a user's long-term facts, both offline on **Opus** (this agent), not the app's weak
housekeeping model:

1. **Hygiene** — mark the **obsolete** ones (duplicates, outdated, irrelevant/ephemeral) so they stop
   reaching the prompt while staying in the store for audit (the v0.36 `obsolete` flag, LUMI-145).
2. **Rerank the identity-core** — select the **top-N durable, relevant** facts and set `core: true` on
   exactly those (unpinning the rest). This skill is the **sole reranker of the identity-core** — the
   old in-app session-start re-rank (a cheap model that under-returned and silently collapsed a curated
   core) has been **removed**, so nothing in the app ever re-touches what this skill chooses.

Both are **propose → human review → apply**. A fact marked `"obsolete": true` is filtered out of **every**
fact path (core block, auto fact-RAG `# Релевантні факти`, `recall(scope=facts)`); a `core: true` fact is
injected into `## Факти` (up to `LUMI_FACTS_CORE_MAX`). Everything is **kept** in the store and reversible
(flip the flag back).

## Usage

```
/review-facts [user_id] [--store <path>] [--pin N] [--apply]
```

- `user_id` — whose facts to review (default `owner`).
- `--store` — path to the store (default `.lumi/store.json`).
- `--pin N` — also **rerank the identity-core**: pick the top-`N` durable facts and set them `core: true`
  (unpin the rest). Omit to do hygiene only. Pair with `LUMI_FACTS_CORE_MAX=N` so all N show.
- `--apply` — skip the dry-run and apply after showing the proposal (still requires explicit confirmation).

## Hard rules

- **Never auto-obsolete a `core: true` fact.** If a core fact looks stale (e.g. a moved-city boundary),
  **flag it for the human's explicit decision** in a separate "core — needs your call" list; do not set it.
- **Non-destructive.** Only ever set `"obsolete"` (and never delete a fact or edit its text); the raw fact
  stays in the store.
- **Store-free discipline.** The app holds `store.json` in memory and rewrites it on every turn — a
  concurrent write would clobber your edit. **Stop the running TUI / bridge / voicer first**, then back up
  `store.json`, then apply.
- **Per-user.** Only touch `facts[user_id]`; never another user's facts.
- **Propose first.** Always show the full proposal with reasons and get explicit confirmation before writing
  (even with `--apply`).
- **Rerank rules (`--pin N`):** pick **verbatim** existing fact texts (so they match on write); prefer the
  **clearest** phrasing of each distinct topic; **never pin an impression** (Лілі's own musing —
  `сприйняття …:` / `ставлення до себе:` / her lyrical self-quotes — those are the v1.14 impressions
  layer, not facts about the user); set `core: true` on exactly the chosen N and `core: false` on every
  other live fact (no stray pins). The skill's pin set is the **source of truth** — that's why the in-app
  re-rank must be off.

## Steps

### 1. Preconditions

1. Confirm **no Lumi process is running** (TUI, `telegram→inbox`/`outbox→telegram` daemons, voicer,
   dictator). Ask the user to stop them if unsure — a live write will clobber the edit.
2. **Back up** the store: copy `store.json` → `store.json.bak-<today>` (use the date from context; do not
   call a clock). Report the backup path.

### 2. Load the facts

1. Read `store.json` and pull `facts[user_id]` (each entry: `{user_id, fact, meta, confidence, ts, core,
   obsolete}`). Skip entries already `obsolete: true`.
2. Report the count (live / core / already-obsolete). With many facts (hundreds+), process in **batches**
   (~50–100 per pass) so each reasoning step stays sharp; de-dup detection compares **across** batches, so
   keep the full list in view when judging duplicates.

### 3. Propose obsolete candidates (the review)

Go through the facts and classify each candidate into exactly one bucket, **with a one-line reason**:

- **duplicate** — the same information as another fact (a paraphrase/near-identical). Keep the clearest /
  most recent one; propose the rest. Cite the kept fact.
- **outdated** — superseded by a newer fact (e.g. "lives in Lviv" → a later "moved to Kyiv"). Propose the
  old one; cite the superseding fact + its newer `ts`.
- **irrelevant** — ephemeral / trivia that was never durable ("was tired today", a one-off).

Build three lists:
1. **Propose obsolete** — `(fact, bucket, reason)` for each non-core candidate.
2. **Core — needs your call** — any `core: true` fact that looks stale (shown separately; **not**
   auto-applied).
3. **Kept** — just the count (don't dump them).

### 3b. Rerank the identity-core (`--pin N` only)

Over the **live, durable** facts (exclude obsolete + the impressions bucket), select the **top-`N` most
important, distinct** facts about the user — the identity-core. Cover the spread, don't stack near-dupes:
**biographical** (name, birth data, location, work, family), **the ongoing project / relationship**,
**stable tastes & interests**, **communication style**, and **standing agreements / boundaries**. Pick
the **clearest verbatim** fact for each topic. Bias to genuinely durable, self-descriptive facts; skip
ephemeral state and skip impressions (per the rerank rules above).

Build the **Propose pins** list — the `N` chosen fact texts (verbatim), grouped by category. Note which
currently-`core` facts fall **out** of the new set (they'll be unpinned).

### 4. Show the proposal + confirm

Print the **Propose obsolete** list (fact — bucket — reason) and the **Core — needs your call** list, and
(with `--pin N`) the **Propose pins** list. Show counts: `N proposed obsolete, P pins, M core-flagged for
review, K kept`. Ask:
**"Apply — obsolete N, pin P? (the core-flagged ones are left for you to decide)"** — wait for an explicit
yes.

### 5. Apply (only on confirmation)

Load the store fresh, then in `facts[user_id]` (match by exact `fact` text; match all if duplicate texts):
- **Obsolete:** set `"obsolete": true` on each confirmed candidate (and `"core": false` — obsolete is never
  core). Leave every other field unchanged.
- **Pins (`--pin N`):** set `"core": true` on each of the `P` chosen (and `"obsolete": false`); set
  `"core": false` on every **other** live fact, so the core set is **exactly** the chosen `P` (no stray
  pins survive). A pin is never also obsoleted.

Write `store.json` back with the same shape (`json.dump(..., ensure_ascii=False, indent=2)`), **atomically**
(temp file, then `os.replace`). For the **Core — needs your call** list: only if the user explicitly
approves a specific fact, set its `obsolete`.

### 6. Report

Print a summary: backup path, counts (obsolete applied, **pins set**, core left for review, kept), and a
reversibility reminder (flip `obsolete`/`core` back). The app has no in-app re-rank, so the pin set is
safe across restarts — nothing re-touches it. Remind to set `LUMI_FACTS_CORE_MAX ≥ N` so all pins show.

## Notes

- The review is **judgment** — when unsure whether a fact is stale, **keep it** (false-obsolete reads as
  "she forgot me"). Bias toward keeping; only propose clear cases.
- Duplicate/outdated detection is by **reading** (that's why this runs on Opus). The v0.36 fact embeddings
  (LUMI-141) can pre-cluster near-duplicates at large scale, but the final call is always the agent's +
  the human's.
- Re-runnable: a second pass sees the already-obsolete ones (skip them) and only judges the live pool.
</content>
