# Kazma Disaster Recovery Runbook

**Version:** 0.6.x / Phase 4.5  
**Audience:** Operators deploying single-node or multi-replica Kazma  
**Related:** `scripts/backup_kazma.py`, `scripts/restore_kazma.py`, `SECURITY.md`

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
| Memory / vectors | `kazma-data/vector*`, `memory.db` | Medium |
| Cron jobs | `kazma-data/cron.db` | Medium |
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

### Steps

1. **Optional:** Stop writes for a clean cut (preferred for large DBs):
   ```powershell
   # stop uvicorn / docker compose stop kazma
   ```
2. Run the backup script from the repo root (or install tree):
   ```powershell
   python scripts/backup_kazma.py --dest D:\backups\kazma
   ```
3. Copy the resulting `kazma-backup-YYYYMMDD….zip` to **offsite** storage (S3, NAS, encrypted drive).
4. Confirm secrets are in the password manager (not only in the zip).

### What the script does

- `PRAGMA wal_checkpoint(TRUNCATE)` on each `*.db` for a more consistent copy  
- Zips `kazma-data/**` + `MANIFEST.json`  
- Does **not** include `.env` unless `--include-env` (avoid by default)

### Automated example (Windows Task Scheduler / cron)

```powershell
cd G:\GitHubRepos\kazma
& .\.venv\Scripts\python.exe scripts\backup_kazma.py --dest D:\backups\kazma
# retain last 14 days
Get-ChildItem D:\backups\kazma\kazma-backup-*.zip |
  Sort-Object LastWriteTime -Descending |
  Select-Object -Skip 14 |
  Remove-Item -Force
```

---

## 3. Restore procedure (single-node)

**RTO target:** < 1 hour for single-node with known secrets.

1. **Stop** all Kazma processes (UI, gateway, workers).
2. Restore secrets to `.env` / environment:
   ```
   KAZMA_SECRET=…
   KAZMA_VAULT_KEY=…
   ```
3. Restore data:
   ```powershell
   python scripts/restore_kazma.py --archive D:\backups\kazma\kazma-backup-….zip --force
   ```
4. Start Kazma.
5. Smoke checks:
   - `GET /health` → 200  
   - Login works  
   - A prior chat session appears  
   - Settings → providers still configured (vault unlocks)  
   - Swarm task history loads  

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

- [ ] Take backup with `backup_kazma.py`  
- [ ] Restore to a **staging** host with `--force`  
- [ ] Confirm chat + settings + vault secrets  
- [ ] Confirm Postgres restore (if used)  
- [ ] Time the restore (update RTO notes)  
- [ ] Rotate one test secret and re-backup  
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
