---
sidebar_position: 5
---

# Certification

Certification is a **local readiness check** plus an optional **remote submit** to a hub API. It is not a fully hosted “app store review” unless you deploy that API yourself.

## Local check (always available)

```bash
kazma hub validate ./my-skill
kazma hub check-certification ./my-skill
# JSON:
kazma hub check-certification ./my-skill --json
```

`check-certification` reports which requirements pass/fail (manifest valid, security lint score, entry point present, etc.) — implemented in `hub/cli.py`.

## Levels (guidance, not separate CLI flags)

Documentation and badge assets describe Basic / Standard / Premium tiers. **There is no** `kazma hub submit --level standard` flag. Levels are product/guidance labels; local scoring is pass/fail per requirement, not a CLI enum.

| Label | Intent |
|-------|--------|
| Basic | Manifest valid, tools load, basic tests |
| Standard | + security lint pass, docs |
| Premium | + deeper review / benchmarks (operator-defined process) |

## Remote submit (optional)

```bash
kazma hub submit ./my-skill --source-url https://github.com/org/skill
kazma hub status <submission_id>
```

Requires a reachable hub API. If the POST fails, the skill is still usable after local `register`.

## Badges in manifests

Some skills may include certification metadata in YAML. Treat remote `certified_by` claims as trustworthy only when you control the signing/cert pipeline.

## Related

- [Security auditing](../kazma-hub/security-auditing)  
- [Publishing](../kazma-hub/publishing-skills)  
