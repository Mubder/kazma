<!-- AGENTS.md, piece 62-security-audit-2026-11. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 62. The security audit of 2026-11-10: the High findings

Five findings, each fixed with a test that fails on the old code. The
audit's remaining findings are not listed here until each is fixed (the
repository is public).

- **A saved web extract is a note, never a file a program loads** (SEC-01,
  `tools/read_url._extract_path_refusal`). `read_url_to_file` (write tier, no
  card) and `crawl_site`'s save name their file through
  `_safe_workspace_path`, whose only rules were "inside the workspace" and
  "not there yet"; a JSON answer is saved byte for byte under `# ` header
  lines. A fetched page that steered the model could plant
  `.venv/Lib/site-packages/x.pth` (Python runs its `import` lines at the next
  start) or `kazma.local.yaml`. Now: a `.md` or `.txt` file with a plain
  name, in no dot folder and no folder of `GENERATED_DIRS` (compared without
  case), no `:` in the path, never one of Kazma's own files. Any other
  destination is `file_write`'s, which asks. Gate:
  `tests/test_extract_saves_are_notes.py` (the old rule as the control).
  Since 2026-11-15 every save lands under `research/` (or
  `KAZMA_RESEARCH_DIR`; `notes/x.md` becomes `research/notes/x.md`), never
  under a name in `read_url.LOADED_FILE_NAMES` (`requirements*.txt`,
  `constraints*.txt`, `CMakeLists.txt`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
  `SKILL.md`...): a page's text saved at the workspace root as
  `requirements.txt` was code at the next `pip install -r`.
- **shell_exec resolves what it is given** (`safety/shell_paths.py`,
  2026-11-15, the defensive check before the independent review: with the
  workspace the install folder, `grep -r` over `.` and the Windows short name
  `ENV~1` read the vault key). Every value is resolved to the file it opens
  (8.3 names, trailing dots, `::$DATA`, links, junctions, `~`, wildcards by
  long and short name) and asked `is_kazma_store`; a folder the program walks
  (`WALKS`: recursive grep, find, ls -R, du, tar -c, cp -r, git add...; the
  working folder when none is named) may not hold Kazma's files
  (`store_registry.kazma_file_under`). Asked before the card
  (`call_preflight.call_refusal` -> `shell_target_refusal`) and by the tool.
  git runs `shell_arguments.GIT_SUBCOMMANDS` only (an allowlist: aliases,
  `web--browse`, `send-email`, `instaweb` are out); tar refuses volume scripts
  and `host:path` archives. `git_commit` refuses to stage a Kazma file
  (`git add --dry-run` first). Gate: `tests/test_shell_exec_kazma_files.py`
  (every allowlisted binary is in `WALKS` or `NO_WALK`).
- **Kazma's configuration is readable, never writable**:
  `is_kazma_store(path, write=True)` covers `CONFIG_FILE_NAMES` (`kazma.yaml`,
  `kazma.local.yaml`, `kazma-permissions.yaml`) wherever Kazma reads them and
  the data-dir entries declared `config`; every write door asks with
  `write=True`. An approved `file_write` of `kazma.local.yaml` turned the
  approval gate off and added a hook command. Gate:
  `tests/test_kazma_config_files_protected.py`. Containment is by real path,
  and walks prune links (`workspace/real_paths.py`; gate
  `test_no_product_code_asks_isjunction`). **A git command is a write door
  too** (2026-11-16): `git checkout <branch>` and `git merge` write every
  file the other commit holds differently, an ignored `kazma.local.yaml`
  included (git takes an ignored file as expendable), so on an
  install-folder workspace a branch tracking it replaced the owner's
  configuration. `safety/git_tree_writes.git_command_refusal` lists the
  files a tree-changing subcommand writes (checkout, switch, merge,
  cherry-pick, revert, stash, restore, rm, mv; reads that start nothing the
  repository chose, `safety/git_read`) and refuses one of Kazma's; asked by
  `shell_paths.path_refusal` (before the card and where it runs) and by
  `git_checkout` and `git_merge`. `git_pull` fetches, checks the upstream
  commit (`switch_refusal`) and merges it by its hash; the push's rebase
  onto the remote is checked the same way. A list that cannot be read
  refuses. Gate: `tests/test_git_tree_writes.py` (git overwriting the
  ignored file, and a plain merge of the upstream, as witnesses; every
  tree-changing git argv in product code checked or declared).
- **A workspace's `kazma.yaml` is content, never configuration** (SEC-02).
  The settings store's YAML fallback is the install's own file for the life
  of the store. `ConfigStore.reload_from_root` (boot, every workspace switch
  and clone) pointed it at the active workspace, so a cloned repository's
  file answered every key with no stored row: chat-app allowlists (gateway
  admin), the commitment layer's switches, the offsite backup's destination.
  The method and its four callers are gone; nothing assigns `_yaml_path`
  outside `__init__`. Gate: `tests/test_workspace_yaml_is_not_config.py`
  (the repoint put back by hand as the control; a planted repoint).
