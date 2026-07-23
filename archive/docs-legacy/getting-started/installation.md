---
sidebar_position: 1
---

# Installation

Kazma is a Python 3.11+ framework for building autonomous AI agent systems with skill management, delegation, and a central hub.

## Prerequisites

- Python 3.11 or later
- pip or uv package manager
- Node.js 18+ (for documentation site)

## Install from source

```bash
git clone https://github.com/kazma-ai/kazma.git
cd kazma
pip install -e ".[dev,cli]"
```

## Install from PyPI

```bash
pip install kazma
```

## Verify installation

```bash
kazma status
# Expected: Kazma status: OK
```

## Next steps

- [Quickstart guide](./quickstart) — build your first agent in 5 minutes
- [Configuration](./configuration) — customize Kazma for your environment
- [First skill](./first-skill) — install and run a skill from Kazma Hub
