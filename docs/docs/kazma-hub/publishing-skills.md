---
sidebar_position: 2
---

# Publishing & registering skills

Share skills by registering them locally and, optionally, submitting them to a remote hub API.

:::warning No `publish` command
There is **no** `kazma hub publish` command. Use **`register`** for local install from a path, and **`submit`** only when a hub API is configured and reachable.
:::

## Prerequisites

- A directory with valid `skill_manifest.yaml`  
- Optional: `tools.py` (native tools shape) or entry-point module  
- `KAZMA_SECRET` if you will **sign** the skill  

## Recommended flow (local)

### 1. Validate

```bash
kazma hub validate ./my-skill
# optional JSON:
kazma hub validate ./my-skill --json
```

### 2. Sign (integrity)

```bash
kazma hub sign ./my-skill
# or: kazma hub sign ./my-skill --secret "$KAZMA_SECRET"
```

Writes checksum + HMAC into the manifest (see [Skills, MCP & Tools](../guide/skills-mcp-and-tools)).

### 3. Register locally

```bash
kazma hub register ./my-skill
```

### 4. Verify

```bash
kazma hub search "my-skill"
kazma hub list
kazma hub info <skill-id>
```

## Optional: remote certification submit

If you operate a hub API (`hub_url` / CLI default):

```bash
kazma hub submit ./my-skill --source-url https://github.com/you/my-skill
kazma hub status <submission_id>
```

There is **no** `--level` flag on `submit`. Local readiness:

```bash
kazma hub check-certification ./my-skill
```

## Versioning

Use semantic versioning in the manifest:

- **Patch** (1.0.1): bug fixes  
- **Minor** (1.1.0): backward-compatible features  
- **Major** (2.0.0): breaking changes  

## Best practices

1. Keep tools small and HITL-aware (danger tools must match real capabilities).  
2. Prefer the native `tools:` map used by built-in skills under `kazma-skills/`.  
3. Document env vars and permissions in the skill README.  
4. Run `validate` + `check-certification` before sharing.  