- **The markdown renderer stashes every tag it writes** (SEC-03,
  `static/js/streaming.js` `inline`: `stash`, `oneToken`). Each generated
  `<a>`, `<img>` and `<span>` is held behind a token, like code spans, until
  the last pass has run, and restored newest first; a link or image address
  is one token (no space, quote, angle bracket or backtick, escaped or not).
  The auto-link pass ran over anchors already written: a second URL after a
  space inside a link's address closed the outer `href` and the rest became
  attributes (`onmouseover`). Never add a pass that reads emitted markup.
  Gate: `tests/js/test_markdown_no_attribute_injection.js` (the old passes as
  the control).
- **A chat-app sender sees and takes over only their own seasons** (SEC-04,
  `sessions/directory.py`: `list_directory(only_for=)`,
  `resolve_session(only_for=)`, `sender_may_take_over(admin=)`;
  `agent_handler/session_commands.py`). `/sessions` listed every chat and
  `/switch` bound the sender to any chat with no owner on record, which is
  every chat started on the Web; with an empty allowlist that was anyone who
  could message the bot (private since 2026-11-11, §42). A sender who is not a gateway admin
  (`allowlists.is_gateway_admin`) now lists and resolves their own seasons,
  and an unowned season is an admin's. `KAZMA_SESSION_OPEN_TAKEOVER=1` still
  opens both. Gate: `tests/test_session_takeover_needs_admin.py`.
- **A session's role is enforced in every mode** (SEC-05, `auth.py`
  `create_auth_middleware`: `_multi_user or _session_cred`). `role_allows`
  ran only under multi-user; an OIDC sign-in mints viewer/operator sessions
  with no local user, and such a session reached the MCP, settings and token
  routes. A request carrying a session credential is now always judged by
  its role; an unreadable user list is a WARNING and never "single
  operator". API tokens and the shared secret are unchanged. The install's
  mail and calendar connections (`/api/email`, `/api/calendar`) are admin
  prefixes. Gate: `tests/test_session_role_always_enforced.py`.
- With them: the API token hash is compared with `hmac.compare_digest`, and
  a file shared into a web chat that is not an inline image is served
  `Cache-Control: no-store` (`routes_chat_upload.shared_file`).

**The second batch (same audit, 2026-11-10)**, each with a test that fails on
the old code:

- **Agent tools.**
  - `shell_exec` finds git's and uv's real subcommand after their global
    options (`safety/shell_arguments.split_subcommand`; an unknown global
    option is refused): `git -C . config ...` and `uv --directory . run ...`
    passed the old first-word rule. More flags that start a program are
    refused (`git grep -O`, `git difftool --extcmd`, `git bisect run`,
    `sort --compress-program`/`-o`), abbreviated long options included
    (`matches_blocked`), and a value attached to a short option reaches the
    path policy for every binary (`attached_values`). Gate:
    `tests/test_shell_exec_subcommand_and_attached_values.py`.
  - The git read tools start no program the repository chose (`_git_read`:
    no external diff, textconv, pager, hooks, signature program or filter
    driver). Only `core.fsmonitor` was off. Gate:
    `tests/test_git_read_tools_start_no_repo_program.py` (a real repository).
  - Every branch and remote a git tool passes is a plain ref name
    (`_checked_ref`, `_checked_revision`), a remote must be one the
    repository has, and `--` precedes positionals. Gate:
    `tests/test_git_tool_refs_validated.py`.
  - A network or device path is refused on its text, before anything
    resolves it (`workspace/remote_paths.remote_path_reason`, called by
    `resolve_tool_path`, `check_path_access`, the shell argument check and
    the workspace routes): resolving `\\host\share` connects to that host.
    `/api/research/download` takes a name, never an absolute path. Gate:
    `tests/test_remote_paths_refused_before_touching.py`.
