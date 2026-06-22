---
name: review-facts
description: Review the whole long-term facts DB on demand and mark stale/duplicate/irrelevant facts obsolete (propose → human review → apply). Non-destructive; never auto-obsoletes a core fact.
---

# Skill: Review Facts (hygiene)

Review **all** of a user's long-term facts and mark the **obsolete** ones — duplicates, outdated
(superseded by a newer fact), and irrelevant/ephemeral — so they stop reaching the prompt while staying
in the store for audit. This is the **propose → human review → apply** flow for the v0.36 `obsolete`
flag (LUMI-145). It runs offline on **Opus** (this agent), not the app's model.

A fact marked `"obsolete": true` is filtered out of **every** fact path: the core static block, the auto
fact-RAG (`# Релевантні факти`), and `recall(scope=facts)`. It is **kept** in the store (non-destructive,
reversible — set the flag back to `false` to restore).

## Usage

```
/review-facts [user_id] [--store <path>] [--apply]
```

- `user_id` — whose facts to review (default `owner`).
- `--store` — path to the store (default `.lumi/store.json`).
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

### 4. Show the proposal + confirm

Print the **Propose obsolete** list (fact — bucket — reason) and the **Core — needs your call** list.
Show counts: `N proposed obsolete, M core-flagged for review, K kept`. Ask:
**"Apply these N obsolete marks? (the core-flagged ones are left for you to decide)"** — wait for an
explicit yes.

### 5. Apply (only on confirmation)

For each confirmed fact, set `"obsolete": true` on the matching entry in `facts[user_id]` (match by exact
`fact` text; if duplicate texts exist, match all). Leave every other field unchanged. Do **not** touch
`core: true` facts. Write `store.json` back with the same shape (`json.dump(..., ensure_ascii=False,
indent=2)`), atomically if possible (write a temp file, then replace).

For the **Core — needs your call** list: if the user explicitly approves a specific core fact, set its
`obsolete` (its `core` stays as-is). Otherwise leave it.

### 6. Report

Print a summary: backup path, counts (obsolete applied, core left for review, kept), and a one-line
reminder that it's reversible (set `obsolete` back to `false`) and that the next session start will re-rank
the core over the now-cleaner pool.

## Notes

- The review is **judgment** — when unsure whether a fact is stale, **keep it** (false-obsolete reads as
  "she forgot me"). Bias toward keeping; only propose clear cases.
- Duplicate/outdated detection is by **reading** (that's why this runs on Opus). The v0.36 fact embeddings
  (LUMI-141) can pre-cluster near-duplicates at large scale, but the final call is always the agent's +
  the human's.
- Re-runnable: a second pass sees the already-obsolete ones (skip them) and only judges the live pool.
</content>
