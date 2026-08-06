---
id: migration
title: Migration (cross-machine)
sidebar_label: Migration
description: Move a full Kazma installation across machines/OSes with kazma migrate — vault pairing, path translation, Postgres dump/restore
---

# Cross-machine migration (`kazma migrate`)

> Move a full Kazma installation — config, secrets, memory, chat history, snapshots, scheduled jobs, assets — from one machine to another (WSL→Windows, Linux→Mac, server→laptop) **without the silent breakage of a naive copy-paste.**

A naive file copy breaks in three specific ways: the encrypted vault becomes undecryptable (wrong key), embedded file paths point at a dead `/home/user/...`, and Postgres-backed data (chat history, settings, checkpoints) is missed entirely. `kazma migrate` prevents all three.

---

## Quick start

On the **source** machine:

```bash
kazma migrate export --out my-kazma.zip
```

On the **target** machine (after copying `my-kazma.zip` over):

```bash
kazma migrate verify my-kazma.zip
kazma migrate import my-kazma.zip --workspace /path/to/kazma --dry-run   # preview first
kazma migrate import my-kazma.zip --workspace /path/to/kazma             # real import
```

That's it for a SQLite-backed install. For **Postgres** sources, see [§ Postgres migration](#postgres-migration) below.

---

## What the bundle contains

A `.zip` archive with:

| File | Contents |
|------|----------|
| `manifest.json` | Version, source OS/host, per-file sha256, vault-key fingerprint, table counts |
| `meta.env` | `KAZMA_VAULT_KEY` + `KAZMA_PUBLIC_URL` (needed for vault decryption + OAuth) |
| `config.yaml` | Full ConfigStore settings (secrets are `vault://` refs, not plaintext) |
| `data/vault.db` | Encrypted secrets store (29+ secrets) — travels WITH the vault key |
| `data/*.db` | All SQLite databases (snapshots, memory, cron, chat, checkpoints, etc.) |
| `data/postgres.dump` | Postgres dump (only when source is Postgres-backed) |
| `data/workspaces.db` | Workspace table (root paths rewritten on import) |
| `assets/` | Binary artifacts: attachments, documents, exports, images, fonts |
| `pathmap.json` | Source workspace root + data dir (for path translation) |

---

## The three invariants

These are the silent-breakage modes `kazma migrate` prevents:

### A. Vault pairing — `vault.db` + `KAZMA_VAULT_KEY` travel together

The vault's encryption salt lives *inside* `vault.db`, so the DB is undecryptable without its matching key. The bundle carries both. On import:

- **Keys match** → vault.db is installed, secrets decrypt. ✅
- **Target key is empty** → the bundle's key is written to the target `.env`. ✅
- **Keys differ** → import **aborts** unless you pass `--reset-vault-key` (which backs up the target's existing vault.db first, then writes the bundle's key).

```bash
kazma migrate import my-kazma.zip --workspace /path --reset-vault-key
```

### B. Path translation — embedded paths rewritten automatically

A source install at `/home/user/kazma` has that path baked into `workspaces.root_path`, `snapshots.state_json` (full SupervisorState blobs), chat messages, memory episodes, and cron prompts. The importer rewrites them all to the target path, across OS separator conventions.

```bash
# Linux source → Windows target
kazma migrate import my-kazma.zip --workspace "C:\Users\me\kazma"
# All /home/user/kazma references → C:\Users\me\kazma
```

