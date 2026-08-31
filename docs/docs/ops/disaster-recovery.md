---
id: disaster-recovery
title: Disaster Recovery
sidebar_label: Disaster Recovery
description: Disaster Recovery — production ops
---

# Kazma Disaster Recovery Runbook

**Version:** 0.6.x / Phase 4.5  
**Audience:** Operators deploying single-node or multi-replica Kazma  
**Related:** `kazma_core.backup.restore`, `kazma_core.backup.restore_drill`, `SECURITY.md`

---

## 1. What to protect

| Asset | Location | Criticality |
|-------|----------|-------------|
| Settings + secrets pointers | `kazma-data/settings.db` (+ vault) | **Critical** |
| Vault encryption key | `KAZMA_VAULT_KEY` env / `.env` | **Critical** — without it secrets are unreadable |
| Shared operator secret | `KAZMA_SECRET` | **Critical** |
| LangGraph checkpoints | `kazma-data/checkpoints.db` | High — conversation continuity |
| Chat sessions | `kazma-data/chat_sessions.db` (or configured path) | High |
| Swarm tasks | `kazma-data/swarm_tasks.db` | Medium–High |
| Memory / vectors / graph | `kazma-data/vector_memory/`, `memory.db`, `vector.db`, `knowledge_graph.db` | Medium |
| Cron jobs | `kazma-data/cron.db` | Medium |
| Document Intelligence metadata | `{documents.storage_root}/documents.db` (or Postgres when metadata backend is PG) | High — library + jobs |
| Document content-addressed blobs | `{documents.storage_root}/` (`quarantine`/`originals`/`artifacts` + manifests) | **Critical** — irrecoverable without backup |
| Graph memory | Neo4j (`bolt://…`) — Docker volume, NOT in the data dir | Medium–High |
| MCP + connector config | `kazma.yaml` at the install root, NOT in the data dir | **Critical** — a restore without it boots with no tools |
| Opaque web sessions | ConfigStore / Postgres | Low (users re-login) |

**Out of band (never only on the app disk):**

- `KAZMA_SECRET`
- `KAZMA_VAULT_KEY`
- Provider API keys if not vaulted
- OIDC client secret
- Postgres credentials (`KAZMA_DATABASE_URL`)

---

## 2. Backup procedure (single-node SQLite)

### Frequency

| Environment | RPO target | Action |
|-------------|------------|--------|
| Lab / personal | Best effort | Daily or before upgrades |
| Production single-node | ≤ 24h | Daily automated zip + offsite copy |
| Production multi-replica | ≤ 1h | Continuous Postgres backups + nightly app snapshot |

### How it works now (restic, since 2026-08-29)

Backups are **automatic**. Nothing needs running by hand.

Each cycle produces a *staging generation* under
`kazma-data/backups/universal/<epoch>/` containing every SQLite database
(WAL-safe via the Online Backup API), assets, `.env`, `kazma.yaml`,
`research/`, and a JSONL export of the Neo4j graph. Postgres is dumped
separately to `kazma-data/backups/pg/` with `pg_dump -Fc`.

That generation is then snapshotted into **two independent restic
repositories**:

| Repository | Location | Credential |
|---|---|---|
| Local | `kazma-data/backups/restic` | passphrase at `~/.kazma/restic.pass` |
| Offsite (recommended) | `s3:https://<account>.r2.cloudflarestorage.com/<bucket>` (or B2) | append-only host key (`PutObject`/`GetObject`/`ListBucket` + `DeleteObject` on `locks/*` only) |
| Offsite (legacy) | `rclone:<remote>/restic` | rclone OAuth — **do not use Google Drive / a service account**. Service accounts have no Drive quota; `rclone:` write probes can look healthy while every upload 403s. |

Prefer **S3-native restic** (Cloudflare R2 or Backblaze B2). The host key
must not be able to `restic forget --prune`. Keep a full-access prune key
**off this machine**. `remote_writable()` probes `s3:` with a real SigV4
PUT+DELETE under `locks/` — an `rclone:` remote that can list but cannot
write is no longer treated as healthy.

