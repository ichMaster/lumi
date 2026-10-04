---
name: generate-issues
description: Decompose one ROADMAP phase (vA.B) into a dependency-ordered issues file at specification/roadmap/implementation/vA.B-issues.md, grounded in the real current code. The output feeds /upload-issues (GitHub flow) or /execute-issues-file (offline flow).
---

# Skill: Generate Phase Issues

Decompose one ROADMAP **phase** (`vA.B`) into a small, dependency-ordered **issues file** at
`specification/roadmap/implementation/vA.B-issues.md`. The file is the input to `/upload-issues` → `/execute-issues`
(GitHub flow), or straight to `/execute-issues-file` (offline flow).

## Usage

```
/generate-issues <phase>
```

- `/generate-issues v2.1` or `/generate-issues 2.1`: ROADMAP phase **v2.1** (prod & dev split) → `specification/roadmap/implementation/v2.1-issues.md`

One file per phase. Issue ids (`LUMI-###`) are **globally sequential** across phase files **and across
regeneration runs**. Never reset them.

## Instructions

### Step 0: Read inputs

1. Normalize the argument to `vA.B` and check it exists as a `### vA.B` heading in
   [specification/ROADMAP.md](../../../specification/ROADMAP.md). For anything else, name it and ask.
2. Read ROADMAP.md §`vA.B`: the phase's **Goal**, description, **Tasks**, **DoD** (including which items are
   **Manual (owner)**) and **Tests**, plus the version heading (`## vA`) it sits under.
3. Read [specification/ARCHITECTURE.md](../../../specification/ARCHITECTURE.md) for the mechanisms and
   **Contracts** the phase touches, and [specification/MISSION.md](../../../specification/MISSION.md) for the
   principles and **non-goals**. The scope fence is the phase itself plus the non-goals: never pull a later
   phase's work in early, and never add what no phase asks for.
4. Read `CLAUDE.md` for the acceptance gates and the design contracts that must not drift.
5. **Find the next free `LUMI-###` id. Never restart the numbering.** Check both sources and continue
   from the higher:

   ```bash
   # (a) GitHub: survives a wiped working tree (skip if the repo has no remote)
   gh issue list --state all --limit 1000 --json title \
     --jq '.[].title | capture("LUMI-(?<n>[0-9]+)").n' 2>/dev/null | sort -n | tail -1

   # (b) local issues files: covers ids drafted but not uploaded
   grep -rhoE 'LUMI-[0-9]+' --include='*-issues.md' specification/roadmap/implementation/ 2>/dev/null \
     | grep -oE '[0-9]+' | sed 's/^0*//' | sort -n | tail -1
   ```

   Pass the directory with `--include`, not a `*-issues.md` glob. Under zsh an unmatched glob aborts the
   command before `2>/dev/null` applies, and `specification/roadmap/implementation/` may not exist yet.

   The next id is `max(a, b) + 1`, zero-padded to three digits. Start at `LUMI-001` only if both sources
   come back empty. Say in the report which sources were checked: with no remote, or with `gh`
   unauthenticated, (b) alone governs.
6. If `specification/roadmap/implementation/vA.B-issues.md` already exists, ask whether to overwrite or append.

### Step 0.5: Reconcile with the real implementation

Ground the phase in what was actually built and fixed, not only in what the specification describes. Review
fixes and hardening in earlier phases may have moved the code away from the docs.

1. Read the **real current code** this phase builds on: `core/`, `tui/`, `viewer/`, `telegram/`, `voice/`, `state/`, `tests/`,
   `pyproject.toml`, `.env.example` and the authored `core/*.md` / `core/*.toml` files. Note the actual module and function names,
   signatures, config keys and env var names.
2. Read the earlier phases' `specification/roadmap/implementation/*-execution-report.md` and `*code-review*.md`,
   especially their **"Fixes applied"** and **"Architecture impact"** notes.
3. Where ARCHITECTURE.md or ROADMAP.md is stale relative to a landed fix, the **code is ground truth** for
   this phase's issues. Note the drift, and put the ARCHITECTURE.md correction into the issue that touches
   that contract.
4. For owner-run tasks (keys in `.env`, the Telegram daemons, the viewer, migrations), check what can be checked read-only,
   e.g. reading `.lumi/` bus/pointer files or `tail`ing a daemon log, and ask the owner about the rest. A
   task that is already done becomes a verification-only issue, not new work.

### Step 1: Decompose the phase

Turn the phase's tasks into a small set of issues, typically **2–5**. An owner-heavy phase may be a
single issue. Don't pad. Each issue is a coherent, independently verifiable slice:

- **Size** by complexity:
  - **S:** one function or file.
  - **M:** a feature across a few files.
  - **L:** a new component or a contract change.
- **Area**, one of:
  - `core`: `core/` (interface-independent — canon, memory, LLM seams, emotion gate, mood, thoughts).
  - `tui`: `tui/`.
  - `viewer` / `telegram` / `voice` / `state` / `scripts`: those directories.
  - `config`: `pyproject.toml`, `.env.example`, `.gitignore`, `.github/workflows/`, `core/*.toml`.
  - `tests`.
  - `docs`: the `specification/` files, `docs/`, README.md, CLAUDE.md.
  - `ops`: steps the owner performs on the Mac or the Linux host (keys, daemons, migrations).
