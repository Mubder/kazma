---
sidebar_position: 1
---

# Creating Skills

Skills extend Kazma with tools and workflows. Prefer the **native tools** shape used by built-in skills under `kazma-skills/kazma_skills/native/`.

## Recommended: native tools skill

### Layout

```text
my_skill/
  skill_manifest.yaml   # name, tools: map
  tools.py              # async callables named like the tools map keys
```

### Manifest (`tools:` map)

```yaml
name: my-example-skill
version: 0.1.0
description: Example native skill
author: your-name
license: MIT

tools:
  example_echo:
    description: Echo a message back for testing.
    category: utility
    arabic_name: "مثال"
    prompt_chain:
      - "Echo the user's message"
```

### Implementation (`tools.py`)

```python
from __future__ import annotations

async def example_echo(message: str = "") -> str:
    """Must match the tool name key in the manifest."""
    return f"echo: {message}"
```

### Load path

`NativeSkillLoader` (`kazma_skills/native_loader.py`) discovers each native folder, reads `tools:`, imports `tools.py`, and `register_function`s each tool into `LocalToolRegistry`. Danger tools still hit HITL on execute.

### Ship it

```bash
# For a path skill package:
kazma hub validate ./my_skill
kazma hub sign ./my_skill
kazma hub register ./my_skill

# Built-in natives under kazma-skills are auto-discovered at app start.
```

See real examples: `secret_vault`, `git_github_manager`, `task_scheduler_cron` under `kazma-skills/kazma_skills/native/`.

## Alternate: class entry-point skill

Some hub packages use `entry_point: main:MySkill`. That shape is supported by validation/cert checks, but **native tools** are the pattern used by production built-ins. If you use a class, implement a clear contract and document it — do not assume a single global `execute(context)` framework API beyond what your loader expects.

## Testing

```bash
kazma hub validate ./my_skill
pytest path/to/tests -v
```

## Next steps

- [Skill manifest](./skill-manifest) · [Reference manifest spec](../reference/skill-manifest)  
- [MCP integration](./mcp-integration)  
- [Hub overview](../kazma-hub/overview)  
- [Tools catalog](../reference/tools-catalog)  
