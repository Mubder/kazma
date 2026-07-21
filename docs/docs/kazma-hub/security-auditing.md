---
sidebar_position: 4
---

# Security auditing (skills)

Hub validation scores skills for unsafe patterns and weak packaging. Treat this as a **local gate**, not a substitute for production HITL.

## What validation covers

When you run `kazma hub validate ./my-skill`, the wired validator path checks (among other things):

- Manifest shape / required fields  
- Entry point presence (when declared)  
- Security lint signals (dangerous patterns, secrets-like strings)  
- Score threshold for “pass” vs review  

### Patterns typically flagged

- `eval` / `exec` / unrestricted shell  
- Hardcoded secrets  
- Over-broad permissions  

Offline libraries also exist under `kazma_core/security/` (linter, dependency scanner, certification helpers). **Not all of those are auto-run on every install** — they are available for operators and future wiring. See `docs/audits/UNWIRED_INVENTORY.md`.

## Running checks

```bash
kazma hub validate ./my-skill
kazma hub validate ./my-skill --json
kazma hub check-certification ./my-skill
kazma hub sign ./my-skill    # integrity — not the same as a security audit
```

:::note
There is **no** `kazma hub validate --security-audit` flag. Use `validate` and `check-certification`.
:::

## Security score (guidance)

| Score | Guidance |
|-------|----------|
| High (≈90+) | Good candidate for sharing |
| Mid | Review manually before install |
| Low | Do not install in production |

Exact thresholds are enforced in code (`SkillValidator` / certification check). Re-run after changes.

## Runtime safety (more important than hub score)

Even a “clean” skill can register **danger** tools. At runtime:

- Graph HITL / swarm bus / pipeline checkpoints still apply  
- MCP tools may be force-danger under `KAZMA_PRODUCTION`  
- See [Security & Safety](../guide/security-and-safety) and [Tools catalog](../reference/tools-catalog)  

## Related

- [Publishing](./publishing-skills)  
- [Certification](../skill-development/certification)  
