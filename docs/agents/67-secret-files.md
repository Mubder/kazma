<!-- AGENTS.md, piece 67-secret-files. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 67. Secrets in files, copies and masks (2026-11-15)

Found by the defensive check before the independent security review: a
rewritten `.env` lost its owner-only restriction, backups and bundles copied
secret files readable by `Users`, URL passwords under most keys stayed in
plaintext, and maskers missed token shapes Kazma itself holds.

- **One owner-only writer.** `security/owner_only.py` (standard library; the
  guard loads it by path, so there is no second copy) replaces a file's whole
  access list with this account and reads it back;
  `file_access.restrict_to_owner` is True only when `readable_by_others` then
  finds no one (`icacls /inheritance:r /grant:r` kept other accounts'
  explicit entries). `file_access.write_private` is the one raw writer of a
  secret file (made empty, restricted, filled, renamed, checked again); env
  files go through `env_files.set_env_values` / `write_env_text`, copies
  through `copy_private`. Gate: `tests/test_env_files_written_one_way.py`
  (the old writers and a planted one as controls); behaviour:
  `tests/test_secret_files_owner_only.py`.
- **Copies stay private.** Backups and restores restrict every file
  `store_registry.holds_a_secret` names (`restrict_secret_files`); backup
  archives and migration bundles are made by `create_private` or not at all
  (`ExportRefused`). `kazma migrate export` writes to `~/kazma-bundles` by
  default, never inside a git checkout, and says the bundle holds the vault
  key; `kazma-bundle-*.zip` is ignored by git.
- **A URL password goes to the vault under any key**
  (`config_store._secret_vault_name`: `cfg:<key>` when install-scoped, else
  the install-scoped `cfg-url:<key>`, so background readers find it, §38;
  nested values such as `providers.list[].base_url` too; `acquisition.`
  journals are read back raw and excepted). Upper-case `*_KEY` names are
  secrets for vaulting, masking and MCP. Gate:
  `tests/test_url_passwords_vaulted.py`.
- **One table of token shapes** (`security/token_shapes.py`: Slack, Discord,
  Google, Meta, JWTs, Kazma API tokens, Hugging Face, GitLab, AWS...), read by
  every redactor and masker (`errors.redact_secrets`,
  `url_credentials.mask_urls_in_text`, `mask_deep`, settings backups, the
  pg argv, `/config export`); linear patterns. `mask_deep` masks URL
  passwords inside text; `approval_args.SECRET_WHEN_NAMED` hides
  `config_save`'s value when its key is secret, and Bearer tokens are masked
  on every card. Census gate: `tests/test_token_shapes_masked.py` (the
  form maskers that post their value back are declared: a masked token would
  be saved as stars). A phrase (`password is hunter2`, `كلمة المرور هي …`,
  a fullwidth colon, joined `كلمةالمرور`, `رمز المرور`) is masked by the
  value's shape in `errors` and in `mask_urls_in_text`; the patterns are
  literals the regex sweep times (`tests/test_credential_phrases_masked.py`).

