---
id: website-sync
title: Keeping kazma.ai in step with this repository
sidebar_label: Website sync
description: The standing procedure for updating kazma.ai — where to start, what to read, what to change, and how to prove it is done
---

# Keeping kazma.ai in step with this repository

kazma.ai is built from the private repository `Mubder/KazmaAI` (Astro with
Starlight; Cloudflare Pages deploys it when its `main` changes). Everything it
says about Kazma comes from **this** repository. This page is the procedure
for updating it. It is the same every time: an agent or a person working on
the website starts here.

## The rule

The site mirrors this repository; it never leads it. Every docs page has a
source here, every feature claim is in `docs/FEATURES.md`, every number comes
from `metrics.json`. Something that should be on the site and is not here is
added here first, then copied.

## Where the truth lives (this repository)

| What | Source | How it reaches the site |
|---|---|---|
| Documentation pages | `docs/docs/`, and the pages directly in `docs/` | `docs/website-pages.json` gives each page its place on the site, or says why it is not published. You copy and translate |
| What Kazma does | `docs/FEATURES.md` | Every claim on the site's own pages must match it |
| Known limits | `docs/KNOWN_GAPS.md` | Published as `security/known-gaps`; nothing on the site contradicts it |
| What changed | `CHANGELOG.md` | Its new entries are what the site may announce |
| Numbers | `scripts/generate_metrics.py` | `src/data/metrics.json` and `METRICS.md`, by a daily pull request ([Website metrics](website-metrics.md)). Never by hand |

## Where things live (the website repository)

| What | Where |
|---|---|
| English docs page | `src/content/docs/<page>.md`, served at `/docs/<page>/` (`index.mdx` is `/docs/`) |
| Arabic docs page | `src/content/docs/ar/<page>.md`, served at `/ar/docs/<page>/`. Every English page has one |
| Docs sidebar | `astro.config.mjs`, Starlight's `sidebar` list: `slug: 'docs/<page>'` with an Arabic label under `translations` |
| Redirects | `public/_redirects` (Cloudflare Pages) |
| Record of the last sync | `src/data/docs-sync.json`, written only by the plan script |
| Numbers | `src/data/metrics.json` (read by `src/data/metrics.ts`) and `src/data/METRICS.md` |
| Claims outside the docs | The home page sections (`src/components/home/`), the Features, Security, FAQ and About pages (`src/components/pages/`; a page file under `src/pages/` and `src/pages/ar/` only names its component and language), `src/data/features.ts` (every line of `docs/FEATURES.md` with its status, English and Arabic), `src/data/faq.ts`, the legal pages in `src/pages/`, `public/llms.txt`, `public/llms-full.txt` and their `public/ar/` copies, and any component that prints a number or a feature |
| Screenshots | `public/screenshots/` (WebP, English and Arabic, a dark and a `-light` capture of each view; the site shows the one matching its theme): captures of Kazma on this repository's test harness with demo data, never of a live install. When the interface they show changes, retake them with `python scripts/site_screenshots.py --site <KazmaAI checkout>` (it runs `tests/site/test_site_screenshots.py` per language and theme, and changes the site's files only when every capture succeeded) |

## The procedure

### 1. Get both repositories current

```bash
git -C kazma pull
git -C KazmaAI pull
```

This repository is public: `https://github.com/Mubder/kazma`. If the website
has an open metrics pull request (branch `chore/auto-refresh-metrics`), merge
it first.

### 2. Make the plan

```bash
python kazma/scripts/website_sync_plan.py --site KazmaAI
```

It compares each source's content (its git blob) with the one the site
recorded at its last sync, compares each page's structure with its source and
with its other language, and checks the site's links and sidebar. Standard
library only; it reads both folders and writes nothing (`--json` for
machine-readable output).

