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
- [ ] Multi-replica: unique `KAZMA_REPLICA_ID` + LB sticky on `kazma-replica` cookie
- [ ] Shell allowlist + env scrub active
- [ ] Workspace root confinement in production
- [ ] Cron concurrency / stop / stale RUNNING handled

## Multi-user / multi-replica (if applicable)

- [ ] `KAZMA_DATABASE_URL` set; migrate script run
- [ ] Opaque sessions / RBAC admin user created
- [ ] OIDC vars if SSO
- [ ] `KAZMA_PUBLIC_URL` correct for redirects
- [ ] HA compose / LB only if Postgres shared state verified

## Document Intelligence (if enabled)

- [ ] `documents.enabled` intentional (default compatibility: enabled, not default-authoritative)
- [ ] Do **not** run multi-replica against a shared document store until metadata is Postgres — check `GET /api/documents/ops/readiness`
- [ ] `documents.capacity.storage_free_floor_bytes` set conservatively (default 512 MiB)
- [ ] Nightly document backup path known (`kazma-data/backups/document-store-*` or equivalent)
- [ ] Migration plan includes `documents.db` + content tree ([Migration](./migration))
- [ ] Optional engines understood (fitz / WeasyPrint / LibreOffice may be CONDITIONAL)
- [ ] Malware: install ClamAV (`clamscan`/`clamdscan` on PATH); consider `documents.security.malware_scan=on` + fail-closed; check readiness `malware.available`
- [ ] Cert smoke: `python scripts/certify_documents.py` exits non-FAIL; record report if promoting canary

Guide: [Document Intelligence](../guide/document-intelligence) · Ops: [Document processing](./document-processing).

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
