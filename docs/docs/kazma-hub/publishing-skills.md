---
sidebar_position: 2
---

# Publishing Skills

Share your skills with the Kazma community.

## Prerequisites

- A Kazma Hub account
- A skill with valid `skill_manifest.yaml`
- Tests passing

## Publish flow

### 1. Validate your skill

```bash
kazma hub validate ./my-skill
```

Fix any errors before proceeding.

### 2. Register locally

```bash
kazma hub register ./my-skill
```

### 3. Publish to the hub

```bash
kazma hub publish ./my-skill
```

### 4. Verify publication

```bash
kazma hub search "my-skill"
```

## Versioning

Use semantic versioning:

- **Patch** (1.0.1): Bug fixes
- **Minor** (1.1.0): New features, backward compatible
- **Major** (2.0.0): Breaking changes

## Best practices

1. Include comprehensive tests
2. Document all configuration options
3. Keep dependencies minimal
4. Follow the security guidelines
5. Respond to issues promptly
