---
sidebar_position: 4
---

# Testing Skills

Write tests for your skills using pytest and Kazma test utilities.

## Test structure

```
my-skill/
  skill_manifest.yaml
  main.py
  tests/
    __init__.py
    conftest.py
    test_my_skill.py
```

## Writing tests

```python
# tests/test_my_skill.py
import pytest
from my_skill import MySkill

@pytest.fixture
def skill():
    return MySkill()

@pytest.mark.asyncio
async def test_execute(skill):
    result = await skill.execute({
        "message": "Hello",
        "agent_id": "test-agent",
    })
    assert "result" in result
    assert "Hello" in result["result"]
```

## Validation tests

Test that your manifest validates correctly:

```python
import yaml
from kazma_core.hub.manifest_schema import SkillManifest

def test_manifest_valid():
    with open("skill_manifest.yaml") as f:
        data = yaml.safe_load(f)

    manifest = SkillManifest.from_dict(data)
    result = manifest.validate()
    assert result.passed, f"Validation failed: {result.errors}"
```

## Running tests

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=my_skill

# Run validation only
kazma hub validate ./my-skill
```
