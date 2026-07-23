---
id: multi-region
title: Multi-Region & HA
sidebar_label: Multi-Region & HA
description: Multi-Region & HA — production ops
---

# Multi-region / multi-replica deployment

Kazma supports **horizontal replicas** when all shared state lives in Postgres.

## Architecture

```
                  ┌─────────────┐
   Users ────────►│ Load Balancer│  health: GET /health/ready
                  └──────┬──────┘
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
        Kazma-1       Kazma-2       Kazma-N
           │             │             │
           └─────────────┼─────────────┘
                         ▼
              Managed Postgres (HA)
         (settings, sessions, tasks, checkpoints)
```

## Requirements (same in every region / replica)

| Env | Same across replicas? |
|-----|------------------------|
| `KAZMA_DATABASE_URL` | **Yes** (one primary DB cluster) |
| `KAZMA_SECRET` | **Yes** (or OIDC-only) |
| `KAZMA_VAULT_KEY` | **Yes** |
| `KAZMA_PUBLIC_URL` | Public HTTPS URL (for OIDC) |
| Local `kazma-data` SQLite | **Do not share** across replicas |

## Local multi-replica demo

```bash
# .env must set KAZMA_SECRET and KAZMA_VAULT_KEY
docker compose -f docker-compose.ha.yml --profile nginx up -d --build --scale kazma=2
curl -s http://127.0.0.1:9090/health/ready | jq .
```

## Multi-region (cloud)

1. Deploy **one** primary Postgres (multi-AZ) with automated backups + PITR.  
2. Deploy Kazma as a service in each region (or one region + CDN).  
3. Point LB health checks at `/health/ready` (503 = remove from pool).  
4. Use sticky sessions only if you keep ephemeral local state; with Postgres cutover stickiness is optional.  
5. Cross-region active-active with two write DBs is **not** supported — use one write primary (or a multi-primary DB product that you fully operate).

## Failover

| Layer | Action |
|-------|--------|
| App replica dies | LB stops routing after failed `/health/ready` |
| Postgres primary fails | Managed failover (RDS/Cloud SQL); apps reconnect via pool |
| Region loss | Fail over DNS to secondary region apps still pointed at healthy PG |

See also: `SAAS_AND_POSTGRES.md`, `DISASTER_RECOVERY.md`.

