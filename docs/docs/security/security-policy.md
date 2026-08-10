---
sidebar_position: 1
---

# Security Policy

**Canonical policy:** repository root
[`SECURITY.md`](https://github.com/Mubder/kazma/blob/main/SECURITY.md).
For how to report issues, see [Vulnerability reporting](./vulnerability-reporting).

## Overview

Kazma is a **single-operator trusted-host agent** by default. Localhost + a
strong `KAZMA_SECRET` + HITL is the recommended daily posture. Public multi-user
SaaS is **not** the default threat model.

## Supported versions

| Version | Support |
| ------- | ------- |
| 0.6.x | Full support |
| 0.5.x | Critical security fixes only |
| &lt; 0.5 | Unsupported |

## Security features (summary)

### HITL & permissions

Danger tools go through HITL gates (graph interrupt, swarm bus, pipeline
checkpoints). Skills declare permissions; MCP tools are classified by risk.
See [Security & Safety](../guide/security-and-safety).

### Secrets

API keys and vault material must never be committed. Prefer `KAZMA_VAULT_KEY`
and ConfigStore vault refs. Never use the historical default
`kazma-local-dev-secret`.

### Audit trail

Privileged actions (skill installs, permission changes, security-relevant
events) are logged when the audit path is enabled. Treat completeness as
deployment-dependent — verify in your environment.

### Skill certification

Skills that pass validation checks may receive a **Kazma-Certified** badge
(manifest, entry point, permissions, MCP types, pattern scan). Certification is
not a formal third-party audit.

## No paid bug bounty

There is **no** cash bounty program at this time. Responsible disclosure only —
see [Vulnerability reporting](./vulnerability-reporting) and `SECURITY.md`.

## Related

- [Hardening guide](./hardening-guide)
- [Document security](./document-security)
- [Configuration → security config files](../guide/configuration#7-security-config-files)
- [`kazma-security.yaml`](https://github.com/Mubder/kazma/blob/main/kazma-security.yaml)