The two repositories use **different credentials on purpose**: the offsite
path must not fail when a connector token is revoked. Drive+rclone did
exactly that on 2026-08-27, and 29 consecutive backups went local-only
without anyone noticing.

**Operator still to do (not code):** create the R2/B2 bucket and the
append-only host key, set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`,
point `backups.restic.remote` at `s3:…`, `restic init`, then run
`python -m kazma_core.backup.restore_drill` against a restored generation
before retiring Drive. Escrow the restic passphrase off this host.

**Why restic rather than the zip archives** this runbook used to describe:
deduplication, encryption, and a restore that upstream tests harder than we
can. On this install four generations cost **131 MB** where the zip scheme
cost 3.8 GB, and each additional generation adds roughly **2 MB**.

### Retention

Time-based, not count-based: **7 daily, 8 weekly, 12 monthly**, applied
nightly by the `restic_maintenance` task, which also clears stale locks and
runs `restic check`. "Keep the last 30" is thirty days or thirty hours
depending on how often the loop ran — not a recovery guarantee.

On-disk staging keeps only **2** generations; the history lives in restic.

### The passphrase

`~/.kazma/restic.pass` decrypts **every** snapshot, local and offsite. It is
deliberately NOT `KAZMA_SECRET` — the defect being fixed was the vault key
travelling with the data it protects.

> **Keep a copy off this machine.** Encryption moves the single point of
> failure from the archive to the key. Without it, every backup is
> permanently unreadable.

### Verifying without restoring

```powershell
python -m kazma_core.backup.restore_drill
```

Integrity-checks every SQLite file in a scratch copy and parses the Postgres
archive with `pg_restore --list`. Non-zero exit on failure, so it can be
scheduled.

### The legacy scripts

`scripts/backup_kazma.py` and `scripts/restore_kazma.py` still work and
still produce zips. They are no longer the primary path and do not include
the graph export or `kazma.yaml`.

---

## 3. Restore procedure (single-node)

**RTO target:** < 1 hour for single-node with known secrets.
**Rehearsed:** 2026-08-29, from both the local and the offsite repository.

### Step 1 — see what you can restore to

```powershell
python -m kazma_core.backup.restore --list
```

Lists every recoverable generation with its snapshot and the Postgres dump
that accompanies it.

> **Never use `restic restore latest`.** It selects the newest snapshot by
> the time the SNAPSHOT was taken, which is not the newest DATA. Generations
> ingested out of order — a bulk import, a re-upload — carry recent
> timestamps and old content, so `latest` can hand you a backup missing
> `kazma.yaml` and the graph export while looking like a clean success. The
> command above selects by **generation**; use it rather than restic
> directly.

### Step 2 — restore the files

```powershell
python -m kazma_core.backup.restore --target D:resh-kazma
```

Add `--generation <epoch>` to pick a specific point, or `--repo <path>` to
restore from the offsite repository when the machine is gone.

The target must be **empty** — a restore over an existing tree interleaves
two states into one that looks plausible and is neither.

You get an install layout: `.env`, `kazma.yaml`, `kazma-data/`,
`neo4j_graph.jsonl`, and the paired Postgres dump under `pg/`. Every
restored SQLite database is integrity-checked before it reports success.

### Step 3 — load the databases

Not automatic, and deliberately so: these overwrite live data. The exact
commands are printed at the end of step 2 with paths filled in.

```powershell
# Postgres
pg_restore --clean --if-exists -d "$env:KAZMA_DATABASE_URL" "D:resh-kazma\pg\…\pg_shared_….dump"

# Graph memory, into an EMPTY Neo4j (it refuses a populated one)
python -c "from kazma_core.backup.neo4j_backup import restore_graph; print(restore_graph(r'D:resh-kazma
eo4j_graph.jsonl'))"
```

### Step 4 — point Kazma at it and start

Copy `.env`, `kazma.yaml` and `kazma-data/` into the install root, or point
`KAZMA_DATA_DIR` at the restored tree. Then:

- `GET /health/ready` → `ready`, all checks ok
- Login works
- A prior chat session appears
- Settings → providers still configured (the vault unlocks — this proves
  `.env` came back)
- Tools are listed (this proves `kazma.yaml` came back)

### Failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Settings empty / keys missing | Wrong or missing `KAZMA_VAULT_KEY` | Restore vault key from password manager |
| 401 everywhere | Wrong `KAZMA_SECRET` | Restore secret; clear old cookies |
| SQLite “database is locked” | Process still running | Kill uvicorn/python; retry |
| Vector search empty | Vector volume not in backup path | Restore `vector_memory` / Chroma path; re-index if needed |

---

## 4. Multi-replica / Postgres (Phase 4.3)

When `KAZMA_DATABASE_URL=postgresql://…` is set:

