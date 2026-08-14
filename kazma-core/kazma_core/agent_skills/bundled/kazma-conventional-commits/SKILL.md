---
name: kazma-conventional-commits
description: Conventional commit style for this repository. Use when writing commit messages, proposing a commit, or squashing PR commits.
---

# Kazma Conventional Commits

Use this commit format in the workspace.

## Format

```
type(scope): summary
```

- `type` (required): `feat` | `fix` | `docs` | `test` | `refactor` | `perf` | `chore` | `security`
- `scope` (optional but preferred): subsystem — e.g. `ui`, `server`, `memory`, `swarm`, `gateway`, `backup`, `skills`
- `summary`: imperative, ≤72 chars, no trailing period (e.g. "fix(ui): stop false CoT heading after send")

## Body (when needed)

Explain WHY and WHAT, not how. Mention user-visible impact and any behavior changes.
For fixes, describe the symptom it corrects.

## Rules

- Commit through the `git_commit`/`git` tools — never raw shell.
- Never commit secrets, `.env`, or generated artifacts.
- Reference issues as `#123` when relevant.
- Squash fixup commits before pushing; keep history linear (rebase, no merge commits on main).
