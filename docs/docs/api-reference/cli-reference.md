---
sidebar_position: 4
---

# CLI Reference

## Core commands

```bash
# Show status
kazma status

# Start the agent server
kazma serve [--config CONFIG]

# Start the interactive wizard
kazma wizard
```

## Hub commands

```bash
# Search for skills
kazma hub search QUERY [--capabilities CAPS] [--tags TAGS] [--author AUTHOR]

# View skill details
kazma hub info SKILL_ID

# Install a skill
kazma hub install SKILL_ID

# Uninstall a skill
kazma hub uninstall SKILL_ID

# List installed skills
kazma hub list

# Register a skill locally
kazma hub register PATH

# Validate a skill
kazma hub validate PATH [--verbose]
```

## Docs commands

```bash
# Build documentation
kazma docs build

# Serve documentation locally
kazma docs serve [--port PORT]
```
