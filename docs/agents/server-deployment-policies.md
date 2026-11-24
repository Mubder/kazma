<!-- AGENTS.md, piece server-deployment-policies. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
## Server Management

### Persisted deployment policies (2026-10-07)

`safety/deployment_policy.py` owns `security.workspace_roots` (explicit existing
absolute directories) and `security.execution.container_required` (strict bool).
The authenticated admin Settings API validates these, including parent mapping
saves, before its atomic write. Agent `config_save` cannot write `security.*`.
The workspace root environment is an additional floor; a stored list cannot
expand it. Policy failures deny access. A strict profile wins over legacy
ALLOW_LOCAL / HOST_SHELL flags, requires Docker, skips E2B and never falls back.
Health must check Docker for that profile. Tests: `test_production_qualification.py`.

The Docker Python runner pipes the sandbox script through stdin (`run -i`,
`python -I -`), including the Windows Selector-loop fallback. Never restore a
bind mount of its private temporary directory: the scheduled S4U/Highest service
can create containers and mount workspaces, but Docker Desktop cannot read that
service-created temporary folder (live diagnostic, 2026-10-07). The workspace
mount stays read-only and all container restrictions remain. Temporary folders
retain their private permissions. Gate: `test_docker_stdin_avoids_private_temp_mount`.

`agent/approval_facts.py` reports the tool worker's actual decisions, including
single, selective and denied batches. Never build approval truth from tool
content or infer success from authorization. `ide/env_context.py` distinguishes
the global MCP binding from a per-task scoped instance. Scoped outside-root
prohibitions and mount descriptions are not global audit-only requests; a
separate explicit global restriction still wins.

