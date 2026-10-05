---
id: website-metrics
title: Website metrics
sidebar_label: Website metrics
description: How kazma.ai gets its numbers from this repository, and the metrics.json layout it reads
---

# Website metrics

kazma.ai shows figures measured from this repository: lines of code, the test
count, commits, contributors. (Its pages and claims follow
[Website sync](website-sync.md); the numbers arrive by themselves.) They reach
the site one way:

1. **Daily** (04:23 UTC), or by hand (Actions → **Sync Metrics** → **Run
   workflow**), `.github/workflows/sync-metrics.yml` installs the project the
   way CI's Tests job does and runs
   `scripts/generate_metrics.py --out-dir … --require-collected`. That writes
   `METRICS.md` (for people) and `metrics.json` (for the site's build) for the
   head of `main`.
2. `scripts/sync_site_metrics.py` copies both into `src/data/` of the website
   repository (`Mubder/KazmaAI`, private) on the branch
   `chore/auto-refresh-metrics`, and makes ONE **open** pull request carry that
   commit. It reads the pull request back and fails the run if none does.
3. Someone merges that pull request, and Cloudflare Pages deploys the site.
   Nothing merges automatically.

The workflow only reads this repository (`contents: read`): `main` is
protected ([Branch protection](branch-protection.md)) and gets no bot commits.
This repository's own `METRICS.md` is refreshed with
`python scripts/generate_metrics.py --write` before a push. It names the
commit it was made from, so it always describes the parent of the commit that
carries it. README's headline numbers are gated in CI (`--check-readme`).
README's test count is the one pytest collects, the figure the site shows as
"tests", so the gate collects too (seconds); a collection that fails fails the
gate with pytest's reason.

## Fresh metrics in Kazma chat and X posts

For a new Kazma metrics post, use the native **`repository_metrics`** tool
first. It measures the source installation containing Kazma with the same
`scripts/generate_metrics.py` collector used by the website. It does not read
the cached `METRICS.md`, change repository files, import tests, or loosen the
shell interpreter restrictions. It measures Kazma, even when your active
workspace is another project; a wheel installation without the source checkout
reports that these measurements are unavailable.

The result includes the exact commit, measurement time, tracked-change status,
Python file/line counts, package count, test files, source test functions and
commit count. If the source changes during measurement, the tool refuses the
figures and asks for a retry. Tracked changes mean working-tree measurements,
not a pristine committed release.

The tool returns **`collected_tests`** from the tracked collection receipt
written by a successful `python scripts/generate_metrics.py --write --require-collected`
run in development/CI. Its SHA-256 fingerprint must match the current tracked
source, tests, fixtures and collection configuration. Checkout line endings
and merge-only commits do not invalidate identical inputs. Provenance includes
the collection time, source commit, Python version and platform. The native
tool never runs pytest on the live server.

Use that verified count for the headline, including parameterized cases.
Source test functions are definitions, not a replacement for collected cases;
neither count proves tests passed. If the receipt is missing or stale, omit
the test headline and refresh through the trusted generator. Stage new inputs
before generation, then commit the receipt with the source changes. The
pre-commit and CI receipt gate reject mismatched inputs. X publishing still
uses the normal approval gate.

An old snapshot's commit and the current HEAD can differ because of merge
history or the commit carrying that snapshot. Counting intervening commits
does not prove that every metric changed. Fresh measurement is the evidence
for a current post.

## What fails, and how it shows

| Failure | What happens |
|---|---|
| pytest cannot collect (project not installed, an import error) | `--require-collected` fails the run with pytest's error. Nothing is sent, so the site never gets "n/a". |
| `WEBSITE_TOKEN` missing or expired | The sync step fails. The secret is a fine-grained PAT for `Mubder/KazmaAI` with Contents and Pull requests read & write. |
| The push or the pull request did not land | The run fails: "no single open pull request … carries …". |
| The site's `main` already has these files | An open metrics pull request is closed as stale; the run succeeds. |
| Nothing changed since the open pull request | Nothing is pushed (a push would only rebuild the site's preview); the run succeeds. |

A failed run shows as failed in the Actions tab; GitHub emails a failed
scheduled run to whoever last changed its schedule.

## Why it looks like this (2026-09-27)

Until that day the workflow ran on every push, and for two months every run
was green while the site got nothing:

- It found the pull request with `gh pr view <branch>`, which answers with the
  branch's latest pull request in any state. PR #3 was merged on 2026-07-30;
  every run after it force-pushed the branch and logged "Updated existing PR".
  No pull request was open. The site kept its numbers until someone copied new
  ones in by hand on 2026-09-24.
- It installed nothing, so pytest could not collect and every copy said
  "Collected at runtime: n/a". The site would have fallen back to the static
  count (9,421 instead of 11,376); the website grew its own script to fill the
  number in by hand.
- Every push updated the branch, and every update built a Cloudflare Pages
  preview of the site: 529 in September, above the free plan's 500 builds a
  month.

`tests/test_site_metrics_sync.py` runs the script against a fake GitHub that
answers like the real one (merged pull requests included), and holds the
workflow to: daily and by hand only, read-only, the same install as CI's Tests
job, `--require-collected`, no `gh pr view`.

## `metrics.json`, schema_version 1

The site reads this file, not the Markdown tables. Keys change only with a new
`schema_version` (the layout is frozen in `tests/test_generate_metrics.py`);
adding a key is compatible. Every count is a plain integer: no separators, no
bold.

| Key | Meaning |
|---|---|
| `schema_version` | `1`. The site must refuse a version it does not know. |
| `generated_on` | Date of the run, `YYYY-MM-DD`. |
| `repository` | `Mubder/kazma`. |
| `commit` | `sha`, `short`, `subject`, `date` of the commit measured. |
| `python` | `files`, `lines`, `code_lines`, `blank_lines`, `comment_lines` over every tracked `.py` file. `code_lines` excludes blank and comment lines. |
| `areas` | `source` (the product packages), `tests`, `examples`, `archive`, `scripts`, `root`: each with `files`, `lines`, `code_lines`. |
| `packages` | A list, one entry per product package: `name`, `files`, `lines`, `code_lines`. The package count is its length. |
| `tests.collected` | Tests pytest collects in CI's environment, parametrized cases included. The number to show as "tests". Never null in a synced file. |
| `tests.static_functions` | `def test_*` plus `async def test_*` in the source. Under-reports; README states this one. |
| `tests` (others) | `files`, `def_functions`, `async_def_functions`, `classes`, `lines`: static counts of the test code. |
| `source_structure` | `functions`, `async_functions`, `classes` defined in the product packages. |
| `assets` | `javascript_files`, `javascript_lines`, `html_files`, `yaml_files`, `markdown_files`, `tsx_files`, `css_files`, `json_files`, `svg_files`. |
| `git` | `commits`, `contributors` (GitHub's contributors list, bots included), `branches`, `tags`. |
| `largest_python_files` | A list of the 15 largest `.py` files: `path`, `lines`. |
| `versions` | `pyproject`, `kazma_yaml`, `cli`: version strings, null where none was found. |

## Refresh by hand

- From GitHub: Actions → **Sync Metrics** → **Run workflow**.
- From a checkout with `gh` signed in: `python scripts/sync_site_metrics.py`
  generates, then syncs; `--dry-run` prepares the commit and pushes nothing.

On the website: never edit `src/data/METRICS.md` or `src/data/metrics.json` by
hand (the next refresh replaces them), and never push to
`chore/auto-refresh-metrics`.
