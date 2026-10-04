---
name: review-and-fix-issues
description: Code-review a phase, a component or the current branch, write a criticality-ranked recommendations doc in specification/roadmap/implementation/, implement the fix-now items with regression tests, then record what was done in the SAME doc. Never releases.
---

# Skill: Review & Fix Issues

One loop over the codebase: **review → recommend → fix → record.**

1. Run a critical code review.
2. Write a single recommendations document that ranks findings by criticality and marks each **FIX NOW**
   or **DEFER →**.
3. Implement the fix-now items with regression tests.
4. **Update that same document in place**, marking what was fixed and adding a "Fixes applied" section.

The recommendations and the results live in **one document**.

This skill fixes only small, in-scope, high-value findings. It **never** bumps the version or cuts a
release (that stays with `/release-version`), and it never pulls deferred or larger work forward without
flagging it.

## Usage

```
/review-and-fix-issues [target]
```

- `/review-and-fix-issues v0.5`: review what the phase delivered (through its tag `v0.5.0` if released).
- `/review-and-fix-issues core`: scope the review to one component (`core` / `tui` / `viewer` / `telegram` / `voice` / `state` / `tests`).
- `/review-and-fix-issues`: review the **current branch**, i.e. everything built so far.

## Instructions

### Step 0: Scope and a green baseline

1. **Resolve the target:**
   - a phase (`vA.B`) / a version (`vA`) or its tag: the commits whose subjects carry that phase's `LUMI-###` ids, plus
     the files they touched;
   - a component;
   - no argument: the whole working tree.
2. **Clean tree:** check `git status` is clean and note the branch.
3. **Green baseline:** run the automated gates (`uv run ruff check .`, `uv run pytest`). If the suite is **red or flaky**, say so. Fix a clear flake first (small,
   its own commit) or raise it and ask. **Never review or fix on top of a red suite.**

### Step 1: Critical code review

Read the in-scope code and take the highest-risk areas first. Be **adversarial**: hunt for *real*
defects, not restatements of what works.

- **The emotion contract (v0.3):**
  - Is every reply path validated by the gate — streamed turns at completion, thought turns, voice turns,
    Telegram-originated turns? Unknown emotion → `calm`, intensity clamped, a repair always logged?
  - Can any path emit text to a renderer before validation?
- **Per-user isolation:**
  - Is every `Repository`/vector-store/file-sandbox/journal path keyed by `user_id`?
  - Can recall/RAG, facts, impressions or topic records ever surface another user's data?
  - Is anything global-by-design (inner life, needs, thoughts) accidentally per-user, or vice versa?
- **Off-by-default pins:**
  - Does every feature flag off → byte-identical behavior, and is that pinned by a test?
  - Is a new env var documented in `.env.example`?
- **Never competence:**
  - Can mood, closeness, needs or biorhythms bias refusal, accuracy or willingness anywhere — not just tone?
- **Untrusted content and de-identification:**
  - Are file text, web/wiki/news bodies and image content treated as data, never instructions?
  - Do outbound queries (news, web lookup, image gen) carry only the topical part — no names, no memory?
- **The file sandbox:**
  - Path traversal rejected? Writes non-destructive (create-only / append-only)? Size and loop caps enforced?
- **The file bus (v0.13):**
  - Pointer advance exactly-once (ack-after-flush, replay dedup)? No echo by construction? Catch-up caps?
- **Scheduler and ticks:**
  - Quiet hours and day caps honored on every firing path? The clock injected (DST/timezone safe)?
  - Is post-turn work off the critical path (the v1.5 async queue), and can it never drop a write silently?
- **Robustness:**
  - Does a failing model/tool/TTS/STT call degrade to a readable line instead of hanging or killing the turn?
  - Are store writes atomic; is a corrupt record skipped, not fatal; are huge inputs bounded?
- **Secrets and logging:**
  - Do keys, tokens or message texts appear in logs, exceptions or `repr`?
  - Is anything secret committed or echoed? Do tests or gates call a paid API?
- **Spec drift:** does the code diverge from the ARCHITECTURE.md contracts or the ROADMAP, or add what no phase asks for?

For each finding, capture:

