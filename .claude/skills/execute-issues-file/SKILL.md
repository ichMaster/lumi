---
name: execute-issues-file
description: Execute one phase's issues straight from its local specification/roadmap/implementation/vA.B-issues.md (no GitHub). Implement -> gates -> owner confirmation for manual checks -> commit -> push (if a remote exists) for each issue in dependency order, then write vA.B-execution-report.md. The offline counterpart of execute-issues.
---

# Skill: Execute Issues From File

Execute one phase's issues **straight from its issues file**, `specification/roadmap/implementation/vA.B-issues.md`,
with **no GitHub involvement**: no issue lookup and no closing. Each issue is implemented, validated,
committed and pushed in dependency order, and the run ends with an execution report.

This is the offline counterpart of `/execute-issues`. The discipline is the same; only the issue list comes
from the markdown file instead of `gh issue list`.

## Usage

```
/execute-issues-file <vA.B | path-to-issues-file> [--issue LUMI-###] [--dry-run]
```

- `/execute-issues-file v0.5` → executes `specification/roadmap/implementation/v0.5-issues.md`.
- `/execute-issues-file @specification/roadmap/implementation/v1.1-issues.md`.
- `--issue LUMI-###`: only that issue. Its file-listed dependencies must already be committed.
- `--dry-run`: print the execution plan without changing anything.

## Instructions

### Step 0: Verify prerequisites and read the file

1. **Branch and tree:** note the current branch and check `git status`.
   - If the only uncommitted file is this phase's issues file, commit it first (`docs: add vA.B issues`).
   - Any other uncommitted change: stop and ask.
2. **Remote:** check `git remote -v`. Without a remote, the run commits but doesn't push; say so up front.
3. **Read the issues file:** resolve the target to `specification/roadmap/implementation/vA.B-issues.md` and read the
   summary table, the Dependency Tree and every `### LUMI-###` section. **No `gh` is used.**
4. **Read the spec:** [specification/ROADMAP.md](../../../specification/ROADMAP.md) §vA.B (Goal, Tasks, DoD,
   Tests), [specification/ARCHITECTURE.md](../../../specification/ARCHITECTURE.md) (contracts) and
   [specification/MISSION.md](../../../specification/MISSION.md) (non-goals), plus `CLAUDE.md` (the gates and
   the rules that are easy to break).
5. **Green baseline:** run the automated gates that apply, so a later failure can be attributed.

### Step 1: Build the execution queue from the file

- **Order:** parse the ids and titles from the summary table and order them by the Dependency Tree.
- **Skip what's done:** an issue whose id already appears in a commit subject (`git log --grep "^LUMI-###:"`)
  is done; skip it. This is what makes the run resumable.
- **`--issue`:** execute only that issue, after checking that its dependencies are committed.

Show the ordered plan and proceed. With `--dry-run`, stop here.

### Step 2: Execute each issue, in dependency order

1. **Announce:** `--- Starting LUMI-###: {title} ---`.
2. **Read** its section: what needs to be done and the acceptance criteria.
3. **Implement** per `CLAUDE.md` and ARCHITECTURE.md, routed by component. The routing is the same as
   `/execute-issues` Step 2c:
   - `core/` is interface-independent: canon, memory, the `LLMClient` seam, the emotion gate, mood,
     thoughts — no TUI/web/Telegram concern ever leaks in; decisions kept as pure functions, the clock injected.
   - `tui`/`viewer`/`telegram`/`voice`/`state` follow the repo layout in CLAUDE.md; the bridge daemons stay
     dumb (no core logic); storage goes through `Repository`, keyed by `user_id`.
   - The locked contracts (the emotion channel, per-user isolation, off-by-default → byte-identical pins)
     are contracts: changing one changes its contract test, in the same commit.
   - `ops` issues produce their repo artifacts plus a numbered checklist for the owner. Don't act on the
     Linux host or in Telegram unless the owner asks.
   - A **contract change** updates ARCHITECTURE.md and its pinning test in the same commit.
4. **Validate:**
   - `uv run ruff check .` clean and `uv run pytest` green, with the model/TTS/STT/web mocked (no paid APIs).
   - For **Manual (owner)** criteria, run the read-only checks yourself (`.lumi/` bus/pointer files,
     daemon logs) and ask the owner to perform
     and confirm the rest.
   - Record each result as pass / fail / n/a, and each manual check as `confirmed by owner` /
     `pending owner`.
5. **Commit** (one issue = one commit, only when the gates are green):

   ```bash
   git commit -m "$(cat <<'EOF'
   LUMI-###: {title}

   {1-2 sentence summary of what was implemented}

   Co-Authored-By: <the running model's trailer> <noreply@anthropic.com>
   EOF
   )"
   ```

   There is no `Closes #…` line, since there is no GitHub issue. An `ops` issue with no repo artifact has no
   commit; it is done once the owner confirms.
6. **Push:** `git push` if a remote exists.
7. **Log** the id and title, commit, files, gate results, manual checks and status (`completed` /
   `awaiting owner` / `failed` / `skipped`).

An issue whose code is committed but whose owner checks are pending is `awaiting owner`. Continue with
issues that don't depend on it.

### Step 3: Handle failures

On a failed implementation or a red gate:

1. Don't commit.
2. Revert tracked changes with `git checkout -- .` and delete **by name** the new files this issue created.
   Never `git clean`.
3. Log the failure.
4. Ask whether to continue with the next independent issue or stop.

### Step 3b: No automatic version bump

Never touch `VERSION`, `RELEASE.txt`, `pyproject.toml`'s version or tags here; that is `/release-version`.
A phase with failed, skipped or `awaiting owner` issues is not releasable.

### Step 4: Write the execution report

Write `specification/roadmap/implementation/vA.B-execution-report.md` with the same structure as `/execute-issues`
Step 4:

- a status summary table;
- a per-issue table (LUMI id · title · status · commit · files · gates · manual);
- detailed results;
- next steps.

There is no GitHub column. Commit it (`docs: vA.B execution report`, with the trailer) and push if a remote
exists.

## Important Rules

- **File-driven, no GitHub.** The issue list, details and order come from `vA.B-issues.md`. Never run
  `gh issue list`/`create`/`close`, and never write `vA.B-github-report.md`.
- **One issue = one commit**, one issue at a time, in dependency order.
- **No broken code.** Commit only when lint and tests are green.
- **Tests ship with the feature**, with the model/TTS/STT/web mocked. No test or gate calls the network or a paid
  API.
- **Manual checks need the owner.** Never report one as passed on your own.
- **Contracts stay stable**: ARCHITECTURE.md and the pinning test change together.
- **Secrets stay out.** Never print `.env`, `server/.env`, `server_con.yaml` or anything under
  `state/`; no secrets in argv, logs or commits; no message, summary or memory texts in logs.
- **Ask on ambiguity.** If an issue's scope is unclear, ask rather than guess.
- **Progress updates.** Print a short status line after each issue.
