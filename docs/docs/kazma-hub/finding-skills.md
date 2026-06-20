---
sidebar_position: 3
---

# Finding Skills

Discover and install skills from Kazma Hub.

## Search

```bash
# Basic search
kazma hub search "weather"

# Search by capability
kazma hub search --capabilities "image_analysis,data_processing"

# Search by tag
kazma hub search --tags "utility,beginner-friendly"

# Search by author
kazma hub search --author "kazma-team"
```

## Browse

```bash
# List all installed skills
kazma hub list

# View detailed info
kazma hub info author/skill-name
```

## Install

```bash
# Install specific version
kazma hub install author/skill-name@1.0.0

# Install latest
kazma hub install author/skill-name

# Install using the wizard
kazma wizard
```
