<!-- AGENTS.md, piece 46-slash-commands. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 46. Chat-app slash commands say what they measured (2026-10-01)

- **A fact a command shows is read from where it lives**
  (`agent_handler/commands._build_slash_ctx`, per command). `/status`
  reports the `GatewayManager` (`stats`, `connection_report()`,
  `messages_in_progress()` -- the asking message excluded); `/cost` the
  per-call ledger (`llm_ledger.thread_usage`, off the loop); `/context` the
  chat's checkpoint through the `context_info` tool's report
  (`tools/context_cmd.context_report`, one report for both, off the loop);
  `/config` the active profile's model. A fact that cannot be read is left
  out and the command says so. Until then `/status` and `/cost` showed
  constants and `/context` measured the command itself.
  `tests/test_gateway_slash_facts.py` fails on a key a command reads that
  the builder fills from nothing, or from a literal.
- **`/config` writes one key, and a failed write is never a success.**
  `/config model` goes through `slash_commands.switch_model_from_chat`
  (`switch_active_model`: model AND provider, §1), which the `/model` menu
  uses too, in a thread; `/config memory` sets `memory.enabled`;
  `/config tools toggle` is Settings → MCP's `set_mcp_server_enabled`. They
  used to save the whole merged configuration back (`_save_config`, gone),
  and the tools toggle wrote `mcp.disabled_servers`, which nothing reads.
- **One rule for the admin gate:** `slash_commands.changes_global_config`
  (audit H-8, `allowlists.is_gateway_admin`), asked by `graph.py` before
  every slash command: code (`/skill install|uninstall`), the model, memory,
  MCP servers, the personality (the supervisor reads it on every step, for
  every user), the active workspace (`/ide repo switch|clone|<owner/repo>`)
  and the swarm's output routing (`/swarm config group|clear`). Showing and
  listing stay open. A new command that changes Kazma for everyone goes
  into it (`tests/test_slash_admin_gate.py`). The `/model` menu's pick runs
  before that gate and checks the same admin rule itself.
- **The "/" menu, `/help` and the reference agree**
  (`tests/test_slash_help_and_menu.py`): `BOT_MENU_COMMANDS` and
  `_cmd_help()` name the same commands, and
  `docs/docs/reference/slash-commands.md` shows `_cmd_help()`'s text and
  names every menu command. A new command needs all three. `/stop` is
  `/abort` on every surface (catalog, gateway, web chat): three "/stop"s
  answered "Unknown command" while a turn ran (live 2026-10-20).
- **A chat command that acts for everyone is an admin's** (2026-11-15, the
  defensive check before the independent review: `/x approve` posted to the
  owner's X account for any allowed sender). Every `/x` word but help,
  `list` and `subjects` (`slash_commands.X_OPEN_SUBCOMMANDS`, so a new word
  starts closed) and the `/skill owner/repo` shorthand are admin-only. Gate:
  `tests/test_chat_command_effects_need_admin.py` reads each `_try_*_command`
  branch's calls: a branch that acts needs `changes_global_config` or a
  `DECLARED_OPEN` reason (the old rule and a planted branch as controls).

