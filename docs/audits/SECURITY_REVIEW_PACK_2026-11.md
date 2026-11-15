# Security review pack: Kazma, the whole product

Prepared 2026-11-15 for an independent security review of
[`Mubder/kazma`](https://github.com/Mubder/kazma) (public), on a commit the
reviewer pins. The Documents security profile had its own independent review
in October (`docs/audits/Q04_DOCUMENTS_SECURITY_REVIEW_PACK_2026-10-15.md`,
results in `docs/audits/Q04_REVIEW_RESULTS_2026-10-15.md`); this pack covers
everything else, and Documents again only where the rest of Kazma reaches it.

Before this pack went out, the authors ran a defensive check of its claims:
five AI reviewers that had not written the code, each trying to break one
group of claims from the code and the tests. They found about forty gaps,
among them a model-options route that returned provider keys, `shell_exec`
reaching secret files through recursion and Windows short names, a writable
configuration file that could turn approvals off, delegated tasks that
carried a model-chosen address past the outside-text rule, an unbounded
request body before sign-in, API tokens without a role check, and a release
tag that could skip the update signature. Each was fixed first, as a class,
with a test the old code fails (commits `c00f6caae` to `23cec1cde`, 15
November 2026), and section 4 states the claims as the code now holds them.
That check was self-review, not this review, and a check like it has missed
things before (the October Documents review found one).

Everything below was written by the people who built Kazma. Treat each claim
as something to break.

## 1. What we ask

1. Test whether the claims in section 4 hold on the commit you pin, for a
   single-operator install (section 2).
2. Report anything that breaks a claim, and anything section 6 should have
   listed and did not.
3. Deliver the items in section 8.

Depth, method and time are yours. Section 6 says where we think the risk is;
it is not a limit.

## 2. The system in one page

Kazma is an AI agent that one person runs for themselves, on their own
computer or server (`docs/THREAT_MODEL.md`, "one operator per install"). The
agent reads the web, documents, email and chat messages, and acts through
about 170 tools: files, a shell, code execution, a browser, email, calendar,
X, GitHub, scheduled tasks, a swarm of worker agents. Any text it reads may be
hostile and may try to steer it (prompt injection). The design goal is that a
steered agent still cannot do what its owner did not approve, and cannot
reach Kazma's own secrets.

**Processes.**

- **The server** (`kazma-ui`, FastAPI on uvicorn, `kazma_ui/app.py`): the web
  UI and its API, the agent (a LangGraph graph in `kazma-core`), the chat-app
  gateway (`kazma-gateway`), scheduled jobs, background maintenance. It binds
  to `127.0.0.1` by default; remote access goes through a proxy or tunnel the
  owner declares (`KAZMA_TRUSTED_PROXIES`).
- **The guard** (`scripts/service/kazma_guard.py`, standard library only): a
  supervisor that starts the server, probes its health, reloads it, starts
  Docker and the database when they stop, watches disk and power, and pages
  the owner. On Windows it runs as a boot task with administrator rights (the
  owner's decision; the server is its child).
- **The document worker broker** (`kazma_core/documents/worker_broker.py`): the
  only holder of the Docker socket for document jobs; one container per job.
  Reviewed in October.
- **Code execution** (`python_exec`): a Docker container per call, or a local
  sandboxed process where Docker is not used (`tools/code_exec.py`).
- **Kazma Watch** (`deploy/observer`, a Cloudflare Worker the owner deploys to
  their own account): watches the install from outside and pages when it goes
  quiet. It receives signed heartbeats from the guard.

**Ways in.**

| Way in | Who can use it | Code |
|---|---|---|
| Web UI and `/api` | The owner, signed in (a password, an API token, OIDC, or loopback auto sign-in) | `kazma_ui/auth.py`, `platform_rbac.py`, `csrf.py` |
| Chat apps: Telegram, Discord, Slack, WhatsApp | Allowed senders only (an empty allowlist means the owner's known accounts) | `kazma_gateway/adapters/`, `sender_rule.py` |
| Webhooks: Telegram, WhatsApp, OAuth callbacks | The platform, proven by its secret or signature | `auth.ALWAYS_OPEN_PREFIXES` |
| `kazma` CLI and TUI, `kazma mcp` (MCP over stdio) | The local user | `kazma-cli`, `kazma-tui` |
| A2A (agent to agent), off by default | Callers with an API token; read-only turns | `kazma_ui/a2a_api.py` |
| Content the agent reads | Anyone who can put text on a page, in a document, an email or a message | `web_acquire/`, `safety/prompt_fence.py` |

**Where data lives.** PostgreSQL for settings, chats and checkpoints (or SQLite
on a small install), a set of SQLite stores (every one is listed in
`kazma_core/store_registry.py`), and an encrypted vault for credentials
(`security/vault.py`: AES-256-GCM with the `cryptography` library, the key from
`KAZMA_VAULT_KEY` through PBKDF2-HMAC-SHA256, 600,000 iterations).

**Ways out.** Model providers (chosen by the owner), web pages and search,
email and calendar (Google, Microsoft, IMAP), X, GitHub, the chat apps,
optional remote document parsing (off by default), offsite backups.

The rules each part must keep, and why, are in `AGENTS.md` (sections are
cited below as §n). `docs/THREAT_MODEL.md` says what the boundaries do not
stop; `docs/docs/security/safety-design.md` describes how they are built.

## 3. Scope

| In scope | Where |
|---|---|
| Sign-in, sessions, roles, CSRF, request origin, proxies, OAuth and OIDC sign-in | `kazma_ui/auth.py`, `proxy_headers.py`, `csrf.py`, `platform_rbac.py`, `oauth_sign_in.py`, `kazma_core/security/web_sessions.py`, `security/oauth_attempts.py` |
| The approval gates (human in the loop) on every path a tool can run | `agent/graph_tool_worker.py`, `agent/tool_registry.py`, `safety/hitl.py`, `safety/hitl_gates.py`, `safety/call_authority.py`, `mcp/`, `kazma_ui/routes_direct/misc.py` |
| What the tools may touch: shell, code execution, files, Kazma's own stores and secrets, protected settings | `tools/`, `agent/tool_builtins/`, `safety/shell_arguments.py`, `security/child_env.py`, `store_registry.py`, `safety/protected_config.py`, `workspace/` |
| Fetching from the internet and handing outside text to the model | `web_acquire/`, `security/ssrf.py`, `security/ssrf_pin.py`, `safety/prompt_fence.py`, `safety/outside_text_fetch.py` |
| The web page's own safety: markdown rendering, modals, headers, CSP | `kazma_ui/static/js/streaming.js`, `security_headers.py` |
| Chat apps, webhooks, slash commands and buttons | `kazma_gateway/` |
| A2A and the MCP server | `kazma_ui/a2a_api.py`, `kazma-cli` |
| Secrets: vault, masking in answers, cards and logs, backups and migration | `security/vault.py`, `security/config_mask.py`, `security/approval_args.py`, `security/url_credentials.py`, `backup/`, `migration/` |
| Setup, update and release: what Kazma downloads and runs | `setup.ps1`, `setup.sh`, `kazma_cli/update.py`, `kazma_cli/release_signature.py`, `.github/workflows/release.yml`, Dockerfiles, compose files |
| Kazma Watch's signed requests | `deploy/observer/src/` |

**Out of scope**, with why:

- **Several people on one install.** Kazma is single-operator today: with
  platform users, the Swarm, Scheduled, Knowledge, Documents and Running-work
  surfaces refuse (decision D1), and the threat model says anyone let in acts
  with the owner's authority. Spaces, the multi-person design, is planned
  (`docs/plans/SPACES_PLAN.md`) and will need its own review.
- **What the owner chooses to allow.** An approved call does what it says;
  turning approvals off (YOLO) or allowing a tool for a session is the owner's
  decision. Approval is consent, not containment (`docs/THREAT_MODEL.md` §1).
- **Container to kernel escapes**, and the local (non-Docker) code sandbox as a
  security boundary (`docs/THREAT_MODEL.md` §2).
- **The model provider's own handling of what Kazma sends it.**
- **The Documents container profile**, already reviewed (Q04).
- **The live install and kazma.ai.** Please test on your own machine.

## 4. Claims to test

Each claim names where it is enforced and the tests that hold it today. A
passing test is our evidence, not proof: please try to break the claim, not
the test.

| # | Claim | Enforced in | Held by |
|---|---|---|---|
| C1 | Every `/api` route needs a credential, except the declared open prefixes (OAuth callbacks, the Telegram and WhatsApp webhooks) and the health probes; the A2A agent card answers only while A2A is on. Every gate judges the path the router serves, and a decoded `?` or `#` in a path is refused | `kazma_ui/auth.py` (`ALWAYS_OPEN_PREFIXES`, `HEALTH_PROBE_PATHS`, `is_always_open`), `kazma_ui/request_path.py` | `tests/test_thread_route_policy.py`, `tests/test_gates_judge_the_routed_path.py`, `tests/test_a2a.py`, `tests/test_websocket_roles.py` |
| C2 | Loopback auto sign-in is never given to a request that came through a declared proxy; forwarded headers are honoured only from declared proxies; an undeclared proxy is detected and turns peer trust off; while sign-in is off, a loopback request whose Host could be a rebound public name is refused | `kazma_ui/proxy_headers.py`, `auth._peer_trust_allowed`, `auth._is_trusted_proxy`, `auth._open_mode_host_refused`, `security/boot_guard.py` | `tests/test_forwarded_headers_peer.py`, `tests/test_open_mode_rebinding_and_launchers.py`, `tests/test_auth_disabled_behind_proxy.py`, `tests/test_security_switch_spelling.py` |
| C3 | Every credential but the shared secret carries a role (an API token: the role chosen when it was made, operator by default), enforced in every mode, OIDC included; install-wide writes (credentials, the workspace, the chat apps, model profiles) need an admin; each WebSocket asks its HTTP twin's role; ending or changing a user ends their sessions | `platform_rbac.role_allows`, `platform_rbac._ADMIN_WRITE_PREFIXES`, `auth.create_auth_middleware`, `auth.websocket_principal` | `tests/test_session_role_always_enforced.py`, `tests/test_api_token_roles.py`, `tests/test_install_wide_writes.py`, `tests/test_websocket_roles.py`, `tests/test_demo_mode_admin_routes.py`, `tests/test_user_changes_end_sessions.py` |
| C4 | A state-changing request from another origin is refused; a WebSocket accepts a cookie only with a same-origin handshake | `kazma_ui/csrf.py`, `auth._ws_origin_allowed` | `tests/test_csrf.py`, `tests/test_ws_origin_cswsh.py` |
| C5 | Each OAuth sign-in is one attempt, started by a POST from Kazma's page and bound to that browser; a state used twice, from another browser, late or for another provider is refused. OIDC sign-in carries a nonce, refuses an unverified email and applies the configured allowlists. No sign-in runs in a popup | `kazma_core/security/oauth_attempts.py`, `kazma_ui/oauth_sign_in.py`, `auth.py` (OIDC) | `tests/test_oauth_attempts.py`, `tests/test_oidc_who_may_sign_in.py`, `tests/test_no_popup_sign_in.py` |
| C6 | Every sign-in attempt costs the same work whatever the username; attempts are throttled per address and per username, with no global lockout | `auth.authenticate_local_user`, the login route | `tests/test_login_timing_same_for_every_name.py`, `tests/test_login_throttle.py` |
| C7 | Every registered tool has a tier; a danger or unclassified tool asks a person before it runs, on the chat graph, the swarm and IDE registry, and the MCP executor; an approval covers exactly the call it showed | `safety/hitl.py` (`approval_floor`), `agent/graph_tool_worker.py`, `agent/tool_registry.py`, `safety/call_authority.py`, `mcp/` | `tests/test_hitl_wiring.py`, `tests/test_tool_arguments_reach_the_tool.py`, `tests/test_hitl_gates.py`, `tests/test_swarm_bus_approvals.py` |
| C8 | Always-ask tools (posting to X, the vault tools, folder access) are never skipped by YOLO or a standing grant, and their card offers a one-time approval only; a draft is published only after the user has seen it | `ALWAYS_HITL_TOOLS`, `safety/public_actions.py`, `safety/commitment/proposals.py` | `tests/test_always_ask_one_time_only.py`, `tests/test_folder_access_always_asks.py`, `tests/test_public_action_cards.py`, `tests/test_proposal_seen_before_publish.py`, `tests/test_x_post_proposal_gate.py` |
| C9 | An approval resumes only the live interrupt the registry holds as pending, keyed by its id; a retried, stale or foreign decision resumes nothing | `kazma_ui/routes_direct/misc.py`, `safety/hitl_gates.py`, `hitl_decision.record_gate_decision` | `tests/test_time_travel_rewind.py` (`test_every_approval_resume_is_keyed_by_its_interrupt`), `tests/test_hitl_gates.py` |
| C10 | `shell_exec` runs only allowlisted programs, refuses shell metacharacters and every argument that makes an allowed program run another program: git runs an allowlist of subcommands (parsed past its global options), tar refuses volume scripts and remote archives; git never runs a program the repository chose without the card naming it | `agent/tool_builtins/system.py`, `safety/shell_arguments.py` (`GIT_SUBCOMMANDS`), `safety/git_repo_programs.py` | `tests/test_shell_exec_kazma_files.py`, `tests/test_shell_exec_subcommand_and_attached_values.py`, `tests/test_shell_git_programs.py`, `tests/test_git_read_tools_start_no_repo_program.py`, `tests/test_git_tool_refs_validated.py` |
| C11 | Catastrophic commands and code (deleting the root, fork bombs, piping a download into a shell) are refused before any card; `python_exec` snippets run with restricted builtins, or in Docker with Kazma's folders and secret files hidden | `safety/commitment/authorize.py`, `safety/commitment/python_denylist.py`, `tools/code_exec.py` | `tests/test_python_exec_denylist.py`, `tests/test_code_exec.py`, `tests/test_sandbox_hides_kazma_files.py` |
| C12 | A process a tool starts gets none of Kazma's secrets in its environment | `security/child_env.py` (`tool_child_env`) | `tests/test_child_env.py` |
| C13 | No tool reads or writes Kazma's own stores or secret files (the vault, `.env`, the restic password, database copies), whatever the path or tool: `shell_exec` resolves every value to the file it opens (Windows short names, links, junctions, wildcards) and refuses a program that walks a folder holding them; a folder grant cannot open one; Kazma's configuration files can be read but never written by any tool | `store_registry.is_kazma_store`, `store_refusal`, `safety/shell_paths.py`, `workspace/real_paths.py`, every file, SQL and shell door | `tests/test_store_registry.py`, `tests/test_kazma_secret_files_refused.py`, `tests/test_shell_exec_kazma_files.py`, `tests/test_kazma_config_files_protected.py`, `tests/test_git_commit_kazma_files.py`, `tests/test_vault_tools_kazma_secrets.py` |
| C14 | A relative path means the active workspace; a network or device path is refused before anything resolves it; the agent cannot write protected settings (approvals, security, MCP commands, hooks) | `workspace/binding.resolve_tool_path`, `workspace/remote_paths.py`, `safety/protected_config.py` | `tests/test_tool_paths.py`, `tests/test_remote_paths_refused_before_touching.py`, `tests/test_protected_config.py` |
| C15 | Every fetch of an address that the model, a page, a message or a remote server chose goes only to public addresses, checked at every redirect and pinned to the checked IP: pages, the browser, chat and voice attachments, generated images and MCP sign-in alike; an unpinned request whose address is not written in the code is declared with why | `web_acquire/http.py`, `web_acquire/dns_worker.py`, `security/ssrf.py`, `security/ssrf_pin.py`, `security/pinned_request.py`, `web_acquire/browser_network.py`, `mcp/oauth.py` | `tests/test_chosen_urls_fetched_pinned.py`, `tests/test_web_acquisition_safety.py`, `tests/test_ssrf_pin_authority.py`, `tests/test_acquisition_dns.py`, `tests/test_chat_attachments_fetched_pinned.py`, `tests/test_mcp_oauth_pinned.py`, `tests/test_image_downloads_pinned.py`, `tests/test_browser_acquisition_broker.py` |
| C16 | Outside text reaches the model inside an untrusted-data fence that a forged or disguised tag cannot close; after a turn has read outside text (a page, a saved page read back, the Knowledge block, an earlier page still in its history), a fetch to a host the user did not name asks first, and a task the turn starts (a sub-agent, a swarm task, a handoff, a scheduled task) carries that state | `safety/prompt_fence.py`, `safety/outside_text_fetch.py` | `tests/test_static_gates.py` (`test_no_unfenced_web_tool_output`), `tests/test_outside_text_fetch.py`, `tests/test_outside_text_delegation.py`, `tests/test_fence_marker_variants.py` |
| C17 | A saved web page is a plain note under the research folder, never a file another program loads; a workspace's own `kazma.yaml` is never Kazma's configuration | `tools/read_url.py` (`_extract_path_refusal`, `LOADED_FILE_NAMES`), `config_store.py` | `tests/test_extract_saves_are_notes.py`, `tests/test_workspace_yaml_is_not_config.py` |
| C18 | The web chat cannot be made to run script, set an attribute or load an off-site resource from a model's reply; pages load scripts only from this server and connect only to it and the configured LiveKit server | `static/js/streaming.js`, `kazma_ui/security_headers.py` | `tests/js/test_markdown_no_attribute_injection.js`, `tests/js/test_markdown_images.js`, `tests/test_security_headers.py`, `tests/test_vendor_livekit.py` |
| C19 | Chat apps answer only allowed senders, on every accept path, before any download; commands and buttons that act for everyone (posting to X, installing skills, approvals) need an admin; a sender reaches only their own sessions | `kazma_gateway/sender_rule.py`, `allowlists.py`, `slash_commands.changes_global_config`, `sessions/directory.py` | `tests/test_chat_apps_private_by_default.py`, `tests/test_chat_command_effects_need_admin.py`, `tests/test_slash_admin_gate.py`, `tests/test_telegram_sender_checked_before_download.py`, `tests/test_session_takeover_needs_admin.py` |
| C20 | The Telegram webhook needs its secret; the WhatsApp webhook verifies Meta's signature on the raw body before parsing and answers nothing while no app secret is saved; every secret comparison is constant-time on bytes | `kazma_gateway/adapters/telegram.py`, `adapters/whatsapp.py`, `whatsapp_parse.signature_ok`, `security/secret_compare.py` | `tests/test_whatsapp.py`, `tests/test_secret_compare.py` |
| C21 | A2A turns and read-only scheduled tasks are read-only: they never write a file, keep nothing in memory and open no approvals; an A2A request is at most 256 KiB | `kazma_ui/a2a_api.py`, `cron/capabilities.py`, `side_effects.READ_ONLY_ARGUMENTS` | `tests/test_a2a.py`, `tests/test_read_tools_never_write.py` |
| C22 | Credentials are stored in the vault, never in plaintext settings, kazma.yaml or config.raw, a password inside any stored address included | `security/vault.py`, `config_store._secret_vault_name`, `mcp/secrets.py` | `tests/test_url_passwords_vaulted.py`, `tests/test_mcp_secrets.py`, `tests/test_masked_settings_go_to_the_vault.py`, `tests/test_url_credentials.py` |
| C23 | No answer, card or log line carries a secret (masked by key name and by value: URL passwords, inside text too, and every token shape in one table), and no answer carries an exception's own text | `security/token_shapes.py`, `security/config_mask.py`, `security/provider_mask.py`, `security/approval_args.py`, `security/url_credentials.py`, `logging_config.py`, `errors.py` | `tests/test_token_shapes_masked.py`, `tests/test_no_get_route_answers_a_provider_key.py`, `tests/test_settings_never_echo_secrets.py`, `tests/test_config_read_masks_secrets.py`, `tests/test_approval_cards_hide_credentials.py`, `tests/test_gate_records_keep_no_credentials.py`, `tests/test_logs_never_carry_url_passwords.py`, `tests/test_route_errors_carry_no_exception_text.py` |
| C24 | A secret file is readable by its owner only whenever Kazma writes or copies it (env files, backups, restores, archives, migration bundles), checked after every write; offsite copies leave secret files out; a migration bundle carries the vault key by design, says so, and is never written inside a git checkout by default | `security/owner_only.py`, `security/file_access.py` (`write_private`, `copy_private`), `env_files.py`, `backup/universal.py`, `migration/` | `tests/test_secret_files_owner_only.py`, `tests/test_env_files_written_one_way.py`, `tests/test_offsite_copy_leaves_env_out.py`, `tests/test_migration_leaves_no_vault_key.py`, `tests/test_generated_keys_kept_in_the_install.py`, `tests/test_secret_file_access_check.py` |
| C25 | Setup and update run nothing unchecked: uv is checked by SHA-256 before it is unpacked; a release tag is read one strict way, and an update installs a wheel only after its Sigstore signature, checksum and GitHub digest verify and its own version equals the tag; Docker's package key is trusted only by its fingerprint; actions are pinned by commit, images by digest, MCP servers and the embedding model by exact version | `setup.ps1`, `setup.sh`, `kazma_cli/update.py`, `kazma_cli/release_signature.py`, `container_engine_assets/setup-engine.sh`, `.github/workflows/` | `tests/test_setup_downloads_verified.py`, `tests/test_update_checks_release_signature.py`, `tests/test_engine_docker_key.py`, `tests/test_actions_pinned_by_sha.py`, `tests/test_mcp_shipped_servers_pinned.py`, `tests/test_embedding_model_pinned.py`, `tests/test_release_supply_chain.py`, `tests/test_workflow_permissions.py` |
| C26 | Kazma Watch accepts only requests signed over their method, path, time and body with the install's key; a replay, an old signature form or a signature for another route is refused; its one open route, a problem report, pages only a checked version and a cleaned note, marked as a report | `deploy/observer/src/verify.js`, `deploy/observer/src/reports.js` | `deploy/observer/test/` (`node --test deploy/observer/test/*.test.mjs`), `tests/test_observer_heartbeat.py`, `tests/test_outside_watch.py` |
| C27 | Every request body has a limit, refused before it is read: 64 KiB before sign-in, 2 MiB after, larger limits declared per route that needs them | `kazma_ui/body_limits.py` | `tests/test_request_body_limits.py` |
| C28 | A problem report sends exactly what the person was shown, after redaction, and nothing without pressing Send | `kazma_core/support/problem_report.py`, `kazma_ui/support_api.py`, `agent_handler/report_command.py` | `tests/test_problem_report.py` |

## 5. Setting up

Any Windows 11 or Linux machine with Python 3.11 to 3.14 and, for code
execution in containers, Docker.

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma
git checkout <the commit you review>
./setup.sh --mode express -y          # Windows: setup.cmd -Mode express -Yes
```

Setup installs Kazma as a service on port 9090 and opens the setup page; any
model provider works (a local Ollama or LM Studio needs no key). To run from
the checkout without a service: `uv sync --extra dev` then
`python -m kazma_cli serve`.

The claims' tests run without a model or network:

```bash
uv sync --extra dev
python -m pytest <the test files in section 4> -q
python scripts/fast_test.py            # the whole suite, about ten minutes
node --test deploy/observer/test/*.test.mjs   # Kazma Watch
```

CI runs the same suite on Linux, Windows and PostgreSQL
(`.github/workflows/ci.yml`; every job is a required check on `main`).

## 6. Where we think the risk is

Suggestions, roughly by the damage a hole would do:

1. **A steered agent doing what was not approved.** Prompt injection that
   reaches a danger tool with no card, a card that shows one call and runs
   another, an approval that resumes a different question, a tool that runs a
   program the allowlist did not name (through arguments, environment,
   configuration files, git or uv), code execution that reaches the host.
2. **Kazma's secrets.** Reading the vault, `.env`, provider keys or tokens
   through any tool, answer, log, card, backup or error message.
3. **Server-side request forgery.** Making Kazma fetch an internal address
   (the database, the document broker, cloud metadata) through any fetch path,
   redirect, DNS answer or browser subresource.
4. **Sign-in.** Reaching the API without a credential, through the proxy and
   loopback rules, a WebSocket, an OAuth or OIDC flow, or a role mix-up.
5. **Chat apps.** A stranger getting answers, approving a card, switching into
   the owner's session, or a webhook accepting a forged call.
6. **The web page.** Script running in the owner's browser from a model's reply,
   a page title, a document name or a chat message.
7. **The supply chain.** An update or setup step that runs code it did not
   check.

## 7. Known limits (please report only if worse than stated)

From `docs/THREAT_MODEL.md` and the hardening guide:

- One operator per install; anyone let in acts with the owner's authority.
- An approval is consent: once given, the call does what it says.
- The prompt fence reduces how often outside text steers the model; it does
  not prevent it. The approval gates are the boundary.
- `shell_exec` is a narrow door, not a jail: an allowlisted program can still
  read what the account can read inside the workspace.
- The local code sandbox (no Docker) restricts the snippet's builtins only; the
  production profile requires Docker (`security.execution.container_required`).
- The guard runs with administrator rights on Windows (the owner's decision);
  the server is its child.
- On Windows the strict browser network mode refuses to launch; Linux needs a
  network namespace and cgroup controls for it.
- A host that appears in a search result the agent made counts as named by
  the user for the outside-text rule, so research can open what it found
  (the owner's choice; `AGENTS.md` §7).
- Addresses the owner configured (a model server, an MCP server, a backup
  endpoint, an identity provider) are reached as configured, on the
  owner's network too; the pinned path is for addresses someone else chose
  (each such client is declared in `tests/test_chosen_urls_fetched_pinned.py`).
- No fuzzing is set up yet.

## 8. What to deliver, and how

- **Report privately**, by the channels in `SECURITY.md` (GitHub private
  vulnerability reporting, or email). The repository is public: please do not
  open public issues for findings.
- **A scope statement:** the commit, the platforms, what you tested and how,
  and what you did not test.
- **Each finding:** an id, a title, severity with a CVSS vector, the claim it
  breaks (C1 to C28) or "not covered", the component and commit, a minimal
  reproduction, the impact, and a suggested fix if you have one.
- **A retest** of each finding on the commit that fixes it.

How results are recorded here: the owner adds
`docs/audits/SECURITY_REVIEW_RESULTS_<date>.md` (scope, findings, fixes,
retests) once every finding with security impact is fixed. Each fix lands with
a behavioural test and a negative control (the old code failing it), as every
fix in this repository does.

## 9. For an audit programme's intake (OSTIF and others)

- **Project:** Kazma, an open-source AI agent framework (MIT), Python 3.11+,
  about 430,000 lines of product code in six packages and 390,000 lines of
  tests. Maintained by its author.
- **Security policy:** `SECURITY.md`, `docs/docs/security/`.
- **Previous reviews:** the Documents profile (Q04, October 2026, independent);
  internal audits in `docs/audits/` (each finding fixed with a test before its
  report was published).
- **Dependencies:** listed in `pyproject.toml`, locked in `uv.lock`; Dependabot
  watches uv, GitHub Actions, Docker and compose; Kazma's own advisory watch
  reports new OSV advisories daily.
- **Encryption libraries:** `cryptography` (AES-256-GCM, PBKDF2-HMAC-SHA256);
  Python's `hmac`, `hashlib` and `secrets`; Sigstore for release signing and
  verification.
- **CI/CD:** GitHub Actions; CodeQL code scanning (Python, JavaScript, C#,
  Actions), Bandit (high-severity gate), Ruff, every action pinned by commit,
  OpenSSF Scorecard weekly (`.github/workflows/scorecard.yml`); releases built
  and signed by `.github/workflows/release.yml` with build provenance.
- **Fuzzing:** none yet.
- **Contacts:** the maintainer, by `SECURITY.md`'s channels.