- a **concrete failure scenario** (inputs → wrong result or crash);
- a `file:line` anchor;
- a **severity**: 🔴 HIGH / 🟠 MEDIUM / 🟡 LOW;
- a **proposed fix**.

Cross-check against ROADMAP.md and ARCHITECTURE.md. If a gap is already scheduled for a later phase, note that instead of
treating it as new.

### Step 2: Write the recommendations document (the plan)

Write **one** doc at `specification/roadmap/implementation/<scope>-code-review.md`, e.g. `v0.4-code-review.md`,
`core-code-review.md`, or `branch-code-review.md` for the whole tree. Include:

- a header: date, reviewer, **scope**, method;
- a **criticality-ranked summary table**: `# | Severity | Finding | Recommendation | Status`.
  Recommendation is `FIX NOW` or `DEFER → <home>`; Status starts as `⏳ pending`;
- for each finding, its failure scenario and proposed fix;
- a short **"What's solid"** section, to keep the review balanced;
- **suggested next actions**.

Decide **FIX NOW vs DEFER** honestly:

- **FIX NOW** means real, small, self-contained, high-value and in scope now: an allowlist hole, a replay on
  restart, a crash that kills sync, a secret in a log.
- **DEFER →** means larger work, or work a later phase already owns. Give the home: a later phase (`v1.1`…`v3.3`),
  `backlog` (no phase owns it) or `cleanup (/simplify)`. Do **not** pull it forward.

Commit the doc as the plan (`docs: vA.B code review`) **and push it** if a remote exists. The review is worth
keeping even if the fix pass is interrupted.

### Step 3: Implement the FIX NOW items, with tests

For each **FIX NOW** finding, in criticality order:

1. Implement the fix following `CLAUDE.md` and ARCHITECTURE.md. Keep it minimal.
2. **Add a regression test that would have caught the bug**, with the model/TTS/STT/web mocked. For a race,
   drive the interleaving explicitly.
3. **Validate:** lint and tests green. Commit only passing code.
4. **Commit** one focused change per finding: `fix(<area>): … (code review #N)`, with the running model's
   `Co-Authored-By` trailer. **Then push.** Never leave a landed fix unpushed.
5. **A contract change** updates ARCHITECTURE.md and the pinning test in the **same** commit.

If a fix turns out bigger than "fix now" (it touches a contract broadly or needs a design decision),
**stop and re-classify it as DEFER** in the doc, with the reason, and move on. Don't half-land it.

### Step 4: Update the SAME document (the result)

Edit the doc **in place**:

- **Status column:** flip it to `✅ FIXED — <commit>` for each applied fix; keep `⏳ deferred` for the rest.
- **"Fixes applied" section:** for each fix, give the change, the regression test and the verification
  (final gate status).
- **"Architecture impact" note:** add one for any fix that **changed a documented contract or
  design-relevant behavior**, and make sure ARCHITECTURE.md reflects it. The next
  `/generate-issues` or `/reconcile-issues` reads these notes.
- **"Suggested next actions":** update them. Fixes on an already-released phase suggest a patch release
  (`/release-version A.B.1`); deferred items are carried into their phase.

Commit the update (`docs: vA.B code review — fixes applied`) **and push**.

### Step 5: Report

Summarize:

- findings by severity;
- which were **fixed** (with commits) and which **deferred** (with homes);
- the final gate status.

If fixes landed on an already-released phase, suggest `/release-version A.B.<next>`, but do **not** run
it. Offer a deeper pass with `/code-review high` for confirmation.

## Important Rules

- **One document, updated in place.** Recommendations and results share a single doc.
- **Fix only the FIX NOW items.** Never pull deferred work forward without re-classifying it in the doc.
- **Every fix ships a regression test**, with the model/TTS/STT/web mocked. No test calls the network or a paid
  API.
- **Green before, green after.** Commit only code that passes the gates.
- **Record architecture deltas.** A contract change updates ARCHITECTURE.md and its test in
  the same commit, and gets an "Architecture impact" note.
- **Never release.** No version bump, no tag.
- **Never leave work unpushed** (when a remote exists). This skill can stop mid-way, so "the next step will
  push it" is not a safe assumption.
- **Ask on genuine ambiguity**: an unclear scope, or a borderline fix-now-vs-defer call.
