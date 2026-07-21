---
sidebar_position: 4
---

# Testing Skills

Prefer **pytest** against your tool functions and hub validation against the package path.

## Native tools skill

```text
my_skill/
  skill_manifest.yaml
  tools.py
  tests/
    test_tools.py
```

```python
# tests/test_tools.py
import pytest
from tools import example_echo  # or import path under kazma_skills.native…

@pytest.mark.asyncio
async def test_example_echo():
    out = await example_echo(message="Hello")
    assert "Hello" in out
```

## Hub package validation

```bash
kazma hub validate ./my_skill
kazma hub check-certification ./my_skill
```

Manifest unit test:

```python
import yaml
from pathlib import Path

def test_manifest_loads():
    data = yaml.safe_load(Path("skill_manifest.yaml").read_text(encoding="utf-8"))
    assert data.get("name")
    assert "tools" in data or data.get("entry_point")
```

## Danger / HITL

If a tool is on the danger list, integration tests should either:

- Mock the safety bus, or  
- Pass the graph’s approved path only in controlled fixtures  

Do not disable HITL in production-like tests without an explicit escape hatch flag.

## Related

- [Creating skills](./creating-skills)  
- [Tools catalog](../reference/tools-catalog)  
