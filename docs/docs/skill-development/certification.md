---
sidebar_position: 5
---

# Certification

Kazma Hub certifies skills that meet quality, security, and functionality standards.

## Certification levels

| Level | Badge | Requirements |
|---|---|---|
| Basic | certified-basic | Manifest valid, tests pass |
| Standard | certified-standard | + Security audit passed, documentation |
| Premium | certified-premium | + Performance benchmarks, i18n, advanced security |

## Certification process

### 1. Self-validation

```bash
kazma hub validate ./my-skill
```

### 2. Security audit

```bash
kazma hub validate ./my-skill --security-audit
```

The audit checks:
- No dangerous code patterns (eval, exec, os.system)
- No hardcoded secrets
- Permissions are minimal and justified
- Dependencies have known vulnerabilities

### 3. Submit for certification

```bash
kazma hub submit ./my-skill --level standard
```

### 4. Review

The hub team reviews:
- Code quality and style
- Documentation completeness
- Test coverage
- Security posture
- Performance characteristics

## Certification badges

Skills that pass certification receive a badge in their manifest:

```yaml
certification:
  level: standard
  certified_at: "2026-06-20"
  certified_by: kazma-team
  expires_at: "2027-06-20"
```

## Maintaining certification

- Recertify annually
- Update dependencies regularly
- Respond to security advisories within 48 hours
