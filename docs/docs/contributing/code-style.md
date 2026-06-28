---
sidebar_position: 2
---

# Code Style

## Python

- Python 3.11+
- Type hints required
- Line length: 100 characters
- Formatter: Ruff
- Linter: Ruff + MyPy

### Imports

```python
# Standard library first
import asyncio
from pathlib import Path

# Third-party
import yaml
import aiosqlite

# Local
from kazma_core.hub.manifest_schema import SkillManifest
```

### Type hints

```python
async def process(message: str, agent_id: str | None = None) -> dict:
    ...

def parse_skill_id(skill_id: str) -> tuple[str, str, str]:
    ...
```

## YAML

- 2-space indentation
- kebab-case for keys
- No trailing whitespace

## TypeScript/React

- Functional components
- TypeScript strict mode
- CSS modules for styling
