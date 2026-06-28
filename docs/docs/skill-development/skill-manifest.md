---
sidebar_position: 2
---

# Skill Manifest

The `skill_manifest.yaml` file defines your skill metadata, capabilities, and configuration.

## Full specification

```yaml
# Required fields
name: my-skill                    # kebab-case, lowercase
version: 1.0.0                    # semver format
author: your-name                 # kebab-case, lowercase
description: Short description    # 1-2 sentences

# Optional fields
license: MIT
repository: https://github.com/you/my-skill
keywords:
  - analysis
  - data
capabilities:
  - custom-analysis
  - data-processing
permissions:
  - file_read
  - network_outbound
tags:
  - utility
  - beginner-friendly
entry_point: main:MySkill         # module:ClassName format

# Dependencies
dependencies:
  - name: kazma-core
    version: ">=0.1.0"
  - name: httpx
    version: ">=0.27.0"
    optional: true

# MCP server configuration
mcp_servers:
  - name: my-mcp-server
    type: stdio
    command: ["python", "-m", "my_mcp_server"]
    env:
      API_KEY: "${MY_API_KEY}"

# Skill-specific config
config:
  default_model: openai/gpt-4o-mini
  max_retries: 3
  timeout_seconds: 30
```

## Field reference

| Field | Required | Type | Description |
|---|---|---|---|
| name | Yes | string | Kebab-case skill name |
| version | Yes | string | Semver version |
| author | Yes | string | Author identifier |
| description | Yes | string | Short description |
| license | No | string | SPDX license identifier |
| capabilities | No | list | Capabilities this skill provides |
| permissions | No | list | Required permissions |
| tags | No | list | Discovery tags |
| entry_point | No | string | Module:ClassName entry point |
| dependencies | No | list | Required packages |
| mcp_servers | No | list | MCP server configs |

## Validation

The manifest is validated by `SkillManifest.validate()`:

```python
from kazma_core.hub.manifest_schema import SkillManifest

manifest = SkillManifest.from_dict(data)
result = manifest.validate()

if result.passed:
    print("Manifest is valid")
else:
    for error in result.errors:
        print(f"Error: {error}")
```

Validation checks:
- Name is kebab-case and lowercase
- Version is valid semver
- All required fields present
- Permissions are in the allowed list
- Entry point module exists (if specified)
