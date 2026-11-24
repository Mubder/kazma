<!-- AGENTS.md, piece 47-settings-restore. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 47. Settings backup and restore keep the owner's keys (`kazma_core/settings_restore.py`, 2026-10-01)

The owner's rule: a restore never makes them enter a key again, and never
replaces one they have.

- **Every settings key is classed** in `KEY_RULES`: a setting, Kazma's own
  state (`system.`, grants, a chat's long-task record, cursors), a credential
  (`security.`, `account.`, `web_session.`, `platform.users`, push keys,
  `mcp.oauth.`) or learned data (the Soul). A backup holds settings and the
  learned Soul only, keys as `vault://` references, plaintext secrets and URL
  passwords as `****`. A restore writes settings only, so state and
  credentials in an older file are never written. Gate:
  `tests/test_settings_restore_classes.py`. It reads every write to the
  settings store in product code (literals, f-strings, constants, helpers). A
  key no rule names fails the build. A write it cannot read is declared in
  `DYNAMIC_WRITES` with the keys it writes. The stale-rule check catches
  typos.
- **Keys:** a held key is kept, even when the backup's differs. A missing key
  comes back only when its reference opens in this vault; otherwise it is
  listed to enter again. Named lists (providers, MCP servers, `email.accounts`
  by `alias`) and mappings merge: entries and fields added since stay. A mail
  account comes back only while the vault holds its sign-in. Nothing is
  deleted.
- **Preview, then exactly that plan.** `restore(dry_run=True)` runs inside
  `read_only_diagnostic`, so any write raises. An apply carries the preview's
  `digest`. A plan that changed since raises `PlanChanged` (409 + the new
  plan) and writes nothing. The undo record (`system.settings.restore_undo`)
  stores each written key's raw before and after. Undo reverts only keys still
  holding what the restore wrote, and keeps rows holding keys.
- **One check for every way a value is written**
  (`kazma_core/settings_validation.py`): the single and batch saves, a
  restore, `config_save` and the TUI (which falls back to a local write only
  when no server answers; `local_api.LocalApiRefused` means the server
  refused). A new validated key goes in `_rules()`. The agent cannot write a
  key that is not a setting: `safety.protected_config` asks `classify`, on
  the key without its tenant prefix.
- `/api/settings/import` and `/api/settings/reset` are gone (the import wrote
  raw rows and could not read its own export; the reset's buttons never sent
  its confirmation). `export_yaml()` copies the cached kazma.yaml before
  merging (`yaml_defaults()`). It used to write the rows into the defaults
  `get()` falls back to.