The rewrite is byte-level substring (not a JSON parse) so it handles the 300+ MB `snapshots.db` efficiently. Path-prefix ordering prevents partial rewrites (`/home/u/kazma` won't corrupt `/home/u/kazma-repos/ShipX`).

### C. Atomic import — staging → backup → swap

Import never touches live data mid-flight:

1. **Stage** — extract the bundle to `kazma-data/.migrate-staging-<ts>/`
2. **Path-rewrite** the staged copies
3. **Backup** live DBs to `kazma-data/.migrate-backup-<ts>/`
4. **Swap** staging → live (WAL-safe, one file at a time)

A failure before the swap leaves live data untouched. The staging dir is preserved on failure for inspection. To roll back: copy the `.db` files from the backup dir back over the live ones.

---

## Postgres migration

When the source is Postgres-backed, the bundle also includes a `data/postgres.dump` produced by `pg_dump -Fc` (custom format — handles `bytea` blobs natively, ~7× smaller than plain text).

### Discovery — `pg_dump` / `pg_restore`

The migration engine finds the binaries automatically:

1. `pg_dump` / `pg_restore` on `PATH`
2. `docker exec ${KAZMA_DB_CONTAINER:-kazma-db} <bin>` — the common Docker-deployment default
3. Clear error with install hint if neither is available

Override the container name:

```bash
KAZMA_DB_CONTAINER=my-postgres kazma migrate export --out my-kazma.zip
```

### Import into a Postgres target

The target must have `KAZMA_DB_BACKEND=postgres` + `KAZMA_DATABASE_URL` set:

```bash
export KAZMA_DB_BACKEND=postgres
export KAZMA_DATABASE_URL=postgresql://kazma:kazma_change_me@127.0.0.1:5433/kazma
export KAZMA_DB_CONTAINER=kazma-db-win
kazma migrate import my-kazma.zip --workspace /path/to/kazma --reset-vault-key
```

`pg_restore --clean --if-exists` recreates the schema (target DB can be empty), then loads data. The SQLite files (vault, memory, snapshots) are restored alongside.

:::note
A Postgres-backed bundle imported into a **SQLite** target **aborts with a clear error** rather than silently producing a half-migrated install (the Postgres tables — chat history, settings, checkpoints — cannot live in SQLite). Set `KAZMA_DB_BACKEND=postgres` on the target to use the migrated Postgres data.
:::

### Docker-internal port

When `pg_dump` / `pg_restore` run via `docker exec`, they execute **inside** the container, where Postgres listens on `localhost:5432` — not the host's forwarded port (e.g. `5433`). The engine detects the Docker case and overrides automatically. Override the internal port if your container listens elsewhere:

```bash
KAZMA_DB_INTERNAL_PORT=5432  # default; change only if your container differs
```

---

## Commands reference

### `kazma migrate export`

```bash
kazma migrate export [--out PATH] [--no-assets]
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--out PATH` | `kazma-bundle-<timestamp>.zip` | Output bundle path |
| `--no-assets` | (assets included) | Skip binary assets (smaller bundle for config+data only) |

### `kazma migrate verify`

```bash
kazma migrate verify BUNDLE [--no-hash]
```

Checks bundle integrity: structure, manifest compatibility, per-file sha256, vault-key fingerprint, table row counts. `--no-hash` skips the (slow) hash re-hash for a quick structural check.

### `kazma migrate import`

```bash
kazma migrate import BUNDLE [--workspace PATH] [--reset-vault-key] [--dry-run]
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--workspace PATH` | current directory | Target workspace root (paths are rewritten to this) |
| `--reset-vault-key` | (abort on mismatch) | Overwrite target's vault key with the bundle's (backs up existing vault.db first) |
| `--dry-run` | (real import) | Verify + plan only; no writes |

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_DB_CONTAINER` | `kazma-db` | Docker container name for `pg_dump` / `pg_restore` discovery |
| `KAZMA_DB_INTERNAL_PORT` | `5432` | Container-internal Postgres port (when running via `docker exec`) |

See also: [Environment variables](../reference/environment-variables) · [Portability](portability) · [Disaster recovery](disaster-recovery)

---

## Rollback

Every import creates a pre-import backup at `kazma-data/.migrate-backup-<ts>/`. To roll back:

```bash
# Stop Kazma, then copy the backup .db files back over the live ones
cp kazma-data/.migrate-backup-<ts>/*.db kazma-data/
```

For Postgres, `pg_restore --clean --if-exists` is idempotent — re-running the import restores from the bundle cleanly.
