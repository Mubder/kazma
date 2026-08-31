---
id: production-checklist
title: Production Checklist
sidebar_label: Production Checklist
description: Go-live checklist for single-node and multi-user Kazma deployments
---

# Production checklist

Use this before exposing Kazma beyond loopback. Aligns with `docs/audits/REMEDIATION_PLAN_2026-07-21.md` (Phases 0–4 shipped in code).

**Feature smoke (research / KB / proxy / memory explain):** see [Smoke test matrix](./smoke-matrix) — run after related deploys.

## P0 — Secrets & bind

- [ ] `KAZMA_HOST` is intentional (`127.0.0.1` or proxy-only `0.0.0.0`)
- [ ] `KAZMA_SECRET` is strong, unique, **not** the historical `kazma-local-dev-secret`
- [ ] Non-loopback bind fails closed without secret (CLI / serve)
- [ ] `KAZMA_PRODUCTION=1`
- [ ] `KAZMA_VAULT_KEY` set
- [ ] Healthcheck uses `/health` (or live/ready as documented)

## P0 — Lifecycle & fail-closed

- [ ] Graceful shutdown drains swarm/cron (no orphan tasks)
- [ ] NullBus denies danger tools (no headless auto-approve)
- [ ] YOLO disabled unless `KAZMA_ALLOW_YOLO=1` (avoid in real prod)
- [ ] Circuit breaker half-open probe semantics intact

## P1 — Security depth

- [ ] Auth default-deny on `/api/*`
- [ ] Discovery SSRF protections enabled
- [ ] `code_exec` Docker force where required (`KAZMA_CODE_EXEC_DOCKER=force`; host-local disabled in prod/multi-user)
- [ ] Untrusted / multi-user code: E2B Firecracker (`E2B_API_KEY` + `pip install 'kazma[sandbox]'`; `KAZMA_E2B=0` to disable)
- [ ] Multi-hour swarm: Temporal (`KAZMA_TEMPORAL_HOST` + `pip install 'kazma[durable]'`)
- [ ] Multi-replica: unique `KAZMA_REPLICA_ID` + LB sticky on `kazma-replica` cookie
- [ ] Shell allowlist + env scrub active
- [ ] Workspace root confinement in production
- [ ] Cron concurrency / stop / stale RUNNING handled
- [ ] Multi-operator: platform allowlists set + `KAZMA_GATEWAY_STRICT_ALLOWLIST=1` (2026-08-19; adapters otherwise run allow-all for backward compat)
- [ ] `KAZMA_HITL_CANONICAL_FLOOR=1` on strict deployments (danger list cannot narrow below canonical; 2026-08-19)
- [ ] Offsite/cloud-sync backups verify TLS (WebDAV default ON since 2026-08-19; `backups.offsite.webdav.tls_verify=false` only for self-signed labs)

## Multi-user / multi-replica (if applicable)

- [ ] `KAZMA_DATABASE_URL` set; migrate script run
- [ ] Opaque sessions / RBAC admin user created
- [ ] OIDC vars if SSO
- [ ] `KAZMA_PUBLIC_URL` correct for redirects
- [ ] HA compose / LB only if Postgres shared state verified
- [ ] Memory: `KAZMA_MEMORY_ENFORCE_TENANT=1` when more than one tenant exists
- [ ] Before `KAZMA_MEMORY_STATE_ROLE=primary`: `python scripts/reconcile_memory_mirror.py --dry-run` is clean (no dead-in-mirror rows)

## Document Intelligence (if enabled)

- [ ] `documents.enabled` intentional (default compatibility: enabled, not default-authoritative)
- [ ] Do **not** run multi-replica against a shared document store until metadata is Postgres — check `GET /api/documents/ops/readiness`
- [ ] `documents.capacity.storage_free_floor_bytes` set conservatively (default 512 MiB)
- [ ] Nightly document backup path known (`kazma-data/backups/document-store-*` or equivalent)
- [ ] Migration plan includes `documents.db` + content tree ([Migration](./migration))
- [ ] Optional engines understood (fitz / WeasyPrint / LibreOffice may be CONDITIONAL)
- [ ] PDF Arabic / electronic text: `pip install -e ".[document-platform]"` (PyMuPDF + pypdfium2); parser readiness should be **ready**, not text-only degraded
- [ ] Scanned Arabic PDFs: system Tesseract + `ara` (and `eng`) traineddata on PATH
- [ ] Malware: install ClamAV (`clamscan`/`clamdscan` on PATH); consider `documents.security.malware_scan=on` + fail-closed; check readiness `malware.available`
- [ ] Cert smoke: `python scripts/certify_documents.py` exits non-FAIL; record report if promoting canary

Guide: [Document Intelligence](../guide/document-intelligence) · Ops: [Document processing](./document-processing).

## Upgrades (git install)

- [ ] Operators use **`kazma update`** (not ad-hoc `git pull`) — see [Kazma Update](./kazma-update)
- [ ] After update: `kazma --version` / HEAD matches `origin/main`; `import kazma_cli` works; `kazma serve` starts
- [ ] Optional extras still present (rag / document-platform); use `kazma update --reinstall -y` if wiped

## Smoke

```powershell
& .venv\Scripts\python.exe scripts\smoke_production.py --base http://127.0.0.1:9090 --secret $env:KAZMA_SECRET
# Document platform (optional but recommended when documents.enabled):
& .venv\Scripts\python.exe scripts\certify_documents.py
```

Also run document rows in the [Smoke matrix](./smoke-matrix).

## Related ops

- [Postgres & SaaS](postgres-and-saas)  
- [Multi-replica & SaaS residual](multi-replica-and-saas)  
- [Disaster recovery](disaster-recovery)  
- [Multi-region](multi-region)  
- [OIDC](oidc-setup)  
- [Document processing](document-processing)  
- [Environment variables](../reference/environment-variables)  

## Commitment Layer

- [ ] **Kill-switch verified**: `KAZMA_COMMITMENT_ENABLED=0` disables the gate (fail-open)
- [ ] **GC cadence running**: `_start_commitment_gc_scheduler` every 15 min (check logs for `commitment GC:` summary)
- [ ] **Flags**: `swarm_scope_enforce` defaults **ON** (workers capped at HIGH since 2026-08-15); `enforce_unknown_mutators` defaults **ON**; `soul_requires_confirm` defaults OFF on a lab and auto-ON in production / multi-user — operators toggle via ConfigStore / env
- [ ] **Metrics endpoint**: `GET /metrics` shows `kazma_commitment_decisions_total{decision=...}` + `kazma_commitment_pending`
- [ ] **Soul confirm queue**: `GET /api/commitment/soul/pending` lists held deltas; `POST /api/commitment/soul/{cid}/confirm` approves
