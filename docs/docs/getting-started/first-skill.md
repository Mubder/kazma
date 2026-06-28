---
sidebar_position: 4
---

# Your First Skill

This guide walks you through installing a skill from Kazma Hub and using it in your agent.

## Using the wizard

The easiest way to install your first skill:

```bash
kazma wizard
```

The wizard will:
1. Show available skills from the hub
2. Let you browse and select a skill
3. Display the skill manifest for review
4. Run security validation
5. Install and verify the skill

## Using CLI commands

### Browse skills

```bash
kazma hub search "drone inspection"
```

### View skill details

```bash
kazma hub info author/drone-inspector@1.0.0
```

### Install a skill

```bash
kazma hub install author/drone-inspector@1.0.0
```

### Verify installation

```bash
kazma hub list
```

## Skill structure

A typical skill directory:

```
my-skill/
  skill_manifest.yaml
  main.py
  requirements.txt
  README.md
```

The `skill_manifest.yaml` defines metadata, capabilities, and dependencies:

```yaml
name: drone-inspector
version: 1.0.0
author: kazma-team
description: Autonomous drone inspection skill
capabilities:
  - drone_control
  - image_analysis
permissions:
  - camera_access
  - network_outbound
```

## Next steps

- [Creating skills](../skill-development/creating-skills) — build your own
- [Skill manifest spec](../skill-development/skill-manifest) — manifest format reference
- [Security auditing](../kazma-hub/security-auditing) — understand security checks
