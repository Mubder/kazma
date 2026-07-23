# Kazma Documentation Consolidation Plan

**Date:** 2026-07-21  
**Goal:** One clean `docs/` tree as the only published source of truth — no dual `docs-v2/`, no orphan loose guides, complete coverage of every product surface.  
**Status:** In progress (Phase 0–2 structure + archive)

---

## 1. Goals

| Goal | Definition of done |
|------|-------------------|
| **Single tree** | All user-facing docs under `docs/docs/` (Docusaurus). No `docs-v2/` at repo root. |
| **Clean IA** | One primary sidebar (plus Skills / Security / Ops). No “Legacy Concepts” dual navigation. |
| **Honest & current** | Prefer code-audited Guide content; mark unwired features; bump version anchors to 0.6.1+. |
| **Complete coverage** | Guide + FAQ + How-to + every tool / CLI / slash / env / UI surface documented or explicitly “library-only”. |
| **Archive, don’t ghost-delete** | Stale material moves to `archive/docs-*` with a README index for later decisions. |
| **Pointers fixed** | `README.md`, `AGENTS.md`, `architecture.md`, `CONTRIBUTING.md` point only at `docs/`. |

---

## 2. Current state (problem)

| Location | Role today | Problem |
|----------|------------|---------|
| `docs/docs/guide/*` (18 pages) | **Best** code-audited content (docs-v2 merge) | Incomplete for post–0.6.1 (IDE, SaaS, Postgres, tools catalog) |
| `docs-v2/docs/*` | Historical authoring copy | **Duplicate**; README still links here |
| `docs/docs/getting-started`, `core-concepts`, `api-reference` | Older Docusaurus pages | Stale / wrong APIs; “Prefer Guide” banners |
| Loose `docs/*.md` | slash-commands, compaction, TROUBLESHOOTING, HANDOVER… | Not in sidebar; duplicates or supersedes Guide |
| `docs/ops/*` | Real ops (DR, OIDC, Postgres) | Not in Docusaurus nav |
| `docs/audits/*` | Engineering audits | Correct as internal; keep unlisted |
| `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` | System map | Not linked from Guide |

---

## 3. Target information architecture

Published site (`docs/docs/`) — **one Guide-first nav**:

```text
docs/docs/
├── intro.md                          # Docs home / map of the docs
├── start/                            # Getting started (from guide quickstart + install)
│   ├── quickstart.md
│   ├── installation.md
│   └── configuration.md              # deep config stays in reference too
├── guide/                            # Conceptual + how-to (existing audited set, updated)
│   ├── architecture.md
│   ├── gateways-and-platforms.md
│   ├── swarm-orchestration.md
│   ├── memory-and-rag.md
│   ├── security-and-safety.md
│   ├── skills-mcp-and-tools.md
│   ├── arabic-cultural-features.md
│   ├── deployment.md
│   ├── development.md
│   ├── troubleshooting-and-workarounds.md
│   ├── faq.md
│   ├── glossary.md
│   └── roadmap-and-future.md
├── products/                         # NEW — UI/surfaces
│   ├── web-ui.md
│   ├── ide.md
│   ├── tui.md
│   ├── command-center-swarm.md
│   └── multi-user-saas.md
├── reference/                        # Exhaustive catalogs
│   ├── cli.md
│   ├── tools-catalog.md              # every local + native skill tool
│   ├── slash-commands.md
│   ├── environment-variables.md
│   ├── kazma-yaml.md                 # or keep configuration.md name
│   ├── api-routes.md
│   ├── skill-manifest.md
│   └── system-map.md                 # condensed from ARCHITECTURE_AND_SYSTEM_MAP
├── ops/                              # Production ops
│   ├── production-checklist.md
│   ├── disaster-recovery.md
│   ├── postgres-and-saas.md
│   ├── multi-region.md
│   └── oidc-setup.md
├── skills/                           # Skill authoring (honest — no fake hub flags)
│   ├── overview.md
│   ├── creating-skills.md
│   ├── skill-manifest.md → symlink/alias to reference
│   └── mcp-integration.md
├── security/
│   ├── policy.md
│   ├── vulnerability-reporting.md
│   └── hardening.md
└── contributing/
    ├── setup.md
    ├── code-style.md
    ├── testing.md
    └── pull-requests.md
```

**Not published (stay on disk, no sidebar):**

- `docs/audits/**` — engineering audits & remediation  
- `docs/DOCS_CONSOLIDATION_PLAN.md` — this plan  
- `archive/**` — retired trees  

---

## 4. Archive matrix (safe moves)