| Section | What to do |
|---|---|
| ADD | Create the page in both languages, with its sidebar entry |
| UPDATE | Apply the source's changes (the plan prints the exact `git diff`) to the English page and to the Arabic page |
| REMOVE | Delete both languages and the sidebar entry; add a `301` redirect in `public/_redirects` to the closest page |
| SITE-ONLY | A page with no source here: remove it the same way, or ask for a source page first (an issue on `Mubder/kazma`) |
| ENGLISH DRIFT | The English page is built differently from its source (headings, code blocks, table rows, asides), or its code is not the source's code: it was not ported, or only in part. Port it in full |
| ARABIC MISSING, ARABIC ORPHANS | Translate the missing page; remove the orphan, or give it its English page |
| ARABIC DRIFT | The Arabic page is built differently from the English one (headings, code blocks, table rows, asides), or its code differs: it is a partial or old translation. Translate the English page again, in full, with the code copied exactly |
| SIDEBAR MISSING, SIDEBAR DEAD | Add the entry (English label and Arabic translation); remove the dead one |
| LINKS | Point it at `/docs/<page>/` for a published page, or at `https://github.com/Mubder/kazma/blob/main/<path>` for anything else |
| CLAIMS | `docs/FEATURES.md` changed: re-read it, then every claim location in the table above (step 5) |
| NEWS | New CHANGELOG entries: `recent-features` is a synced page; update anything the home page announces |

### 3. Port a page

- **Frontmatter:** keep `title` and `description`; drop the Docusaurus keys
  (`id`, `sidebar_label`, `sidebar_position`, `slug`). The sidebar lives in
  `astro.config.mjs`.
- **Links:** a relative link to another source (`../guide/x.md`) becomes
  `/docs/<page>/` of the page `docs/website-pages.json` maps it to; a link to
  a source that is not published becomes its GitHub URL. The plan flags a
  link left in the source's form.
- **Heading ids:** a source heading may end with a Docusaurus id,
  `## Title {#id}`. Keep it: the site gives the heading that id and shows
  only the title (`src/plugins/remark-heading-ids.mjs`). Until 2026-10-01 the
  site showed the braces and every link to such an id missed.
- **Asides:** `:::note`, `:::tip` and `:::danger` exist in both;
  `:::info` becomes `:::note` and `:::warning` becomes `:::caution`.
- **Code is copied exactly**, into both languages: comments included. The
  sources use no MDX imports or components; an `import` line inside a code
  block is part of the example. (The site's first port script,
  `scripts/sync_from_kazma.py`, drops every line starting with `import`, code
  examples included: do not reuse it.) The plan compares the code.
- **Everything else stays as the source says it:** the same headings, tables
  and code blocks, the same limits and status words. Do not summarize, soften
  or add. The plan compares the structure (ENGLISH DRIFT).
- **Adapted pages** are the exception, marked `"adapted": true` in
  `docs/website-pages.json`: the site writes its own page from the source
  instead of copying it. Today that is only the docs home (`index`, a landing
  page with cards and "what is new"). The plan still shows its source's
  changes (UPDATE); put them into the page in its own layout, from the source,
  `docs/FEATURES.md` and `CHANGELOG.md`. Its English and Arabic still match
  each other.

### 4. Translate

- A page that changes in English changes in Arabic in the same commit.
- Translate all of it: the Arabic page has the same headings, tables, code
  blocks and asides as the English one. The plan counts them (ARABIC DRIFT).
- Code blocks are copied exactly, comments included. Commands, paths, setting
  names, environment variables, tool names and numbers in the text stay as
  they are, in Latin script.
- Modern Standard Arabic; the product's Arabic name is كاظمه.
- Direction is automatic (the `ar` locale is right-to-left); add no `dir`
  attributes.
- **Links go to Arabic pages:** `/docs/<page>/` in the English text is
  `/ar/docs/<page>/` in the Arabic one. The plan flags a link in an Arabic
  page that leads to the English page of a page that has an Arabic one, and
  so does the build.
