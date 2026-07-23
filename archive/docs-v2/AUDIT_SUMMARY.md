# Documentation Audit Summary

> Companion to the `docs-v2/` rewrite. This records the gaps filled from code inspection, key corrections to prior documentation, and recommendations for long-term maintenance.

---

## 1. Major gaps filled from code inspection

The previous documentation (README, architecture blueprints, audit reports) described several features aspirationally. The rewrite either connected them to verified code or marked them as not-yet-wired:

| Area | Gap | What the rewrite does |
|---|---|---|
| **Memory wiring** | "4-layer memory pipeline" implied automatic RAG injection. | Documents the **three disjoint subsystems** and that the 4-layer adapter is used by `self_improvement.py` + `phonebook.py` + now **chat agent via `AsyncMemoryAdapter`**. |
| **Compaction** | Described as memory-enriched + checkpointed. | Documents that `agent_runner.py:162-166` wires `AsyncMemoryAdapter` → `VectorMemory`, so LLM summarise + memory retrieval work; auto-store added. |
| **HITL build sites** | AGENTS.md cited "app.py ~line 966". | Corrected to `kazma-ui/kazma_ui/app.py:741-751`; noted `graph_builder.py:966` is unrelated. |
| **Approval endpoint** | Said to be in `app.py`. | Located precisely at `routes_direct.py:454`. |
| **Safety class name** | Some docs said `SafetyGate`/`SafetyChecker`. | Corrected to `SafetyMiddleware` (`swarm/safety.py:47`). |
| **Prometheus** | Implied by "observability" references. | Stated plainly: no `prometheus_client`, no `/metrics`. |
| **LiteLLM** | "router: litellm" implied a dependency. | Clarified it's a string gating one branch; no `import litellm`. |
| **Per-provider env vars** | `.env.example` lists `DEEPSEEK_API_KEY`/`ANTHROPIC_API_KEY`. | Flagged that no code reads them; only `OPENAI_API_KEY`/`KAZMA_API_KEY` are generic fallbacks. |
| **"Trust tiers"** | Implied a tiered trust model. | Corrected: only a boolean `certified` flag + an unused `trust: trusted` string. |
| **K8s manifests** | Implied they deploy Kazma. | Corrected: they deploy a separate Hub API (PostgreSQL + Redis), not the main agent. |
| **Majlis Mode** | Unclear if it existed. | Confirmed it exists in `majlis.py` — but as a core module, not a UI toggle. |
| **WebSocket chat** | Possibly still described as live. | Flagged `/ws/chat` as 410 Gone; SSE is the transport. |
| **`/undo`, `/edit`** | Listed in help. | Flagged as stubs ("not yet implemented"). |
| **`sqlite-vec` dependency** | Assumed declared. | Clarified it's transitive via `langgraph-checkpoint-sqlite`. |
| **`distance()` bug** | Undocumented. | Surfaced that `search_backend.py` `_vector_search` uses an invalid sqlite-vec function. |
| **Dead KG code** | Old `KazmaKG` and `KnowledgeGraphAdapter` caused confusion. | **Removed** — ~1,000 lines deleted; Swarm uses its own `KnowledgeGraph` in `swarm/memory/graph.py`. |

---

## 2. Key improvements over previous documentation

1. **Source-referenced claims.** Nearly every factual statement cites a `file:line`. This makes the docs verifiable and maintainable.
2. **Honest status markers.** 🟡/🔴 in [Roadmap](docs/roadmap-and-future.md) and "Honest status notes" in [Memory & RAG](docs/memory-and-rag.md) prevent operators from relying on non-existent behavior.
3. **Complete configuration reference.** [Configuration](docs/configuration.md) covers every `kazma.yaml` key, every env var (including code-only ones), the ConfigStore precedence model, and the provider presets.
4. **Complete CLI tree.** [CLI Reference](docs/cli-reference.md) documents the hand-rolled top-level parser, the Click `hub` subtree, all swarm subcommands, and the in-chat slash commands — including which are stubs.
5. **Three-gate HITL model.** [Security & Safety](docs/security-and-safety.md) cleanly separates the graph/swarm/pipeline gates and their **three distinct** danger-tool lists.
6. **Mermaid diagrams.** Architecture, data-flow sequence, HITL tiers, swarm patterns, and the Majlis flow are visualized.
7. **Parity matrix.** [Gateways & Platforms](docs/gateways-and-platforms.md) shows exactly which features work on which platform.
8. **Production checklist + diagnostics.** [Deployment](docs/deployment.md) and [Troubleshooting](docs/troubleshooting-and-workarounds.md) give actionable, verified commands.