- **Secrets.**
  - `vault_retrieve`, `vault_store` and `vault_delete` are in
    `ALWAYS_HITL_TOOLS`, and the vault tools refuse Kazma's own rows
    (`vault.is_kazma_owned_secret`: `cfg:` names and install-scoped ones) and
    leave them out of `vault_list`. Gate: `tests/test_vault_tools_kazma_secrets.py`.
  - The universal backup keeps registry entries out by pattern
    (`store_registry.kept_out_of_backups`; `.migrate-*` is not backed up),
    and the offsite copy drops every secret-named file at any depth. The
    importer never extracts `meta.env`; the exporter removes it whatever
    happens. A leftover staging folder loses its `meta.env` and is otherwise
    kept. Gates: `tests/test_offsite_copy_leaves_env_out.py`,
    `tests/test_migration_leaves_no_vault_key.py`.
  - One redactor for every approval card (`security/approval_args.py`): the
    chat card, the swarm bus card and the Swarm page hide a secret-named
    argument, a tool's declared secret argument and URL passwords; every
    name stays and the digest is of the real arguments. Gate:
    `tests/test_approval_cards_hide_credentials.py`.
  - Every key the Settings masker masks is vault-routed
    (`is_sensitive_config_key`; the Web Push key was plaintext, and its
    vault row is install-scoped). Gate:
    `tests/test_masked_settings_go_to_the_vault.py`.
  - `read_system_logs` uses the shared `redact_secrets` (which gained JSON
    members, Authorization credentials and Telegram bot tokens). Gate:
    `tests/test_system_logs_redact_secrets.py`.
  - A first boot keeps its generated keys in the install's env file,
    restricted to the owner before the key is written
    (`env_files.persist_generated_secret`,
    `security/file_access.restrict_to_owner`). Gate:
    `tests/test_generated_keys_kept_in_the_install.py`.