- **Order by dependency.** The first issue is usually the gate that everything builds on. In v2.1 that is
  the `LUMI_HOME` data root everything else derives from.
- **Tests in every code issue.** Keep decision logic pure so it can be unit-tested with plain data: parsing,
  validation gates, pointer/FIFO logic, prompt assembly, schedule/tick decisions, usage aggregation. The
  model, TTS/STT, Telegram and every web service are mocked (mock `LLMClient`, fake transports), the clock
  injected, and no test touches the network — **no paid APIs in CI**.
- **Manual DoD checks** from ROADMAP.md go into the acceptance criteria as **Manual (owner):** items. The
  executor cannot pass them on its own.
- **Contract changes:** a change to anything in ARCHITECTURE.md **§Contracts** carries the ARCHITECTURE.md
  update and the test that pins it, in the **same** issue.
- **Owner-run (`ops`) issues:**
  - List the exact commands from ROADMAP.md.
  - Mark which checks Claude can run read-only from the Mac.
  - Name any repo artifact the issue produces (e.g. `server/.env.example`).
- **Stay within the phase.** No server work in v2.1, no pywebview before v2.8 beyond what its section
  asks for, nothing from MISSION.md's non-goals. "Simplicity first; complexity added by versions."

### Step 2: Write the issues file

Write `specification/roadmap/implementation/vA.B-issues.md` in English. Use **exactly** this format:

````markdown
# vA.B — Issues

Issues for phase **vA.B — {title}** (version **vA — {version title}**), derived from the Tasks and DoD in
[ROADMAP.md](../ROADMAP.md) §vA.B and the contracts in [ARCHITECTURE.md](../ARCHITECTURE.md). This file
covers one phase; ids continue from the previous phase (LUMI-{prev} → **LUMI-{first}…{last}**).

{1–3 sentences: what the phase delivers, what it builds on, what it leaves for later phases.}

## Issues Summary Table

| # | ID | Title | Size | Area | Phase | Dependencies |
|---|----|-------|------|------|-------|--------------|
| 1 | LUMI-{first} | {title} | M | agents | vA.B | -- |
| 2 | LUMI-{…} | {title} | S | tests | vA.B | LUMI-{first} |

**Size legend:** S = one function or file · M = a feature across a few files · L = a new component or a contract change

---

## Dependency Tree

```
LUMI-{first} ({gate})
  |
  +-- LUMI-{…} (…)
  |
  +-- LUMI-{…} (…)  => {phase DoD}
```

**Parallelization hints:** {what must go first; what is independent}.

---

## vA.B — {title}

### LUMI-{id} — {Title}

**Description:**
{1–3 sentences; name the files it touches.}

**What needs to be done:**
- {bullet}

**Dependencies:** {LUMI ids, or None}

**Expected result:**
{one sentence}

**Acceptance criteria:**
- [ ] {functional criterion}
- [ ] **Tests:** {the unit tests added, with the model/TTS/STT/web mocked — no paid APIs}
- [ ] **Contract:** {contract + the test that pins it + the ARCHITECTURE.md update} — *(only if a contract changes)*
- [ ] **Gates:** {which of `ruff` / `pytest` apply}
- [ ] **Manual (owner):** {DoD check from ROADMAP.md §vA.B} — *(only where the DoD needs the live system)*

---

{repeat the `### LUMI-{id} …` block per issue}

## vA.B scope notes

**Critical path:** LUMI-{…} → … → LUMI-{…}.
**Phase DoD (ROADMAP.md §vA.B):** {restate the DoD}.
**Contracts touched:** {contracts + their tests, or "none"}.
**Owner steps:** {what the owner must do on the Mac/Linux host or in Telegram, or "none"}.
**Not in this phase:** {nearby work that belongs to a later phase or to no phase}.
**Generated later:** `vA.B-github-report.md` (on upload), `vA.B-execution-report.md` (on execution).
````

### Step 3: Report

Show the user the file path, the issue count, the `LUMI-###` range, the critical path, and which id
sources were checked. Suggest the next step:

```
/upload-issues @specification/roadmap/implementation/vA.B-issues.md   # GitHub flow
/execute-issues-file vA.B                                     # offline flow
```

Do **not** create GitHub issues or commit here. This skill only writes the local file, so the user can
read and edit it first.

## Important Rules

- **One file per phase**, at `specification/roadmap/implementation/vA.B-issues.md`.
- **Ids are globally sequential** (`LUMI-###`) across phase files and regeneration runs. Resolve the next id
  as `max(GitHub, local issues files) + 1`.
- **Tests in every code issue**, with the model/TTS/STT/web mocked (no paid APIs in CI). Manual DoD checks are labeled
  **Manual (owner):**.
- **Contract change = ARCHITECTURE.md + the pinning test**, all in the same issue.
- **Stay within the phase** and outside MISSION.md's non-goals. PoC simplicity beats completeness.
- **Honor the DoD.** Together, the issues must satisfy the phase DoD in ROADMAP.md §vA.B.
- **Ask on ambiguity.** If a task is under-specified, ask before inventing scope.
- **Don't touch GitHub.** `/upload-issues` does that.
