---
name: ship-phase
description: Full GitHub-backed delivery pipeline over ROADMAP phases. Takes one selector or a comma-separated LIST of phases/versions/ranges (e.g. v0.4, v1, v0-v2). The list names TARGETS - missing earlier phases are added automatically, the set is de-duplicated, sorted into roadmap order, and already-released phases are skipped. Per phase - generate-issues (with reconcile), upload-issues, execute-issues, review-and-fix-issues, release-version A.B.0. At the END of the run a HARDEN sweep runs BY DEFAULT (opt out with --no-harden). Gated; stops on failure; pauses for owner-run steps.
---

# Skill: Ship Phase — the full delivery pipeline

Drive the whole loop over the ROADMAP's phases. **Each phase is released before the next one is
generated**, so the next phase's issues are reconciled against the real, post-fix implementation. The
hardening sweep of deferred findings runs **by default once at the end of the run**; pass `--no-harden` to
skip it.

**The loop:**

```
PLAN = selectors → phases → de-duplicated → + missing earlier phases → sorted into roadmap order
       → minus already-released (tag vA.B.0 exists)

for each PHASE vA.B in PLAN (in order):
    0. RECONCILE  — ground vA.B in the real implementation + all prior fixes (inside generate-issues)
    1. generate-issues vA.B
    2. upload-issues @specification/roadmap/implementation/vA.B-issues.md
    3. execute-issues vA.B::phase   (implement → gates → owner checks → commit → push → close)
    4. review-and-fix-issues vA.B   (review → ranked doc → fix-now fixes → same doc)
    5. release-version A.B.0        ← RELEASE PER PHASE (tag vA.B.0)
    → REPORT the phase to chat
→ END OF RUN: HARDEN (harden-findings <plan scope> --release) — BY DEFAULT, skipped with --no-harden
→ overall summary to chat
```

This skill is a **thin orchestrator**. It sequences the sub-skills, adds the gating and the end-of-run
hardening, and releases per phase; each sub-skill keeps its own discipline.

> **This pipeline releases.** Invoking `/ship-phase` is the explicit opt-in to the automated per-phase
> releases (real tags and pushes) **and** to the HARDEN sweep. `/release-version`'s own rules still hold:
> it never downgrades and it confirms the changelog. To build without releasing, use the individual skills.

## Usage

```
/ship-phase <selector>[,<selector>…] [--no-harden]
```

A **selector** is a phase (`v0.4`, or a bare `0.4`), a version (`v1` = all its phases), or a range
(`v0-v2`, `v0.4-v1.2`). Pass one, or a comma-separated list of any mix; whitespace around commas is
ignored.

- `/ship-phase v0.4`: ship phase v0.4, plus any earlier phases that aren't released yet.
- `/ship-phase v1`: ship v1.1 and v1.2 (and any unreleased earlier phases), then HARDEN, then a summary.
- `/ship-phase v2,v1 --no-harden`: ships v1.1 → v1.2 → v2.1 → v2.2 (reordered), with no HARDEN sweep.

> **The list is a target, not the whole plan.** The roadmap is cumulative: the v2.3 streamed turn needs
> the v2.2 server skeleton, and v3 cannot be built without v2. So missing earlier
> phases are **added automatically**, and anything already released is skipped. On a repo released through
> `v2.2.0`, `/ship-phase v3.1` does exactly one phase's work.

## Instructions

### Step 0: Scope, baseline and the plan

1. **Parse the selector list.** Split on commas and trim. Each element is a phase (`vA.B`), a version
   (`vA`), or a range. Record whether `--no-harden` was passed.
2. **Resolve against the ROADMAP.** The phase universe is the `### vA.B` headings of
   [specification/ROADMAP.md](../../../specification/ROADMAP.md), grouped under their `## vA` version
   headings, **in file order**. A version selector expands to all its phases; a range to every phase it
   spans.
3. **Reject nothing silently.** If an element doesn't resolve to a real ROADMAP phase or version (a typo,
   `v9`, a reversed range `v2-v0`), name it and ask. Never drop it and ship the rest.
4. **Expand, de-duplicate and fill.** Resolve the elements to a set of phases. Then add **every earlier
   roadmap phase** before the latest one that isn't already in the set. These are requirements, not scope
   creep: report them at confirmation but don't ask permission.
5. **Sort into roadmap order.** The set is never a running order: `/ship-phase v2,v1` ships v1 first. If
   the order differs from what was typed, say so.
6. **Skip released phases** (tag `vA.B.0` exists). A phase that is partly done (issues file or GitHub
   issues exist, but no tag) resumes from its remaining steps. The sub-skills are idempotent: generate asks
   before overwriting, upload skips existing issues, execute skips closed ones, release refuses a downgrade.
7. **Preconditions:**
   - `gh` is authenticated and the repo has a GitHub remote. If either is missing, stop and offer
     `gh repo create` (run by the user) or the offline `/ship-solution`.
   - The tree is clean.
   - The automated gates are green. Never start on a red suite.
8. **Flag the owner's work up front.** Some phases carry owner steps (keys in `.env`, BotFather, the
   one-time prod migration), and many DoDs have **Manual (owner)** checks. Tell the user at confirmation
   that the run **will pause** for them.
