# Kazma Skill Manifest Specification

> Version: 1.0.0 | Status: Active | Last updated: 2026-06-20

This document defines the formal specification for Kazma skill manifests (`skill_manifest.yaml`). Every skill installed in Kazma MUST include a valid manifest file at its root directory.

---

## Table of Contents

- [Overview](#overview)
- [YAML Schema](#yaml-schema)
- [Required Fields](#required-fields)
- [Optional Fields](#optional-fields)
- [Field Reference](#field-reference)
- [Permission Model](#permission-model)
- [MCP Server Configuration](#mcp-server-configuration)
- [Versioning Rules](#versioning-rules)
- [Validation Rules](#validation-rules)
- [Security Scoring](#security-scoring)
- [Examples](#examples)

---

## Overview

A skill manifest is a YAML file (`skill_manifest.yaml`) located at the root of a skill directory. It declares the skill's identity, capabilities, dependencies, permissions, and runtime configuration.

### Manifest Location

```
my-skill/
├── skill_manifest.yaml    # This file
├── main.py                # Entry point (optional)
└── ...
```

The validator looks for `skill_manifest.yaml` specifically (not `manifest.yaml` or `manifest.yml`).

---

## YAML Schema

```yaml
# ─── Required Fields ────────────────────────────────────────────────
name: string               # kebab-case identifier
version: string            # semver X.Y.Z
description: string        # human-readable description
author: string             # author name or organization
license: string            # SPDX license identifier

# ─── Optional Fields ────────────────────────────────────────────────
capabilities: [string]     # list of capability tags
dependencies:              # dependency constraints
  core: string             #   minimum core version (semver range)
  optional: [string]       #   optional Python package names
mcp_servers:               # MCP server configurations
  - name: string           #   server identifier
    type: string           #   transport type (stdio|sse|streamable-http)
    command: [string]      #   command to start server (for stdio)
    url: string            #   server URL (for sse/streamable-http)
    env: {string: string}  #   environment variables
permissions:               # permission declarations
  required: [string]       #   permissions needed for core functionality
  optional: [string]       #   permissions for enhanced features
entry_point: string        # dotted module path or file name (without .py)
config_schema: object      # JSON Schema for skill configuration
min_core_version: string   # minimum Kazma core version (semver)
tags: [string]             # searchable tags
homepage: string           # project homepage URL
repository: string         # source repository URL
```

---

## Required Fields

All five required fields MUST be present. Validation fails if any are missing.

### `name`

- **Type**: `string`
- **Pattern**: `^[a-z][a-z0-9-]*$` (kebab-case)
- **Description**: Unique identifier for the skill. Must start with a lowercase letter, contain only lowercase letters, digits, and hyphens.
- **Examples**: `drone-inspector`, `oil-pricing-v2`, `arabic-ocr`

**Validation errors**:
- Missing field → `"Missing required field: name"`
- Invalid format → `"Name must be kebab-case, got: 'MySkill'"`

### `version`

- **Type**: `string`
- **Pattern**: `^\d+\.\d+\.\d+$` (simple semver)
- **Description**: Semantic version of the skill.
- **Examples**: `1.0.0`, `0.1.0`, `2.3.1`

**Validation errors**:
- Missing field → `"Missing required field: version"`
- Invalid format → `"Version must be valid semver (X.Y.Z), got: 'v1.0.0'"`

**Note**: Pre-release suffixes (`1.0.0-beta`) and build metadata (`1.0.0+build`) are NOT supported. Use simple `X.Y.Z` only.

### `description`

- **Type**: `string`
- **Description**: A brief, human-readable description of the skill's purpose.
- **Examples**: `"Read files from the filesystem"`, `"Arabic OCR with RTL layout support"`

### `author`

- **Type**: `string`
- **Description**: Name of the skill author or organization.
- **Examples**: `"ALMuhalab International Holding Group"`, `"Jane Doe"`

### `license`

- **Type**: `string`
- **Description**: SPDX license identifier.
- **Examples**: `MIT`, `Apache-2.0`, `GPL-3.0-only`

**Validation errors**:
- Empty or whitespace-only → `"License must be a non-empty string"`

---

## Optional Fields

These fields are not required but enhance skill functionality and discoverability.

### `capabilities`

- **Type**: `list[string]`
- **Description**: Tags describing what the skill can do. Used for conflict detection when two skills share capabilities.
- **Examples**: `["drone_inspection", "trading_intelligence"]`, `["audio", "video"]`

### `dependencies`

- **Type**: `object`
- **Description**: Python package dependencies.
- **Sub-fields**:
  - `core` (string): Minimum Kazma core version required (e.g., `">=0.1.0"`)
  - `optional` (list[string]): Optional Python packages the skill uses

```yaml
dependencies:
  core: ">=0.1.0"
  optional:
    - numpy
    - paho-mqtt
    - opencv-python
```

### `mcp_servers`

- **Type**: `list[object]`
- **Description**: MCP (Model Context Protocol) servers this skill requires.
- **Each entry must have**: `name` (string) and `type` (string)
- **See**: [MCP Server Configuration](#mcp-server-configuration)

### `permissions`

- **Type**: `object` or `list[string]`
- **Description**: Permissions the skill requires. Can be a simple list or structured with `required`/`optional` sub-lists.
- **See**: [Permission Model](#permission-model)

```yaml
# Simple form
permissions:
  - file_read
  - network_outbound

# Structured form
permissions:
  required:
    - file_read
  optional:
    - camera_access
```

### `entry_point`

- **Type**: `string`
- **Description**: The Python module path or file name (without `.py` extension) that serves as the skill's entry point.
- **Examples**: `"main"`, `"my_skill.main:run"`, `"src.plugin"`

**Warnings**:
- Relative paths (containing `/` or starting with `.`) generate a warning: use dotted module paths instead.

### `config_schema`

- **Type**: `object`
- **Description**: JSON Schema defining the skill's configuration options. Used by the UI to generate configuration forms.

```yaml
config_schema:
  type: object
  properties:
    api_key:
      type: string
      description: "API key for external service"
    max_retries:
      type: integer
      default: 3
  required:
    - api_key
```

### `min_core_version`

- **Type**: `string`
- **Pattern**: `^\d+\.\d+\.\d+$` (semver)
- **Description**: Minimum Kazma core version required to run this skill. If the installed core version is lower, the skill will not be loaded.
- **Examples**: `"0.5.0"`, `"1.0.0"`

### `tags`

- **Type**: `list[string]`
- **Description**: Searchable tags for skill discovery in the hub.
- **Examples**: `["testing", "example"]`, `["data", "oil-gas"]`

### `homepage`

- **Type**: `string` (URL)
- **Description**: Project homepage URL.
- **Example**: `"https://example.com/my-skill"`

### `repository`

- **Type**: `string` (URL)
- **Description**: Source code repository URL.
- **Example**: `"https://github.com/example/my-skill"`

---

## Field Reference

| Field               | Required | Type              | Default | Description                     |
|---------------------|----------|-------------------|---------|---------------------------------|
| `name`              | Yes      | string (kebab)    | —       | Unique skill identifier         |
| `version`           | Yes      | string (semver)   | —       | Semantic version                |
| `description`       | Yes      | string            | —       | Human-readable description      |
| `author`            | Yes      | string            | —       | Author or organization          |
| `license`           | Yes      | string (SPDX)     | —       | License identifier              |
| `capabilities`      | No       | list[string]      | `[]`    | Capability tags                 |
| `dependencies`      | No       | object            | `{}`    | Python package dependencies     |
| `mcp_servers`       | No       | list[object]      | `[]`    | MCP server configurations       |
| `permissions`       | No       | object/list       | `[]`    | Permission declarations         |
| `entry_point`       | No       | string            | None    | Module entry point              |
| `config_schema`     | No       | object            | None    | JSON Schema for config          |
| `min_core_version`  | No       | string (semver)   | None    | Minimum core version            |
| `tags`              | No       | list[string]      | `[]`    | Searchable tags                 |
| `homepage`          | No       | string (URL)      | None    | Project homepage                |
| `repository`        | No       | string (URL)      | None    | Source repository               |

---

## Permission Model

Permissions declare what system resources the skill needs access to. Kazma validates permissions against an allowlist and scores unknown permissions.

### Allowed Permission Values

| Permission         | Description                              |
|--------------------|------------------------------------------|
| `file_read`        | Read files from the filesystem           |
| `file_write`       | Write/create files on the filesystem     |
| `network_outbound` | Make outbound network requests           |
| `network_inbound`  | Accept inbound network connections       |
| `camera_access`    | Access device camera                     |
| `mqtt_broker`      | Connect to MQTT brokers                  |
| `database_read`    | Read from databases                      |
| `database_write`   | Write to databases                       |

### Permission Declaration Formats

**Simple list** (all required):

```yaml
permissions:
  - file_read
  - network_outbound
```

**Structured** (required + optional):

```yaml
permissions:
  required:
    - file_read
    - network_outbound
  optional:
    - camera_access
    - mqtt_broker
```

### Validation

- Each permission is checked against the allowlist
- Unknown permissions generate a **warning** and deduct -5 from the security score
- Permissions do not cause validation failures (they are advisory)

---

## MCP Server Configuration

MCP servers provide external tool capabilities to skills. Each server entry requires `name` and `type`.

### Required Fields

| Field   | Type   | Description                      |
|---------|--------|----------------------------------|
| `name`  | string | Server identifier (unique)       |
| `type`  | string | Transport type (see below)       |

### Transport Types

| Type               | Description                          | Additional Fields       |
|--------------------|--------------------------------------|-------------------------|
| `stdio`            | Local process via stdin/stdout       | `command`, `env`        |
| `sse`              | Server-Sent Events HTTP endpoint     | `url`                   |
| `streamable-http`  | Streamable HTTP endpoint             | `url`                   |

### Example: stdio Server

```yaml
mcp_servers:
  - name: oil-pricing-api
    type: stdio
    command: ["python", "-m", "oil_pricing_server"]
    env:
      API_KEY: "${OIL_API_KEY}"
```

### Example: SSE Server

```yaml
mcp_servers:
  - name: remote-analytics
    type: sse
    url: "https://analytics.example.com/mcp"
```

### Validation

- Missing `name` → error
- Missing `type` → error
- Invalid `type` → error (must be `stdio`, `sse`, or `streamable-http`)
- Non-list `mcp_servers` → error

---

## Versioning Rules

Kazma uses strict semver (`X.Y.Z`) for all version fields.

### Format

```
MAJOR.MINOR.PATCH
```

- **MAJOR** (`X`): Breaking changes
- **MINOR** (`Y`): New features, backward-compatible
- **PATCH** (`Z`): Bug fixes, backward-compatible

### Rules

1. All three parts MUST be present: `1.0` is invalid, `1.0.0` is valid
2. Parts MUST be non-negative integers: `1.0.0` is valid, `-1.0.0` is not
3. No pre-release suffixes: `1.0.0-beta` is invalid
4. No build metadata: `1.0.0+build123` is invalid
5. No `v` prefix: `v1.0.0` is invalid

### Compatibility Checking

The `min_core_version` field uses semver comparison:

```python
# Pseudo-code
skill_version >= min_core_version
```

Example: A skill with `min_core_version: "0.5.0"` requires core version 0.5.0 or higher.

### Conflict Detection

When installing a new skill:
- **Same name**: Replacement (with warning)
- **Same capabilities**: Warning (potential conflict)
- **Cross-version**: Compatibility check via `min_core_version`

---

## Validation Rules

Validation is performed by `SkillValidator` (in `kazma-core/kazma_core/hub/validator.py`) which runs five checks:

### Check 1: Manifest Exists and Is Valid YAML

- `skill_manifest.yaml` must exist in the skill root
- Must be a valid YAML mapping (not a list or scalar)
- **Penalty**: -30 points if missing or invalid

### Check 2: Entry Point Verification

- If `entry_point` is declared, the corresponding `.py` file must exist
- Example: `entry_point: main` requires `main.py`
- **Penalty**: -10 points if missing

### Check 3: Permission Validation

- Each permission is checked against the allowlist
- Unknown permissions generate warnings
- **Penalty**: -5 points per unknown permission

### Check 4: MCP Server Validation

- Each server must have `name` and `type`
- `type` must be one of: `stdio`, `sse`, `streamable-http`
- **Penalty**: validation failure (errors list)

### Check 5: Security Scan

All `.py` files in the skill directory are scanned for dangerous patterns:

| Pattern           | Detection         | Penalty |
|-------------------|-------------------|---------|
| `eval()`          | `eval\s*\(`       | -20     |
| `exec()`          | `exec\s*\(`       | -20     |
| `__import__`      | `\b__import__\b`  | -15     |
| `os.system()`     | `os\.system\s*\(` | -25     |
| Hardcoded secrets | Various patterns  | -10/file|

**Secret patterns detected**:
- `api_key = "..."` or `api_secret = "..."`
- `password = "..."` or `passwd = "..."`
- `secret = "..."` or `secret_key = "..."`
- `token = "..."` or `access_token = "..."`

### Score Calculation

- Base score: **100**
- Each check returns a delta (0 or negative)
- Final score: `max(0, min(100, 100 + sum(deltas)))`
- Validation passes only if there are **zero errors**
- Warnings are advisory (do not block installation)

---

## Security Scoring

The security score (0-100) reflects the skill's safety profile:

| Score Range | Rating      | Meaning                                    |
|-------------|-------------|--------------------------------------------|
| 90-100      | Excellent   | No issues, safe to install                 |
| 70-89       | Good        | Minor warnings, generally safe             |
| 50-69       | Caution     | Several issues, review before installing   |
| 0-49        | Risky       | Significant security concerns              |

### Kazma-Certified Badge

A skill receives the **Kazma-Certified** badge when:
1. Validation passes (zero errors)
2. Security score >= 90
3. All required fields are present
4. MCP servers use valid types
5. No hardcoded secrets detected

---

## Examples

### Minimal Manifest

```yaml
name: hello-world
version: 1.0.0
description: "A simple hello world skill"
author: "Example Author"
license: MIT
```

### Full-Featured Manifest

```yaml
name: drone-inspection
version: 2.1.0
description: "AI-powered drone inspection with YOLO detection and telemetry"
author: "ALMuhalab International Holding Group"
license: Apache-2.0

capabilities:
  - drone_inspection
  - computer_vision
  - telemetry_analysis

dependencies:
  core: ">=0.5.0"
  optional:
    - numpy
    - opencv-python
    - paho-mqtt

mcp_servers:
  - name: oil-pricing-api
    type: stdio
    command: ["python", "-m", "oil_pricing_server"]
  - name: mqtt-broker
    type: sse
    url: "mqtt://broker.local:1883"

permissions:
  required:
    - file_read
    - file_write
    - network_outbound
    - mqtt_broker
  optional:
    - camera_access

entry_point: main
config_schema:
  type: object
  properties:
    broker_url:
      type: string
      default: "mqtt://localhost:1883"
    yolo_model:
      type: string
      default: "yolov11"
  required:
    - broker_url

min_core_version: "0.5.0"
tags:
  - drone
  - inspection
  - oil-gas
  - computer-vision

homepage: "https://example.com/drone-inspection"
repository: "https://github.com/example/drone-inspection"
```

### Enterprise Skill with Division Permissions

```yaml
name: trading-intelligence
version: 1.0.0
description: "Market data analysis and trading intelligence for general trading division"
author: "ALMuhalab International Holding Group"
license: MIT

capabilities:
  - trading_intelligence
  - market_analysis

dependencies:
  core: ">=0.1.0"
  optional:
    - pandas
    - requests

mcp_servers:
  - name: market-data-api
    type: stdio
    command: ["python", "-m", "market_data_server"]
  - name: news-aggregator
    type: sse
    url: "https://news-api.example.com/mcp"

permissions:
  required:
    - network_outbound
    - database_read

entry_point: intelligence_loop

min_core_version: "0.1.0"
tags:
  - trading
  - finance
  - market-data
```

### Arabic-Aware Skill

```yaml
name: arabic-doc-processor
version: 1.0.0
description: "Process Arabic documents with RTL layout and diacritics support"
author: "Kazma Community"
license: MIT

capabilities:
  - arabic_nlp
  - document_processing

mcp_servers:
  - name: arabic-ocr
    type: stdio
    command: ["npx", "-y", "@anthropic-ai/arabic-ocr-mcp"]

permissions:
  required:
    - file_read
    - file_write

entry_point: processor
min_core_version: "0.1.0"
tags:
  - arabic
  - nlp
  - rtl
  - ocr
```

---

## Appendix: Manifest File Format

The manifest file MUST be named exactly `skill_manifest.yaml` (not `manifest.yaml`, `manifest.yml`, or any other variation).

### File Structure

```yaml
# Lines starting with # are comments (YAML standard)
# Top-level keys are case-sensitive
# Use double quotes for strings containing special characters

name: my-skill
version: 1.0.0
description: "Description here"
author: "Author Name"
license: MIT

# ... optional fields ...
```

### Encoding

- File MUST be valid UTF-8
- YAML must parse without errors
- Unicode characters are allowed (e.g., Arabic text in descriptions)

### Size Limits

- Maximum file size: 64 KB
- Maximum number of MCP servers: 20
- Maximum number of capabilities: 50
- Maximum number of tags: 20
