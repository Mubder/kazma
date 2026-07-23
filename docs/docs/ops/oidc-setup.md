---
id: oidc-setup
title: OIDC IdP Setup
sidebar_label: OIDC IdP Setup
description: OIDC IdP Setup — production ops
---

# OIDC / IdP setup (Okta, Auth0, Entra, Keycloak)

Kazma uses standard OpenID Connect (authorization code + PKCE).

## Kazma env

```bash
KAZMA_PUBLIC_URL=https://kazma.example.com
KAZMA_OIDC_ISSUER=https://YOUR_TENANT.okta.com   # or Auth0/Entra issuer
KAZMA_OIDC_CLIENT_ID=...
KAZMA_OIDC_CLIENT_SECRET=...
# optional overrides
KAZMA_OIDC_REDIRECT_URI=https://kazma.example.com/api/auth/oidc/callback
KAZMA_OIDC_SCOPES=openid profile email
KAZMA_OIDC_ROLE_CLAIM=role          # or groups / custom claim
KAZMA_OIDC_DEFAULT_ROLE=operator    # viewer | operator | admin
```

## IdP app registration

| Setting | Value |
|---------|--------|
| Application type | Web / Confidential |
| Grant | Authorization Code |
| Sign-in redirect URI | `{KAZMA_PUBLIC_URL}/api/auth/oidc/callback` |
| Sign-out (optional) | `{KAZMA_PUBLIC_URL}/login` |
| PKCE | Enabled (S256) |

### Okta

1. Applications → Create App Integration → OIDC → Web Application  
2. Sign-in redirect: `https://your.host/api/auth/oidc/callback`  
3. Assign users/groups  
4. Optional: add claim `role` = `admin` / `operator` / `viewer` in Authorization Server

### Auth0

1. Applications → Regular Web Application  
2. Allowed Callback URLs: `https://your.host/api/auth/oidc/callback`  
3. Issuer: `https://YOUR_DOMAIN.auth0.com/`  
4. Optional Actions: add `role` to ID token

### Azure Entra ID

1. App registration → Web redirect URI  
2. Issuer: `https://login.microsoftonline.com/{tenant}/v2.0`  
3. Expose roles or use App roles mapped into token

## Kazma login UX

- `/login` shows **Continue with SSO** when OIDC is configured (`/api/auth/status` → `oidc: true`)  
- After callback, Kazma mints an **opaque** `kazma-session` with username + role  
- Branding (logo, colors) is configured on the IdP login page — Kazma shows a neutral SSO button

## Role mapping

| IdP claim value | Kazma role |
|-----------------|------------|
| admin, owner, administrator | admin |
| operator, user, member, write | operator |
| viewer, read, guest | viewer |
| (missing) | `KAZMA_OIDC_DEFAULT_ROLE` |

## Test

1. Open `/login` → **Continue with SSO**  
2. Complete IdP login  
3. Land on `/` with cookie set  
4. `GET /api/auth/me` returns `username` + `role`