- **Anchors keep their English id.** A heading's id is made from its words,
  so an Arabic heading gets an Arabic id and `#<english-id>` misses it. When
  a link points at an Arabic heading, end that heading with the English
  page's id for the same heading: `## 10. إشعارات حالة دورة الحياة {#10-lifecycle-status-notifications}`.
  A heading that already carries `{#id}` in English carries the same one in
  Arabic.

### 5. Check the claims

With `docs/FEATURES.md` open, go through every claim location in the table
above, in both languages:

- A feature is claimed only if FEATURES.md lists it, with its status word
  (Shipped, Opt-in, Partial). "Not built" is never presented as available.
- Numbers come from `metrics.json` through `src/data/metrics.ts`. No count,
  line total, test number, commit number or package count is typed into a
  page.
- Nothing contradicts a limit in `docs/KNOWN_GAPS.md`.
- English and Arabic say the same thing.

### 6. Prove it, then record it

1. `npm run build` passes. Cloudflare Pages runs the same build on every pull
   request; a failed build leaves the live site on its last deployment. The
   build fails on a link to a page that does not exist or answers only
   through a redirect, on a `#anchor` its page does not have, and on a link
   in an Arabic page's text to an English page that has an Arabic one
   (`src/plugins/check-internal-links.mjs`). Anchors inside this
   repository's docs are checked by `tests/test_docs_anchors.py`.
2. Record what you synced, both languages done:
   `python kazma/scripts/website_sync_plan.py --site KazmaAI --mark-synced <page> [...]`
   (a site path such as `ops/migration`, or a source path such as
   `docs/FEATURES.md` once its claims are checked), or `--mark-synced-all`
   after a full pass. The script refuses a page missing a language, an
   English page built differently from its source and an Arabic page built
   differently from its English one, and records nothing if any page is
   refused. A record is a claim that the page holds its source; it cannot be
   made for a page that visibly does not.
3. `python kazma/scripts/website_sync_plan.py --site KazmaAI --check` exits 0.
4. Commit `src/data/docs-sync.json` with the pages it records, in the same
   commit: `docs: sync from kazma @<short commit>`.

A large sync can be done in batches, across sessions: record each finished
page with `--mark-synced`, and the next plan shows only what is left.

### The first time

A site with no `src/data/docs-sync.json` has no record to compare with. Add
`--assume-synced-at <commit>`, the commit of this repository the site was last
copied from: for kazma.ai that was `ab265563` (2026-09-24). Keep adding it
until every page is recorded; pages recorded along the way are compared with
their record.

## What the website repository's AGENTS.md says

The website repository carries this as its `AGENTS.md` (and a `CLAUDE.md`
that reads `Read AGENTS.md first.`), so any agent opening it finds this page:

```markdown
# Working on kazma.ai

Everything this site says about Kazma comes from the framework repository,
https://github.com/Mubder/kazma. Before changing any docs page, claim or
number, follow https://github.com/Mubder/kazma/blob/main/docs/docs/ops/website-sync.md

Short version: pull both repositories, then run
    python ../kazma/scripts/website_sync_plan.py --site .
and do what it lists, in English and Arabic; `npm run build`; record the
sync with --mark-synced; the plan with --check must exit 0.

Never edit by hand: src/data/metrics.json and src/data/METRICS.md (the daily
metrics pull request writes them) and src/data/docs-sync.json (only the plan
script's --mark-synced writes it).
```

## For maintainers of this repository

- A page added, moved, renamed or deleted under `docs/docs/`, or directly in
  `docs/`, changes `docs/website-pages.json` in the same commit: where it goes
  on the site, or `"site": null` with a `why`. `tests/test_website_pages.py`
  fails otherwise.
- The plan script is `scripts/website_sync_plan.py`
  (`tests/test_website_sync_plan.py`).
- Numbers reach the site by themselves ([Website metrics](website-metrics.md));
  everything else waits for someone to run this procedure.