9. **Confirm the plan once.** Show:
   - the ordered phase list, grouped by version;
   - the filled-in phases, any reordering and the skips;
   - whether HARDEN runs;
   - the expected owner pauses.

   Then run. Don't re-confirm before each sub-step; pause only for the blockers in the rules below.

**Worked example:** `/ship-phase v2.1,v1` on a repo where v0 is fully released (`v0.1.0`–`v0.5.0` tagged).

```
selectors : v2.1 · v1
expanded  : v2.1 | v1.1 v1.2
filled    : (nothing missing below v2.1 beyond the set)
skipped   : v0.1–v0.5                ← already released
ordered   : v1.1 → v1.2 → v2.1      (v2.1 was listed first; roadmap order is required)

PLAN: v1.1, v1.2, v2.1 → HARDEN v1-v2 → summary
Owner pauses: each phase's Manual (owner) DoD (keys, Telegram, live checks)
```

### Step 1: For each phase, the five steps, gated

Run the phases **strictly in sequence**: phase N+1 starts only after phase N is **released**. Invoke each
sub-skill through the **Skill tool** and follow its instructions fully.

0. **RECONCILE.** This happens inside `generate-issues` (its Step 0.5). It reads the real code, the
   earlier execution reports and the earlier code-review docs ("Fixes applied", "Architecture impact").
   Where fixes moved the code away from ARCHITECTURE.md, the code is ground truth.
1. **`generate-issues vA.B`** writes `specification/roadmap/implementation/vA.B-issues.md`.
2. **`upload-issues @specification/roadmap/implementation/vA.B-issues.md`** creates the GitHub issues, labels and
   dependency comments, and `vA.B-github-report.md`, and commits them.
3. **`execute-issues vA.B::phase`** implements the issues one per commit, runs the gates, gets the owner's
   manual checks, pushes, closes the issues, and writes `vA.B-execution-report.md`.
4. **`review-and-fix-issues vA.B`** writes the ranked review doc, fixes only the FIX NOW items (with
   regression tests) and records them in that same doc.
5. **`release-version A.B.0`** bumps the version, tags `vA.B.0` and pushes.

**Gate the hand-offs:**

- upload only after generate wrote the file;
- execute only after the issues exist;
- review only after execute finished with every issue closed (none failed or `awaiting owner`);
- release only after the review's fix-now items are committed, the gates are green, and the phase's manual
  DoD is confirmed by the owner;
- start the next phase only after this one is released.

**Every phase boundary ends pushed and clean.** Before the next phase, check that `git status` is clean and
there are no unpushed commits, and `git push` if there are. This skill **stops on failure by design**, so a
stop must never leave a phase's work on one machine only.

### Step 2: REPORT each phase to chat

After each release, report:

- the `LUMI-###` range → GitHub numbers;
- the execution commit range and the gate status;
- the manual checks the owner confirmed;
- the review findings (fixed now / deferred, with homes);
- any "Architecture impact" notes;
- the release tag.

Then continue with the next phase.

### Step 3: END OF RUN — HARDEN (default; `--no-harden` skips it)

Once the last phase in the plan is released, invoke **`harden-findings <first>-<last> --release`** through
the Skill tool, with the run's phase range.

- **What it does:** fixes every still-unfixed 🔴 HIGH / 🟠 MEDIUM finding from the run's review docs, each
  with a regression test, updates the docs in place, and ships a patch release on the latest phase (e.g.
  `v1.2.1`).
- **Held findings:** its escape hatch applies, so a fix that can't land cleanly is held with a reason, not
  forced.
- **With `--no-harden`:** skip it, and list the outstanding HIGH/MEDIUM findings and their homes in the
  summary.

Then give a short **overall summary**: phases shipped, phases skipped as already released, the HARDEN
outcome, anything that stopped early and what remains, and what's next.

## Important Rules

- **Release per phase (`A.B.0`)**, after it is built, reviewed, fix-now-fixed and owner-verified. Never
  batch phases into one release; never release mid-phase.
- **Next phase only after the previous one is released.** This strict order is what makes reconciliation
  meaningful.
- **HARDEN runs once at the end of the run, by default.** Invoking the skill is the consent; `--no-harden`
  is the opt-out.
- **Every phase boundary ends pushed and clean.**
- **Stop on failure; don't paper over it.** A failed sub-skill or a red gate halts the run. Report what
  completed and what remains. Never release a phase whose gates aren't green.
- **Pause for the owner.** Owner-run steps and manual DoD checks are confirmed by the owner, never assumed.
  Don't act on the Linux host or in Telegram unless asked.
- **Pause for real decisions:** an id or tag collision, an overwrite-or-append prompt, a held HARDEN
  finding, a failure. Routine confirmations run straight through.
- **Delegate, never duplicate.** This skill sequences `generate-issues`, `upload-issues`, `execute-issues`,
  `review-and-fix-issues`, `release-version` and `harden-findings`, and adds gating; it has no logic of its
  own.
- **The plan is roadmap-ordered and dependency-complete, always.** De-duplicate, fill the earlier phases,
  sort into ROADMAP file order, then drop the released ones. Report the fill, the reordering and the skips.
