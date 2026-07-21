---
id: multi-user-saas
title: Multi-user & SaaS
sidebar_label: Multi-user SaaS
description: Platform RBAC, opaque sessions, OIDC, tenants, and Postgres cutover
---

# Multi-user & SaaS foundation

Kazma ships a **multi-user foundation** (not a full multi-tenant cloud product): local users, roles, opaque sessions, optional OIDC, and shared Postgres state for multi-replica.

## Auth modes (login)

| Mode | Use |
|------|-----|
| Shared secret | Single-operator / automation |
| Local user + password | Multi-user without IdP |
| OIDC PKCE | SSO |

## Roles (platform RBAC)

Typical roles: **viewer** · **operator** · **admin**  
Enforced on SaaS and sensitive APIs (`platform_rbac.py`). Tenant spoofing via headers is ignored in production — JWT/session principal wins.

## Data stores for multi-replica

When `KAZMA_DATABASE_URL` is set:

- ConfigStore / settings / platform users  
- Chat sessions  
- Swarm tasks + metrics  
- LangGraph checkpoints (`AsyncPostgresSaver`)  

SQLite remains default for local single-node. See [Postgres & SaaS](../ops/postgres-and-saas).

## Create an admin (bootstrap)

```python
from kazma_core.security.platform_rbac import create_local_user
create_local_user("admin", "long-password-here", role="admin")
```

## UI

- `/login` — User · Secret · SSO tabs  
- Settings → Account — users / tenants (admin)  
- Header — role badge + logout  

## Env checklist

```bash
KAZMA_DATABASE_URL=postgresql://…
KAZMA_PRODUCTION=1
KAZMA_SECRET=…
KAZMA_VAULT_KEY=…
KAZMA_PUBLIC_URL=https://…
KAZMA_OIDC_ISSUER=…          # optional
KAZMA_OIDC_CLIENT_ID=…
KAZMA_OIDC_CLIENT_SECRET=…
```

## Related

- [OIDC setup](../ops/oidc-setup)  
- [Environment variables](../reference/environment-variables)  
- [Production checklist](../ops/production-checklist)  