---

## 3. Recommendations for diagrams, screenshots, or visuals to add later

| Visual | Priority | Notes |
|---|---|---|
| Annotated screenshot of the Web UI (dashboard, chat, settings) | High | No screenshots exist in the repo. |
| Annotated TUI screenshot | Medium | Shows the tab layout. |
| Provider preset card grid | Medium | Visual at-a-glance of the 10 presets. |
| HITL approval flow screenshot (Telegram inline button + Web button) | High | The most important UX to illustrate. |
| Swarm dispatch sequence diagram (per pattern) | Medium | One sequence per pattern would help. |
| Memory-subsystem relationship diagram | High | The three disjoint subsystems are confusing in prose. |
| ConfigStore precedence flowchart | Low | Already in Mermaid; a polished version would help. |
| Video: first-message walkthrough | Low | Onboarding gold, but effort-heavy. |

---

## 4. Suggested integration into the existing Docusaurus site

The existing docs site is Docusaurus 3.4 (`docs/docusaurus.config.js`, `docs/sidebars.js`). To integrate `docs-v2/`:

1. **Move/copy** `docs-v2/docs/*.md` into `docs/docs/<section>/` (or a new `docs/docs-v2/` docset if you want to A/B test). The files are already in Markdown compatible with Docusaurus (Mermaid requires `@docusaurus/theme-mermaid` or the preset-mermaid config).
2. **Enable Mermaid** in `docusaurus.config.js`:
   ```js
   markdown: { mermaid: true },
   themes: ['@docusaurus/theme-mermaid'],
   ```
3. **Update `sidebars.js`** to reflect the new structure. Suggested sidebar order matches the README "Documentation Map":
   ```js
   module.exports = {
     docs: [
       'quickstart',
       'architecture',
       'configuration',
       'cli-reference',
       'gateways-and-platforms',
       'memory-and-rag',
       'skills-mcp-and-tools',
       'swarm-orchestration',
       'security-and-safety',
       'arabic-cultural-features',
       'deployment',
       'api-and-extension-points',
       'troubleshooting-and-workarounds',
       'development',
       { 'Roadmap & Future': ['roadmap-and-future'] },
       { 'Reference': ['faq', 'glossary'] },
     ],
   };
   ```
4. **i18n:** Docusaurus supports per-locale doc trees. If you want an Arabic mirror, place translated copies under `docs/i18n/ar/docusaurus-plugin-content-docs/current/`. The i18n system described in [Arabic & Cultural Features](docs/arabic-cultural-features.md) is for the *app*, not the docs site — docs translation is separate.
5. **Search:** the site already uses `@easyops-cn/docusaurus-search-local`; reindex after moving content (`npm run build`).
6. **Versioning:** when stable, snapshot as `docs/versioned_docs/version-0.3.x/` and enable the version dropdown.

---

## 5. Honest observations & long-term maintenance recommendations

### Observations

- **The codebase is more capable than the wiring suggests in places.** The 4-layer memory adapter, the cost breaker, and compaction's memory/checkpoint steps are all *implemented* but not connected in the default runtime. This is the single biggest source of doc-vs-reality drift. The highest-leverage code change is wiring `memory_store`/`checkpoint_manager` into `create_authority()` (see [Roadmap §9](docs/roadmap-and-future.md#9-suggested-next-steps-from-the-audit)).
- **Version strings are unsynchronized** (`pyproject.toml` 0.3.0, `kazma.yaml` 0.2.0, CLI `--help` v0.2.0). This should be a one-line fix with high documentation payoff.
- **Three safety lists** (graph/swarm/MCP) are a known footgun. A single source of truth with per-gate overrides would reduce the risk of adding a danger tool to one list but not the others.
- **The K8s manifests are misleading.** They should be moved/renamed to make clear they're for the Hub API, with an env-var set the main agent doesn't use.
- **`search_backend.py` has latent bugs** (`distance()` function, no-op extension detection) that are masked only because the path is unreached. These will bite whoever next touches memory.

