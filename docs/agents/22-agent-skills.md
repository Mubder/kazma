<!-- AGENTS.md, piece 22-agent-skills. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 22. Agent Skills Ecosystem (`kazma-core/kazma_core/agent_skills/`)

Kazma is a first-class citizen of the open **agentskills.io** `SKILL.md`
ecosystem — it can install, run, and publish skills that also work in Claude
Code, Cursor, Codex, etc. Four modules; understanding them is essential before
touching skill loading.

**A. Parser + spec (`parser.py`).** `SKILL.md` = YAML frontmatter + markdown
body. Required fields per the spec: **`name`** + **`description`** only.
`version` is OPTIONAL — do not re-introduce a "missing required field: version"
warning (it spam-listed every ecosystem skill). `validate_manifest` enforces
name+description and is the single gate.

**B. Discovery scopes (`discovery.py`).** `skill_base_dirs()` returns
`(scope, path)` pairs, lowest → highest precedence:
`bundled` (shipped with Kazma) → `user` (`~/.agents/skills` + Kazma/Claude/
Cursor compat dirs) → `project` (`<root>/.agents/skills`, `skills/`, …).
Project overrides user overrides bundled on name collision. The `bundled`
scope is `kazma_core/agent_skills/bundled/` — 3 Kazma-native starter skills.

**C. Integrity — two paths, do not flatten.**
- **User/project skills** (`integrity.py`): HMAC-SHA256 signed at install time
  (keyed by `KAZMA_SECRET`); activation verifies checksum+signature, **fail-closed** on tamper, warn-only on unsigned.
- **Bundled skills** (`catalog._verify_bundled_skill`): verified against the
  committed `bundled/checksums.json` (SHA-256 per skill); a bundled skill NOT
  listed in the manifest fails closed. Adding a bundled skill ⇒ regenerate
  `checksums.json` (see the generator at the bottom of the bundled dir's git
  history) or it won't activate.
- **Every surface shows activation's verdict** (`catalog.skill_integrity`,
  2026-10-02): `verified`, `unsigned` (loads with a warning), `refused`
  (with the reason, which names `kazma agent-skills sign <folder>`), or
  `unchecked` when the check itself did not finish (the Skills page warns;
  the words are `skills.integrity_unchecked`). The
  model's catalog leaves a refused skill out and says so; the list tool, the
  CLI's `list` and the Skills page show the state. They used to call any
  recorded checksum "verified": live, nine skills another key had signed were
  offered to the model and refused at activation. The page shows a security
  score only where a manifest declares one (none does; it showed an invented
  "100/100"). `sign` takes several folders. Only activation logs a refusal
  (`verify_skill(warn=False)` logs nothing above DEBUG): the catalog is built
  every turn and logged each refused skill each time. `tests/test_skill_integrity_shown.py`.

**D. Activation always fences the body** (`catalog.format_skill_activation`):
the SKILL.md body is wrapped in `format_untrusted_block(source="agent_skill:…")`
— it is GitHub-sourced text, data-not-instructions. Never inject a skill body
raw into the system prompt.

**E. Install with no Node/npm** (`installer.py`). `install_from_any(source)`
handles `owner/repo`, full GitHub URLs, `tree/branch/path`, git URLs, local
paths, and `npx skills add …` strings — downloads the GitHub zipball via httpx.
`rglob("SKILL.md")` so multi-skill repos install all skills. One HITL approval
covers an install (the user is the gate for what enters the system).

**F. Marketplace search** (`tools.search_agent_skills` + `/api/skills/marketplace/search`):
GitHub `topic:agent-skills` repository search (GITHUB_TOKEN-aware for rate
limits). The `/skills` page has a Marketplace tab (debounced search + one-click
install). Do not build a parallel registry — the GitHub topic IS the index.

**G. The Skills page's switch is real** (`kazma_core/skills/switches.py`,
2026-09-28). `skills.enabled.<id>` was written and never read: a built-in
skill switched off kept its tools and was listed on again. A native skill's
tools carry `LocalTool.skill_id` (`native:<folder>`, set by the native
loader); a switched-off skill's tools are left out of
`get_tool_definitions` and refused by `_execute_inner`. The switches are
read once per settings store (weak reference, not `id()`) and updated by the
page's toggle, so no model call reads settings. Built-in skills cannot be
uninstalled (400; the page shows the switch only). Marketplace ("hub")
skill installs are not built: `kazma hub install` and the page refuse
("not implemented", 501) instead of reporting an empty install (audit
2026-10-26 AUD-074, `tests/test_hub_install_refuses.py`).
`tests/test_skill_switches.py`. **Settings → Skills uses the same routes**
(2026-10-01): it posted to `/api/settings/skills/*`, which wrote
`skills.<name>.enabled` (read by nothing) and "uninstalled" any skill,
built-in ones included, by writing it; those routes are gone, the tab posts
the skill's id to `/api/skills/toggle` and `/api/skills/uninstall` and shows
Uninstall only for a skill that is not built in
(`tests/js/test_settings_skill_controls.js`). The toggle is admin-only, like
install and uninstall, and a failure answers 500.

**H. The skill folders in the home come from one place**
(`discovery.skills_home()`, 2026-10-01): `.agents/skills`, Claude's and
Cursor's folders and the legacy `.kazma/agent-skills` are all under it, and
`KAZMA_SKILLS_HOME` moves it (a service account reading the operator's
skills). The root conftest pins it, as it pins the data dir: the live install
runs as the same user and reads those folders, an install writes there and an
uninstall removes from there. `uninstall_skill(target_dir=...)` removes from
that folder only -- it also removed the name from the real `~/.kazma`, so
the uninstall test could delete a developer's skill. Gates:
`tests/test_install_data_shield.py` (every skill folder under the test temp
folders; only `skills_home()` reads the home; negative control without the
pin), `tests/test_agent_skills.py`.