| Path | Action | Destination | Later decision |
|------|--------|-------------|----------------|
| `docs-v2/` entire tree | **ARCHIVE** | `archive/docs-v2/` | Delete after 1 release if no diffs vs guide |
| `docs/docs/getting-started/*` (stale) | **ARCHIVE** after content merge into `start/` | `archive/docs-legacy/getting-started/` | Keep only unique install notes |
| `docs/docs/core-concepts/*` | **ARCHIVE** (superseded by guide) | `archive/docs-legacy/core-concepts/` | Merge any unique diagram into architecture |
| `docs/docs/api-reference/*` | **ARCHIVE** after fold into `reference/api-routes.md` | `archive/docs-legacy/api-reference/` | Drop fabricated APIs |
| `docs/HANDOVER.md` | **ARCHIVE** | `archive/docs-loose/HANDOVER.md` | Agent history only |
| `docs/AUDIT_KANBAN.md` | **ARCHIVE** | `archive/docs-loose/` | Superseded by audits/ |
| `docs/ui-rebuild-plan.md` | **ARCHIVE** | `archive/docs-loose/` | Product history |
| `docs/TROUBLESHOOTING.md` | **ARCHIVE** after verify guide supersets | `archive/docs-loose/` | Check unique issues |
| `docs/ROADMAP.md` | **ARCHIVE** if guide roadmap is SoT | `archive/docs-loose/` | |
| `docs/architecture/PROVIDERS.md` | **ARCHIVE** (stale; guide covers providers) | `archive/docs-loose/` | |
| `docs/architecture/MEMORY.md` | Fold unique bits → memory guide; archive rest | `archive/docs-loose/` | |
| `docs/compaction.md` | Fold into memory guide if unique; archive | `archive/docs-loose/` | |
| `docs/google_provider_setup.md` | Fold into configuration/providers how-to | keep snippet in guide | |
| `docs/portability.md` | Fold into deployment | archive residual | |
| `docs/VERSIONING.md` | Keep or fold into contributing | optional archive | |
| `docs/skill-manifest-spec.md` | → `reference/skill-manifest.md` | remove loose | |
| `docs/slash-commands.md` | → `reference/slash-commands.md` | remove loose | |
| `docs-v2/ux-mockup-dashboard.html` | **ARCHIVE** with docs-v2 | | Design artifact |
| Old audits pre–2026-07-18 | **KEEP** in `docs/audits/` (historical) | n/a | Optional compress later |
| Superseeded root pointers | Update only | n/a | |

**Never archive without replacement:** Guide architecture, security, swarm, configuration, ops SaaS/Postgres, production remediation plans.

---

## 5. Coverage matrix (leave nothing behind)

### 5.1 Must document (user-facing)

