---
sidebar_position: 4
---

# Security Auditing

Kazma Hub performs automated security auditing on all skills.

## What we check

### Code patterns

- `eval()` / `exec()` usage
- `os.system()` / `subprocess` calls
- `__import__` dynamic imports
- Hardcoded secrets (API keys, passwords)

### Permissions

- Permissions must be from the allowed list
- Unusual permission combinations flagged
- Minimal permissions recommended

### Dependencies

- Known CVEs in dependencies
- Outdated packages
- License compatibility

## Security score

Each skill receives a security score (0-100):

| Score | Rating | Action |
|---|---|---|
| 90-100 | Excellent | Auto-approved |
| 70-89 | Good | Standard review |
| 50-69 | Fair | Enhanced review |
| 0-49 | Poor | Rejected |

## Running a security audit

```bash
kazma hub validate ./my-skill
kazma hub validate ./my-skill --verbose
```
