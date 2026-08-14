---
name: kazma-release-notes
description: Generate release notes from git history. Use when the user asks for release notes, a changelog entry, or "what changed since the last release".
---

# Kazma Release Notes

Produce release notes from the git history of the current workspace.

## Steps

1. Determine the range:
   - `git describe --tags --abbrev=0` → last tag; if none, use the last N commits
     (ask the user for N, default 15).
   - Range = `<last-tag>..HEAD` (or `HEAD~N..HEAD`).
2. `git log --oneline --no-merges <range>` (use the `git` tool; never raw shell for history).
3. Group commits by conventional-commit prefix:
   - `feat` → **Features** · `fix` → **Fixes** · `perf` → **Performance**
   - `security` → **Security** · `chore`/`docs`/`test`/`refactor` → **Maintenance** (summarize, don't list every line)
4. One bullet per user-visible change; include the short commit hash as a link/tag when the repo is GitHub.
5. Skip commits by bots (`chore(metrics): auto-regenerate METRICS.md`).
6. Output: `## <version-or-date> — <short title>` then the grouped bullets.

## Rules

- No invented changes — only what `git log` actually shows.
- If a change has no user-visible effect, fold it into Maintenance or drop it.
- End with the list of commits included, so the user can verify.
