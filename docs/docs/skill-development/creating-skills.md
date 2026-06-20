---
sidebar_position: 1
---

# Creating Skills

Skills are self-contained modules that extend Kazma capabilities.

## Quick start

```bash
# Create a new skill
mkdir my-skill && cd my-skill

# Create manifest
cat > skill_manifest.yaml << 'EOF'
name: my-skill
version: 0.1.0
author: your-name
description: A custom skill for my use case
capabilities:
  - custom-analysis
permissions:
  - file_read
EOF

# Create entry point
cat > main.py << 'EOF'
class MySkill:
    def __init__(self):
        self.name = "my-skill"

    async def execute(self, context):
        return {"result": "Hello from my skill!"}
EOF
```

## Skill structure

```
my-skill/
  skill_manifest.yaml    # Required: metadata and config
  main.py                # Required: skill entry point
  requirements.txt       # Optional: Python dependencies
  README.md              # Optional: documentation
  tests/                 # Optional: skill tests
    test_my_skill.py
```

## Entry point

The entry point must be a class with an `execute` method:

```python
class MySkill:
    def __init__(self, config=None):
        self.config = config or {}

    async def execute(self, context):
        message = context.get("message", "")
        return {"result": f"Processed: {message}"}

    async def cleanup(self):
        pass
```

## Testing your skill

```bash
# Validate the skill
kazma hub validate ./my-skill

# Install locally
kazma hub register ./my-skill

# Test it
kazma hub info kazma-hub://your-name/my-skill@0.1.0
```

## Next steps

- [Skill manifest](./skill-manifest) — full manifest specification
- [MCP integration](./mcp-integration) — connect to MCP servers
- [Testing skills](./testing-skills) — write and run tests
- [Certification](./certification) — get your skill certified
