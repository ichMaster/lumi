---
name: release-version
description: Bump project version, update all version files, add RELEASE.txt entry, commit, tag, and push.
---

# Skill: Release Version

Bump the project version, update all version references, write release notes, commit, tag, and push.

## Usage

```
/release-version <version> [changelog line 1; changelog line 2; ...]
```

Examples:
- `/release-version 0.1.0` -- bump to 0.1.0, prompt for changelog
- `/release-version 0.2.0 Add three-layer memory; Lock the emotion-field contract` -- bump with provided changelog items

If no changelog items are provided, analyze uncommitted or recent commits since the last tag to auto-generate the changelog.

Version notation `A.B.C`: `A` = roadmap version (v0→0, v1→1, v2→2, v3→3), `B` = phase within that version (`v1.1`→B=1), `C` = post-release fix on that phase. So roadmap phase `vA.B` → semver `A.B.0`; a fix after it bumps `C` (e.g. v0.3 → `0.3.0`, a follow-up fix → `0.3.1`; v1.1 → `1.1.0`). Releases are cut per phase. **Never change the version without explicit user confirmation.**

## Instructions

### Step 0: Parse arguments

1. Extract the target version from the first argument (e.g., `0.1.0`)
2. Remaining arguments (separated by `;`) become changelog bullet points
3. Validate version format matches `X.Y.Z` (semver)

### Step 1: Verify prerequisites

1. Confirm we are on the expected branch
2. Confirm working tree is clean (`git status`) -- if dirty, ask the user whether to include uncommitted changes
3. Find the current version: check `VERSION`, `RELEASE.txt`, or the latest git tag
4. Verify the new version is greater than the current version

### Step 2: Generate changelog (if not provided)

If no changelog items were given as arguments:

1. Find the most recent version tag: `git describe --tags --abbrev=0`
2. Collect commits since that tag: `git log --oneline <tag>..HEAD`
3. Summarize the changes into concise bullet points (group related commits; reference the Lumi phase `vA.B` where relevant)
4. Show the generated changelog to the user and ask for confirmation

### Step 3: Update version files

Update the version string in these files:

1. **`VERSION`** (create if it doesn't exist): the bare version string, e.g. `0.1.0`
2. **`README.md`** (if present): update version reference
3. **`RELEASE.txt`** (create if it doesn't exist): prepend a new version block at the top (after the header if any):

   ```
   Version <version> (YYYY-MM-DD)
   ---------------------------
   - <changelog item 1>
   - <changelog item 2>
   - ...
   ```

   Use today's date. Keep the existing entries below unchanged.

### Step 4: Commit

Stage only the version-related files:

```bash
git add VERSION README.md RELEASE.txt
```

Commit with message:

```bash
git commit -m "$(cat <<'EOF'
Release v<version>

<1-2 sentence summary of what this release includes>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 5: Tag

Create an annotated tag:

```bash
git tag -a v<version> -m "<one-line summary of the release>"
```

### Step 6: Push

```bash
git push && git push --tags
```

### Step 7: Report

Print a summary:

```
Released v<version>
  Branch: <branch>
  Commit: <short hash>
  Tag:    v<version>
  Files updated:
    - VERSION
    - README.md
    - RELEASE.txt
```

## Important Rules

- **Never downgrade.** Refuse if the target version is less than or equal to the current version.
- **Clean tree first.** If there are uncommitted changes, ask the user before proceeding.
- **Annotated tags only.** Always use `git tag -a`, never lightweight tags.
- **Don't modify source files.** This skill only touches version metadata (VERSION, README.md, RELEASE.txt), never `core`, `tui`, `cli`, `server`, `web`, `voice`, or `mcp` code.
- **Confirm changelog.** If auto-generating changelog from commits, show it to the user before committing.
- **Plain-text release notes.** Keep `RELEASE.txt` plain text.
</content>
