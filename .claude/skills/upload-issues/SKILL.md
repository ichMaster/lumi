---
name: upload-issues
description: Upload issues from a version issues file to GitHub one by one with proper labels and dependencies.
---

# Skill: Upload Version Issues to GitHub

Upload issues from a version issues file to GitHub one by one, with proper labels (prefixed by version) and dependencies.

## Usage

```
/upload-issues <version-issues-file>
```

Example: `/upload-issues @specification/roadmap/implementation/v1-issues.md`

A version issues file is the fine-grained breakdown of a ROADMAP version (v0–v3): each phase (`vA.B`) in [specification/ROADMAP.md](../../../specification/ROADMAP.md) is split into one or more `LUMI-xxx` issues. If the file does not exist yet, derive it from the version's phases (each phase's Goal / Tasks / DoD) first, then run this skill.

## Instructions

### Step 1: Read the version issues file

Read the provided file (e.g., `specification/roadmap/implementation/v{N}-issues.md`).

Determine from the file:
- **Version number** (n): from the filename or heading (e.g., `v1-issues.md` -> n = `1`)
- **Label prefix**: `v{n}::` (e.g., `v1::`)

Parse the **Issues Summary Table** to extract for each issue:
- `ID` (e.g., LUMI-001)
- `Title`
- `Size` (S, M, L)
- `Area` (the component: `core`, `tui`, `cli`, `server`, `web`, `voice`, `mcp`, `state`, `tests`)
- `Phase` (the ROADMAP phase it implements, e.g. `v1.2`)
- `Dependencies` (list of LUMI-xxx IDs)

Then parse each **detailed issue section** (heading with LUMI-xxx) to extract:
- `Description`
- `What needs to be done` (full content)
- `Dependencies`
- `Expected result`
- `Acceptance criteria` (checklist — should align with the phase DoD in ROADMAP.md)

### Step 2: Confirm with user

Show the user a summary of what will be created:
- Number of issues
- Label prefix (e.g., `v1::`)
- Full list of labels that will be created
- Ask for confirmation before proceeding

### Step 3: Create labels (if they don't exist)

All labels MUST be prefixed with `v{n}::` (version number).

Label format: `v{n}::{category}:{value}`

Use `gh` to create these labels if they don't already exist (version titles: v0 — TUI; v1 — Server platform; v2 — Face, voice, shared mind & dictation; v3 — Animated Лілі & MCP web search):

```bash
# Version label
gh label create "v1::version:1" --color "0E8A16" --description "Version v1 — Server platform" 2>/dev/null || true

# Size labels
gh label create "v1::size:S" --color "28A745" --description "Small (1-2 days)" 2>/dev/null || true
gh label create "v1::size:M" --color "FFC107" --description "Medium (3-5 days)" 2>/dev/null || true
gh label create "v1::size:L" --color "DC3545" --description "Large (5-8 days)" 2>/dev/null || true

# Area labels (one per component touched in this version)
gh label create "v1::area:core"   --color "6F42C1" 2>/dev/null || true
gh label create "v1::area:server" --color "1D76DB" 2>/dev/null || true
gh label create "v1::area:tui"    --color "0E8A16" 2>/dev/null || true
# ... cli / web / voice / mcp / state / tests as needed
```

### Step 4: Create issues ONE BY ONE

**IMPORTANT:** Issues must be created one at a time, sequentially. After creating each issue:
1. Show the user the result (issue number, URL)
2. Proceed to the next issue immediately (do not wait for confirmation between issues)

For each issue (in order from the summary table):

1. Build the issue body in markdown:

```markdown
## Description
{description from the detailed section}

## What needs to be done
{full content from the detailed section}

## Dependencies
{dependency list, with references to already-created issue numbers}

## Expected result
{expected result from the detailed section}

## Acceptance criteria
{checklist from the detailed section}

---
**ID:** {LUMI-xxx}
**Size:** {S/M/L}
**Version:** v{n}
**Area:** {core/tui/cli/server/web/voice/mcp/state/tests}
**Phase:** {vA.B from ROADMAP}
```

2. Create the issue with a single `gh issue create` command (one issue per command, never batch):

```bash
gh issue create \
  --title "LUMI-xxx: {title}" \
  --label "v1::version:1,v1::size:{S/M/L},v1::area:{area}" \
  --body "$(cat <<'BODY'
{issue body}
BODY
)"
```

3. Record the mapping: LUMI-xxx -> GitHub issue #number

4. Report to user: `Created LUMI-xxx -> #{number}: {title}`

5. If the issue has dependencies on already-created issues, add a comment:

```bash
gh issue comment {issue-number} --body "Blocked by #{dep-issue-number} (LUMI-xxx)"
```

6. Move to the next issue.

### Step 5: Generate report

After all issues are created, generate a report file at:
`specification/roadmap/implementation/v{N}-github-report.md`

Content:

```markdown
# Version v{n} -- GitHub Issues Report

**Uploaded:** {date}
**Repository:** {github repo URL}
**Total issues:** {count}

## Issue Mapping

| LUMI ID | GitHub # | Title | Phase | Labels | URL |
|---------|----------|-------|-------|--------|-----|
| LUMI-001 | #5 | Skeleton and canon | v0.1 | v0::version:0, v0::size:S, v0::area:core | {url} |
| ... | ... | ... | ... | ... | ... |

## Labels Created

- v{n}::version:{n}
- v{n}::size:S, v{n}::size:M, v{n}::size:L
- v{n}::area:{list}
```

### Step 6: Report to user

Show the user:
- Total issues created
- Link to the GitHub issues page
- Path to the generated report file

## Error Handling

- If `gh` is not authenticated, tell the user to run `gh auth login`
- If the repo has no GitHub remote yet, tell the user to create one (`gh repo create`) before uploading
- If an issue already exists with the same title, skip it and note in the report
- If label creation fails, continue (labels may already exist)
- On any failure, report what was created so far and what remains
</content>