| Surface | Primary doc | Status after plan |
|---------|-------------|-------------------|
| Install / quickstart | `start/quickstart` | Update ports 9090, secrets, prod flags |
| `kazma.yaml` keys | `reference/configuration` / guide config | Refresh vs live yaml |
| Env vars (all `KAZMA_*` + provider keys) | `reference/environment-variables` | **NEW** from code + system map |
| CLI: status, serve, wizard, hub, docs, completion, project, gateway, swarm, update | `reference/cli` | Refresh from `main.py` |
| Built-in tools (file_*, shell, code_exec, memory, web, …) | `reference/tools-catalog` | **NEW** from `tool_registry` |
| Native skills tools (vault, git, cron, …) | same catalog | **NEW** from manifests |
| MCP force_danger / trust | skills-mcp + tools | Update |
| Slash commands (all platforms) | `reference/slash-commands` | Promote loose file; mark stubs |
| HITL three gates | security-and-safety | Keep + cross-link |
| Swarm patterns / reliability | swarm-orchestration | Keep |
| Memory FTS/vector/compaction | memory-and-rag | Merge compaction notes |
| Gateways TG/Discord/Slack | gateways | Keep |
| Web UI pages (chat, settings, swarm, IDE, login) | `products/web-ui` | **NEW** |
| IDE API + workspace | `products/ide` | **NEW** |
| TUI screens | `products/tui` | **NEW** |
| Command Center / swarm SSE | `products/command-center-swarm` | **NEW** |
| Multi-user: RBAC, OIDC, opaque sessions | `products/multi-user-saas` | **NEW** |
| Postgres dual backend | `ops/postgres-and-saas` | Promote ops |
| DR / backup / restore | `ops/disaster-recovery` | Promote |
| Multi-region / HA | `ops/multi-region` | Promote |
| OIDC IdP | `ops/oidc-setup` | Promote |
| Production checklist | `ops/production-checklist` | **NEW** from remediations |
| Arabic / Majlis / i18n | arabic-cultural | Keep; note library-only majlis |
| FAQ / Glossary / Roadmap | existing guide | Refresh |
| Contributing / tests | contributing/* | Point at real commands |
| Security policy / disclosure | security/* | Keep |

### 5.2 Document as library-only / not wired

| Package | Doc treatment |
|---------|----------------|
| `delegation/*` | Architecture appendix: not on live path |
| `permissions.yaml` | Not enforced on agent execute |
| Offline `security/linter|scanner|…` | Ops appendix “offline tools” |
| Soft-nav SPA | Web UI: disabled flag |
| Hub install stubs | Skills: honest status |

### 5.3 Internal only (audits)

Keep under `docs/audits/` + `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` (link from reference/system-map).

---

## 6. Execution phases

### Phase 0 — Freeze rules (done when written)
- SoT path = `docs/docs/**` only for product docs.  
- Edits never go to `docs-v2/` again.  
- Audits stay out of Docusaurus sidebar.

### Phase 1 — Archive dual tree & loose clutter
1. `git mv docs-v2 archive/docs-v2`  
2. Create `archive/docs-legacy/` and `archive/docs-loose/`  
3. Move listed stale files  
4. Add `archive/README.md` index  

### Phase 2 — Unified Docusaurus IA
1. Create `intro.md`, `start/`, `products/`, `reference/`, `ops/` content  
2. Rewrite `sidebars.js` + navbar (drop Legacy Concepts dual path)  
3. Update `docs/README.md`, `docusaurus.config.js` footer/copyright  
4. Move/promote slash-commands + skill-manifest into `reference/`  
5. Promote `ops/*.md` into `docs/docs/ops/` with frontmatter  

### Phase 3 — Content refresh & completeness
1. Tools catalog from code  
2. Env var matrix (post-production flags)  
3. IDE / Web / TUI / SaaS pages  
4. CLI full tree vs `main.py`  
5. Slash commands honesty (stubs)  
6. Config defaults vs live `kazma.yaml` + version 0.6.1  
7. Fold unique troubleshooting / compaction / memory notes  

### Phase 4 — Repo pointer sweep
- `README.md` doc table → `docs/docs/...`  
- `AGENTS.md` Key References  
- `architecture.md` pointer  
- `CONTRIBUTING.md` if it cites docs-v2  
- `scripts/check_docs_sync.py` paths  
- Grep for `docs-v2` and fix  

### Phase 5 — Build verify
- `cd docs; npm run build` (broken links → fix or warn policy)  
- Spot-check search index for tools / HITL / serve  

### Phase 6 — Optional later
- Delete `archive/docs-v2` after one release  
- i18n Arabic doc set  
- Auto-generate tools catalog in CI from registry  

---

## 7. Success criteria

- [x] No `docs-v2/` at repository root (→ `archive/docs-v2/`)  
- [x] Docusaurus has a single clear Guide + Reference + Products + Ops story  
- [x] README links only to `docs/`  
- [x] Tools catalog lists every built-in + native skill tool  
- [x] Env / CLI / slash matrices complete (CLI remains guide page; env/tools/slash/API new)  
- [x] `npm run build` succeeds (verified 2026-07-21)  
- [x] Archive index explains what was moved and why  
- [x] Phase 3 content refresh — guide version anchors v0.6.1+; Hub honesty pass; TROUBLESHOOTING/compaction folded; production §15; tools-catalog regen script  
- [x] `archive/docs-v2` retention policy documented in `archive/README.md` (delete after next minor if unused — **not** deleted this pass)  
- [x] `scripts/generate_tools_catalog.py` for catalog regeneration / CI drift  

### Phase 3 close-out notes (2026-07-21)

| Item | Resolution |
|------|------------|
| Guide “docs-v2 merge / v0.5.0” banners | Replaced with **unified docs, v0.6.1+** |
| Hub `publish` / `--security-audit` / `--level` | Removed from docs; real CLI documented |
| Native skill authoring | Documented `tools:` + `tools.py` pattern |
| TROUBLESHOOTING.md (661 lines) | Confirmed supersetted by Guide §1.6–14; provenance note + §15 production |
| compaction.md | Folded summary into Memory §6.5; archive kept |
| archive/docs-v2 delete | **Deferred by policy** (not a gap — intentional retention) |
| Tools catalog CI helper | `scripts/generate_tools_catalog.py` |

---

## 8. Ownership & non-goals

**Non-goals this pass:** rewriting CHANGELOG; deleting audits; inventing Hub marketplace features; multi-language docs site.

**Owner:** docs consolidation track (this plan). Product owners decide archive deletion after Phase 6.

---

*Plan created 2026-07-21 as part of monorepo documentation synthesis.*
