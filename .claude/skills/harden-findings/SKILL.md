---
name: harden-findings
description: Sweep the code-review reports in specification/roadmap/implementation/ for still-unfixed HIGH/MEDIUM findings, fix each with a regression test, update the reports in place, and (opt-in) ship the result as a patch release. Runs standalone, or at the end of a /ship-phase or /ship-solution run.
---

# Skill: Harden Findings

Close out the serious code-review findings that were **deferred** (real, but not fixed at review time).
Sweep the review reports, fix every remaining **🔴 HIGH / 🟠 MEDIUM** finding with a regression test,
record the results in the same reports, and optionally cut a patch release so the hardening ships.

The **invocation of whatever ran this skill** carries the consent:

- **Called directly:** invoking it is the consent.
- **Called from `/ship-phase` or `/ship-solution`:** the orchestrator's own invocation is the consent. Both
  sweep once at the end of the run by default, and `--no-harden` opts out.

## Usage

```
/harden-findings [scope] [--release]
```

- `scope` is an optional filter: a phase (`vA.B`), a version (`vA`), a range (`v1-v2`), or omitted for **all**
  `specification/roadmap/implementation/*code-review*.md` reports.
- `--release` cuts the patch release automatically once the fixes land: the next `A.B.C` on the latest
  released phase the fixes touch (e.g. `1.2.0` → `1.2.1`, tag `v1.2.1`). Without it, finish by
  **recommending** `/release-version`. Never bump a version without explicit consent.

Examples: `/harden-findings` · `/harden-findings v1` · `/harden-findings v1-v2 --release`

## Instructions

### Step 0: Baseline and the finding list

1. **Tree and baseline:** check the tree is clean and the automated gates are green (lint, tests, and
   nothing else). Never harden on a red suite: fix a clear flake first (its own commit) or
   raise it.
2. **Collect** the code-review reports, filtered by `scope` if given.
3. **Select** every finding with severity **🔴 HIGH or 🟠 MEDIUM** whose Status is not FIXED, **regardless of
   its `DEFER → <home>`**. Ignore 🟡 LOW; it stays deferred to its documented home.
   - Exception: a finding homed to a phase that **hasn't been built yet** stays deferred, since its code
     doesn't exist. List it as outstanding.
4. **Order** the queue HIGH before MEDIUM, then by report.
5. Show the queue (finding, severity, source report, proposed fix), then proceed. If it is empty, say so and
   stop.

### Step 1: Fix each finding, gated

For each finding, in order:

1. **Implement the fix** following `CLAUDE.md` and ARCHITECTURE.md. One finding, one minimal change.
2. **Add a regression test that would have caught the bug.** A race gets a test that drives the
   interleaving; an isolation hole gets a test with a second `user_id`; an allowlist hole a test with a
   forbidden sender. The model/TTS/STT/web are mocked — no paid APIs.
3. **Validate:** lint and tests green. Commit only passing code.
4. **Commit** one focused change, `fix(<area>): … (code review #N)`, with the running model's
   `Co-Authored-By` trailer. A **contract change** carries the ARCHITECTURE.md updates and
   the pinning test in the same commit.
5. **Update the source report in place:**
   - Flip the finding's Status to `✅ FIXED — <commit>`.
   - Extend its **"Fixes applied"** section with the change, the test and the verification.
   - Add an **"Architecture impact"** note if documented behavior changed; the next reconcile reads it.
   - If the finding was homed to a later phase, note there that it is **already addressed**, so it isn't
     redone.

**Escape hatch:** if a fix can't land safely now (it needs a design decision, or would grow into a large
change), **do not force a half-baked fix**. Leave the finding deferred, record *why* it is held in the
report, and raise it with the user. Prefer fixing; hold only when a clean landing isn't possible.

### Step 2: Final validation and the optional patch release

1. Re-run the full automated gates once after the sweep. They must be green and deterministic.
2. Commit the report updates (`docs: harden findings`) and push if a remote exists.
3. **Release:** if `--release` was passed, or the user confirms when asked, invoke `/release-version` for
   the patch bump on the latest released phase the fixes touch. Otherwise recommend the command and stop.

### Step 3: Report to chat

Summarize:

- the findings fixed (severity, commit for each);
- the findings **held** by the escape hatch, with why;
- the findings still deferred because their phase isn't built;
- the LOW findings left untouched;
- the final gate status;
- the patch tag, or the recommended `/release-version` command.

## Important Rules

- **Only HIGH and MEDIUM.** LOW findings stay deferred to their documented homes.
- **Invocation is consent**, at whatever level the invocation happened. The **release** is never
  implicit: a patch needs `--release` or an explicit confirmation, and only on an already-released phase.
- **One finding = one focused commit**, each with a regression test. No test calls the network or a paid
  API.
- **Green before, green after.** Start green, commit only passing code, and end with a full green run.
- **Update the same review docs in place**: Status, "Fixes applied", "Architecture impact". No new
  parallel documents.
- **Contracts stay recorded.** A contract change updates ARCHITECTURE.md and its test in
  the same commit.
- **Stop on failure.** A red gate on any fix halts the sweep. Report what landed and what remains; never
  paper over it.
