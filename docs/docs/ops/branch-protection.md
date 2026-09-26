---
id: branch-protection
title: Branch protection
sidebar_label: Branch protection
description: How to protect main without breaking the owner's direct pushes or the metrics bot
---

# Protecting `main`

**State on 2026-09-26** (read with `gh api`): `main` has no branch protection
and no ruleset; the repository does not allow auto-merge. Every CI job named
below has been green on every push of the day, so requiring them costs nothing
a normal push is not already paying.

Protection is a repository setting, and only the owner changes it. This page
is the checklist for doing it without breaking the two things that push to
`main` directly today: the owner (and the agent working with the owner's
credentials), and the **Sync Metrics** bot.

## 1. The checks to require

Use these names exactly — they are the job names GitHub reports as status
checks for `.github/workflows/ci.yml`:

| Check | What it holds |
|---|---|
| `Tests` | the full suite (`scripts/fast_test.py --chunks 4`) |
| `Tests (Postgres backend)` | every `@pytest.mark.postgres` test on a real Postgres |
| `Unified turn lifecycle (GATE)` | the chat turn in real browsers: approvals, reloads, watchers, chunked delivery |
| `Playwright smoke` | health, composer, HITL view model, every page loads clean |
| `Windows selector-loop subset` | the Windows event-loop rules (§23) |
| `Compile Check` | `py_compile` over every `.py` |
| `Every module imports on its own` | each product module imported alone, in a fresh interpreter (`scripts/check_fresh_imports.py`) |
| `JS Syntax Check` | `node --check` over the static JS |
| `Lint (Ruff)` | advisory findings reported; the step itself must pass |
| `Security Scan` | bandit HIGH gate over product, `tests/`, `scripts/` |
| `Shipped wheel and locked dependencies` | the wheel builds and its pins resolve |

## 2. The ruleset

**Settings → Rules → Rulesets → New branch ruleset**

- **Enforcement:** Active. **Target:** the default branch (`main`).
- **Bypass list:** *Repository admin*. This keeps the owner's direct pushes —
  the way this repository works (AGENTS.md, and the agent pushes with the
  owner's credentials) — while everyone else must pass the checks.
- **Rules:** Restrict deletions; Block force pushes; Require status checks
  to pass, with the eleven checks above (`tests/test_branch_protection_runbook.py`
  keeps that table equal to the CI jobs). Leave "require branches to be up to
  date" off: with direct pushes it would force a rebase-and-wait on every
  push for no extra safety (the checks run on the pushed commit itself).

Verify afterwards: `gh api repos/Mubder/kazma/rulesets` lists the ruleset,
and a push from an account without the bypass is rejected with
"required status checks".

## 3. The metrics bot — decide before enabling

`.github/workflows/sync-metrics.yml` regenerates `METRICS.md` after each push
and commits it straight to `main` with `GITHUB_TOKEN`
(`chore(metrics): auto-regenerate METRICS.md [skip ci]`). Under the ruleset
that push is rejected: its commit skips CI, so the required checks never run
on it. Pick one:

1. **Let the bot bypass.** If the ruleset's bypass picker offers the
   *GitHub Actions* app, add it. Smallest change; the bot keeps working as
   today.
2. **Stop committing `METRICS.md` to `main`.** Keep the workflow's
   website-sync half (it opens a PR on the site repo with its own token) and
   drop the framework commit. `python scripts/generate_metrics.py --write`
   before a push keeps the framework copy current, and `--check-readme`
   already runs before every push.
3. **Not recommended:** make the bot open a PR. A PR created with
   `GITHUB_TOKEN` does not trigger workflows, so its required checks would
   never run and it could never merge without a separate token.

Whichever is chosen, run the next push through it once and confirm both a
green CI run and (for option 1) the bot's metrics commit landing.

## 4. After enabling

- Update AGENTS.md §31 ("`main` has no branch protection at all") and the
  KNOWN_GAPS entry "Branch protection is the owner's call".
- The `Unified turn lifecycle (GATE)` job becomes the required check AGENTS.md
  §31 says it is not yet — reword that paragraph too.