### Maintenance recommendations

1. **Treat `docs-v2/` as the source of truth** and delete or clearly archive the contradictory prose in older root-level `.md` files (the `AUDIT_*.md` files are fine as historical artifacts; the README's "4-layer memory" wording should be reconciled).
2. **Add a docs-CI check** that greps for removed features (e.g. `/ws/chat`, `DEEPSEEK_API_KEY`) so stale claims don't reappear.
3. **Keep the "Honest status notes" convention.** Future doc authors should mark 🟡/🔴 rather than describing aspirational behavior as shipped.
4. **Regenerate the audit summary** whenever a major subsystem changes — the file:line references here will drift as code moves.
5. **Consider a single generated config reference** (e.g. from a Pydantic schema) so `kazma.yaml` options can't drift from docs. `KazmaConfig.from_flat_dict` (`config_store.py:795`) is a starting point.
6. **Add screenshots on every release**, since none exist today.

---

## 6. Consolidation record (July 2026)

This `docs-v2/` is now the **single consolidated master**. Three pre-existing doc sets were merged into it:

1. **The base rewrite** (this tree) — the strongest, most source-referenced set.
2. **The duplicate `docs-v2/` copy** (formerly the Astro-site draft) — corrected against, then its 4 unique pieces folded in:
   - `glossary.md`: +6 terms (`AutoScaler`, `BlackboardStore`, `Code-switch token`, `CommandConsole`, `PermissionLevel`, `WorkerCapabilities`).
   - `security-and-safety.md` §8: +hardening-runner check table, +dependency-scanner table, +disclosure-workflow detail.
   - `configuration.md`: +5 `swarm.*` keys (`default_pattern`, `auto_route`, `max_concurrent_tasks`, `max_concurrent`, `output_target`).
   - `cli-reference.md`: +`--port` precedence table, +exit-codes section.
3. **The original Docusaurus `docs/`** — its deep operator troubleshooting ported into `troubleshooting-and-workarounds.md` (§1.6–§1.9 registry, §9.1–§9.4 diagnostic tooling, §10 Gateway/Telegram, §11 Providers/Connectors hub, §12 TUI, §13 CLI, §14 Swarm panel), and the consumer skill workflow into `skills-mcp-and-tools.md` §4.3.

> **Verification note:** `kazma wizard` was initially suspected fabricated but is a real command (`main.py:117-123` → `kazma_core/cli/wizard.py:SkillInstallationWizard`) and was retained. The fabricated-command findings above (`hub publish`, etc.) were re-confirmed against `hub/cli.py`.

**Corrections applied to ported content:**
- `ConfigStore()` → `get_config_store()` everywhere (direct construction causes SQLite lock contention; `config_store.py:760`).
- The two distinct HITL endpoints are now explicitly disambiguated (agent tool-call `POST /api/approve/{thread_id}` vs. swarm/pipeline `POST /api/swarm/tasks/{id}/approve|reject`).
- Dropped the stale `TelegramWorker`/`hermes`/`kazma -p <profile>` narrative — the class no longer exists (tree has only `SwarmWorker`/`InProcessWorker`).

**Excluded from the merge (verified fabricated or superseded):**
- `kazma-hub/overview.md`, `publishing-skills.md`, `security-auditing.md` — non-existent `hub publish` command; fabricated security-score tiers (real validator computes `score = max(0, min(100, 100 + delta))`, no rating tiers).
- `skill-development/{creating-skills,testing-skills,certification}.md` — invented `MySkill.execute(context)` contract (skills register via `@register_tool`, invoked through `ToolRegistry.execute`); fake `--level`/`--security-audit`/`--verbose` flags; placeholder future dates.
- `architecture/PROVIDERS.md` — superseded by the current `architecture.md`/`configuration.md` ModelRegistry coverage (incl. the SSRF guard this older doc omits).

**Outcome:** one internally-consistent, code-verified set, mirrored across the framework and Astro-site repos.

---

*End of audit summary. The `docs-v2/` tree is 19 files: 1 README, 17 docs, plus this summary.*
