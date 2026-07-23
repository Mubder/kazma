---
id: production-checklist
title: Production Checklist
sidebar_label: Production Checklist
description: Go-live checklist for single-node and multi-user Kazma deployments
---

# Production checklist

Use this before exposing Kazma beyond loopback. Aligns with `docs/audits/REMEDIATION_PLAN_2026-07-21.md` (Phases 0–4 shipped in code).

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
- [ ] `code_exec` Docker force where required (`KAZMA_CODE_EXEC_DOCKER=force`)
- [ ] Shell allowlist + env scrub active
- [ ] Workspace root confinement in production
- [ ] Cron concurrency / stop / stale RUNNING handled

## Multi-user / multi-replica (if applicable)

- [ ] `KAZMA_DATABASE_URL` set; migrate script run
- [ ] Opaque sessions / RBAC admin user created
- [ ] OIDC vars if SSO
- [ ] `KAZMA_PUBLIC_URL` correct for redirects
- [ ] HA compose / LB only if Postgres shared state verified

## Smoke

```powershell
& .venv\Scripts\python.exe scripts\smoke_production.py --base http://127.0.0.1:9090 --secret $env:KAZMA_SECRET
```

## Related ops

- [Postgres & SaaS](postgres-and-saas)  
- [Disaster recovery](disaster-recovery)  
- [Multi-region](multi-region)  
- [OIDC](oidc-setup)  
- [Environment variables](../reference/environment-variables)  