- **Sign-in and sessions.**
  - The security switches (`boot_guard.SECURITY_SWITCHES`, the chat apps'
    two since 2026-11-11, §42) are read only
    through `boot_guard.env_flag` (1/true/yes/on). The app factory refuses a
    sign-in-off switch on a bind no launcher checked
    (`factory_exposure_refusal`). While sign-in is off, a loopback request
    whose Host could be a rebound public name is refused
    (`auth._open_mode_host_refused`). A name without a dot passes only when
    it is `localhost`, this machine's own name (`socket.gethostname()`, with
    and without its domain, `auth._machine_names`) or one
    `KAZMA_AUTOLOGIN_HOSTS` names: any single-label name passed until
    2026-11-11, and a DNS search suffix or a LAN name answer (LLMNR,
    NetBIOS) can hand a browser one. Gates:
    `tests/test_security_switch_spelling.py`,
    `tests/test_open_mode_rebinding_and_launchers.py` (the old single-label
    rule as the negative control).
  - A demo visitor is an operator: admin routes need an admin credential.
    Gate: `tests/test_demo_mode_admin_routes.py`.
  - Deleting, disabling, re-roling or resetting a platform user ends their
    sessions. Gate: `tests/test_user_changes_end_sessions.py`.
  - `/ws/voice` and `/ws/dashboard` ask their HTTP twins' role
    (`auth.websocket_principal`, `auth.principal_allows`). Gate:
    `tests/test_websocket_roles.py`.
  - OIDC: optional allowlists (`KAZMA_OIDC_ALLOWED_EMAILS`, `_DOMAINS`,
    `_GROUPS`), an unverified email refused, every role value read, a nonce,
    a rate-limited start route. Gate: `tests/test_oidc_who_may_sign_in.py`.
  - Telegram checks the sender before it downloads or transcribes. Gate:
    `tests/test_telegram_sender_checked_before_download.py`.
  - Every local sign-in costs one PBKDF2, and the username throttle is per
    address. Gate: `tests/test_login_timing_same_for_every_name.py`.
  - `analyze_image` fetches through `web_acquire.http.fetch_public` (pinned).
    Gate: `tests/test_vision_fetch_is_pinned.py`.
  - **An OAuth sign-in is one attempt, started by POST**
    (`kazma_core/security/oauth_attempts.py`, `kazma_ui/oauth_sign_in.py`;
    GitHub, Gmail, Google Calendar, Microsoft mail, Outlook Calendar).
    GitHub kept ONE install-wide state compared with `!=` (a second start
    replaced the first, and any browser could finish it), the mail and
    calendar sign-ins an unbounded dict in memory, and every start was a GET
    that wrote it. A start is a POST from Kazma's page (the CSRF rule; the
    mail and calendar routes also `_verify_same_origin`) that answers the
    provider's URL and sets a cookie named for the attempt
    (`oauth_attempts.binding_cookie_name`: HttpOnly, SameSite=Lax, the
    callback's path, ten minutes). The attempt is kept as its state's hash
    in the settings store (`oauth_attempts.PENDING_KEY`, RUNTIME, at most
    `oauth_attempts.MAX_PENDING`); the callback takes it once
    (`oauth_attempts.take`: the cookie compared with `hmac.compare_digest`
    before anything is read, then one `atomic_update`) and refuses a state
    used twice, from another browser, past ten minutes or of another
    provider, with a code the pages word (`window.kazmaSignInFailed`). A
    start from another origin than the callback is refused with the origin
    to open (409), before anything is kept. The page follows the answer with
    `window.kazmaStartSignIn` (http(s) only); the GitHub callback sends the
    browser back to the page it started from
    (`oauth_attempts.safe_return_path`). Gates:
    `tests/test_oauth_attempts.py` (the real app, stand-in providers; the
    old single state as the negative control),
    `tests/js/test_kazma_start_sign_in.js`.
- **Infrastructure.**
  - The root `.dockerignore` excludes every secret file name and `.kazma/`
    at any depth. Gate: `tests/test_dockerignore_secrets.py`.
  - Headers: `style-src`, `font-src`, `frame-src`, `worker-src`,
    `manifest-src`, HSTS over HTTPS only, `Cross-Origin-Resource-Policy`,
    `Permissions-Policy`, no `Server` header. No `default-src`: every kind
    a page uses is named.
  - **No script from another host; connections to this server and the
    configured LiveKit server only** (2026-11-11,
    `kazma_ui/security_headers.py`). The LiveKit browser client was loaded
    from cdn.jsdelivr.net, unpinned, when Live voice started, and kept the
    pages from sending `script-src`. It ships with Kazma now
    (`static/vendor/livekit`: `livekit-client` 2.23.0, Apache-2.0, its
    version, npm integrity and SHA-256 in `vendored.json`, rebuilt by
    `scripts/vendor_livekit.py`; the two files are `-text` so a Windows
    checkout keeps their bytes). The policy: `script-src 'self'
    'unsafe-inline' 'unsafe-eval'` (the pages' inline scripts and handlers,
    and Alpine's expressions, compiled with `new Function`), and
    `connect-src 'self'`, this server's own `ws`/`wss` origin, and the
    LiveKit server (`LIVEKIT_URL` or `voice.livekit.url`, while duplex is
    on; `_livekit_connect_sources`: its `wss` and `https` origin, a plain
    origin only, and `*.livekit.cloud` for LiveKit Cloud). The LiveKit
    address is read off the loop and kept `_LiveKitSources.TTL_S` seconds
    (`_livekit_sources`: the first response waits for one read in a thread,
    later ones refresh in the background). A page keeps the policy it was
    opened with: a LiveKit address saved in Settings reaches pages opened
    after the save (Settings says so). Gates: `tests/test_security_headers.py`
    (the directives, a hostile address or Host, the read off the loop and
    kept; the old policy as the control), `tests/test_vendor_livekit.py`
    (the files as recorded, served as a script, no page loading a script,
    stylesheet or module from another host; the old `voice.js` line
    planted), `tests/e2e/test_pages_load_clean.py` (every page and Settings
    tab under the policy; a CDN script and an outside socket refused, the
    old policy as the control), `tests/e2e/test_livekit_client_and_policy.py`
    (the vendored client defines `LivekitClient.Room`; the configured LiveKit
    host reachable, an outside one and an unconfigured one refused, live
    voice's own socket never).
  - **No sign-in runs in a popup; every page sends
    `Cross-Origin-Opener-Policy: same-origin`** (2026-11-11). The GitHub
    sign-in opened a window whose callback page answered
    `window.opener.postMessage`, so the opener policy had to stay off. Every
    sign-in now runs in the same tab (`window.kazmaStartSignIn`, the callback
    sends the browser back), and the header is on. The one window a page
    still opens for a sign-in is an MCP server's authorization page
    (`mcp.js`, `noopener`, its callback a loopback listener of its own,
    declared). Gates: `tests/test_no_popup_sign_in.py` (no `window.open` of
    a sign-in, no opener read, no window `message` listener in any product
    script, template or server page; planted shapes and the old Workspace
    line as negative controls), `tests/test_security_headers.py`,
    `tests/e2e/test_github_sign_in_same_tab.py` (reached by the sidebar,
    English and Arabic: GitHub and Gmail sign-ins with stand-in providers,
    no window opened).
  - MCP presets name an exact version, held to the recorded registry answer
    (`tests/test_mcp_catalog.py`). The release workflow checksums the built
    files before any other tool runs and checks them again before signing;
    the metrics workflow's token job installs nothing.
  - **SearXNG's secret key is generated by setup** (2026-11-11, the owner's
    decision; `security/searxng_secret.py`). `kazma setup install`'s private
    search part keeps a generated `SEARXNG_SECRET` in its `.env` before it
    starts SearXNG (`container_services.ensure_secret`, through
    `env_files.persist_generated_secret(path=)`: restricted before the value
    is written; an owner's value, in the file or the shell, is never
    replaced; the placeholder is not a value) and runs compose with
    `--env-file` on that file. The part is not in place while the key is
    missing, so a re-run fixes an older install. `docker-compose.yml` keeps
    the placeholder as its fallback, so a start without setup still works;
    Kazma's SearXNG container on it (`uses_placeholder`: its
    `SEARXNG_SECRET`, else the `secret_key` of the settings file it mounts)
    is a boot WARNING (`service_exposure.searxng_secret_problems`, no ops
    alert) and the security report's `check_searxng_secret`. Gate:
    `tests/test_searxng_secret_generated.py` (the old setup as the negative
    control).

