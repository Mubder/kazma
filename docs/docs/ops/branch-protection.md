---
id: branch-protection
title: Branch protection
sidebar_label: Branch protection
description: How main is protected, which checks it requires, and why no bot writes to it
---

# Protecting `main`

**State since 2026-09-27:** `main` is protected by the ruleset
`KazmaLatestRule` (section 2): the eleven CI checks below must pass, force
pushes and deletion are blocked, and the repository admin may bypass. Only the
owner (and the agent working with the owner's credentials) pushes to `main`;
no workflow writes to it (section 3). Before that day `main` had no protection
at all.

Protection is a repository setting, and only the owner changes it. This page
records what was set, and what has to stay in step with it.

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
  to pass, with the eleven check NAMES in the table above, each added with
  the GitHub Actions source. Leave "require branches to be up to date" off:
  with direct pushes it would force a rebase-and-wait on every push for no
  extra safety (the checks run on the pushed commit itself).

The table is kept equal to CI's job names by a test
(`tests/test_branch_protection_runbook.py`). That file is not a check: a
required check must be a job name GitHub reports, or it never passes. On
2026-09-27 the ruleset was first saved with that file name as its only
required check, and targeting no branch.

**As enabled (2026-09-27):** ruleset `KazmaLatestRule`, Active, targets
`~DEFAULT_BRANCH` and `refs/heads/main`, bypass Repository admin (always),
rules: restrict deletions, block force pushes, the eleven checks. The same
settings as JSON, applied with
`gh api -X PUT repos/Mubder/kazma/rulesets/<id> --input ruleset.json`:
`name`, `target: branch`, `enforcement: active`, `conditions.ref_name.include`,
`bypass_actors: [{actor_id: 5, actor_type: RepositoryRole, bypass_mode:
always}]`, and `rules`: `deletion`, `non_fast_forward`,
`required_status_checks` with each check as
`{context: <name>, integration_id: 15368}` (15368 is the GitHub Actions app,
so only a real CI run can satisfy a check).

Verify afterwards: `gh api repos/Mubder/kazma/rulesets` lists the ruleset,
and a push from an account without the bypass is rejected with
"required status checks".

## 3. The metrics bot writes to the website, never to `main` (decided 2026-09-27)

Before protection, `.github/workflows/sync-metrics.yml` committed a fresh
`METRICS.md` straight to `main` after every push
(`chore(metrics): auto-regenerate METRICS.md [skip ci]`). Under the ruleset
that push is rejected: the commit skips CI, so the required checks never run
on it. The options were:

1. **Let the bot bypass.** Not available here: on a personal-account
   repository GitHub refuses the *GitHub Actions* app as a bypass actor
   ("Actor GitHub Actions integration must be part of the ruleset source or
   owner organization", HTTP 422, 2026-09-27). A deploy key or a GitHub App of
   our own could bypass, but then `main` would take commits that never ran
   CI, and every push raced the bot's commit (a fetch before each push).
2. **Stop committing `METRICS.md` to `main`.** **Chosen.** The workflow only
   reads this repository (`contents: read`). The website gets its metrics from
   the pull request the workflow keeps open on the website repository, once a
   day ([Website metrics](website-metrics.md)). This repository's copy is
   refreshed with `python scripts/generate_metrics.py --write` before a push,
   and `--check-readme` gates README's numbers in CI.
3. **A second branch here for the bot** (`metrics`, unprotected) was not
   needed: the only reader is the website, which takes the files by pull
   request, so a branch here would only be a second copy to keep fresh.

A pull request created with `GITHUB_TOKEN` would not trigger workflows, so a
bot pull request against `main` could never pass its required checks; the
website pull request uses its own token (`WEBSITE_TOKEN`), and the website
repository runs its own build on it.

## 4. Kept in step with the ruleset

- The check table above is held equal to CI's job names by
  `tests/test_branch_protection_runbook.py`. A job added to CI must be added
  to the ruleset too, or protection does not require it.
- AGENTS.md §31 and `docs/KNOWN_GAPS.md` record the protection (updated
  2026-09-27).