| Component | Backend | Notes |
|-----------|---------|-------|
| Shared settings / sessions / platform users schema | Postgres (`kazma_core.db`) | Required for multi-replica consistency |
| Local caches, Chroma, per-node temp | Local disk | Do **not** share SQLite files over NFS |
| Checkpoints | Prefer Postgres checkpointer when configured | See env below |

### Env for multi-replica

```bash
KAZMA_DATABASE_URL=postgresql://kazma:…@db:5432/kazma
KAZMA_DB_BACKEND=postgres
KAZMA_PG_POOL_MIN=1
KAZMA_PG_POOL_MAX=10
KAZMA_PRODUCTION=1
KAZMA_VAULT_KEY=…
KAZMA_SECRET=…          # or IdP-only with multi-user
KAZMA_PUBLIC_URL=https://kazma.example.com
```

Optional packages:

```bash
pip install 'psycopg[binary,pool]>=3.1' 'langgraph-checkpoint-postgres>=2.0'
# or: pip install -e ".[postgres]"
```

### Postgres backup

Use your platform standard:

- **Managed:** enable automated backups + PITR (RDS, Cloud SQL, Azure).  
- **Self-hosted:**
  ```bash
  pg_dump -Fc "$KAZMA_DATABASE_URL" -f kazma-$(date -u +%Y%m%d).dump
  ```
- Restore:
  ```bash
  pg_restore -d "$KAZMA_DATABASE_URL" --clean --if-exists kazma-YYYYMMDD.dump
  ```

**Rule:** Never run multiple replicas against a shared SQLite file.

Compose example: `docker-compose.postgres.yml`.

---

## 5. Multi-user / IdP (Phase 4.4)

| Mode | How |
|------|-----|
| Single operator | `KAZMA_SECRET` + opaque session (default) |
| Local multi-user | Create users via `platform.users` / `create_local_user()`; login with username+password |
| OIDC | `KAZMA_OIDC_ISSUER`, `CLIENT_ID`, `CLIENT_SECRET`, `KAZMA_PUBLIC_URL` → `/api/auth/oidc/start` |

Roles: **viewer** < **operator** < **admin** (see `platform_rbac.py`).

After DR restore, re-test:

1. Admin login  
2. Operator can chat/approve  
3. Viewer cannot hit `/api/settings`  

---

## 6. Drill checklist (run quarterly)

Backups run themselves; the drill exists to prove the RESTORE still works,
which is the half that rots unnoticed.

- [ ] `python -m kazma_core.backup.restore --list` — points exist, newest is recent
- [ ] Restore to an empty staging directory — expect all steps green
- [ ] Confirm every SQLite database passed `integrity_check` (the restore reports it)
- [ ] Restore once from the **offsite** repo, not just local (`--repo rclone:…`)
- [ ] Load the Postgres dump into a throwaway database
- [ ] Load the graph into an empty Neo4j and compare node/relationship counts
- [ ] Confirm the vault unlocks and tools are listed (proves `.env` + `kazma.yaml`)
- [ ] Time it (update the RTO note above)
- [ ] Confirm the restic passphrase is still recoverable from OUTSIDE this machine
- [ ] Document any gaps in this file  

---

## 7. Incident contacts

| Event | Action |
|-------|--------|
| Suspected compromise | Rotate `KAZMA_SECRET`, `KAZMA_VAULT_KEY`, provider keys; invalidate sessions; restore from last known-good backup if needed |
| Data corruption | Stop writers → restore from last good zip/dump → smoke test |
| Lost vault key | **Unrecoverable** for vaulted secrets — restore key from offline store or re-enter secrets |

---

*Maintain this runbook with every production architecture change.*

