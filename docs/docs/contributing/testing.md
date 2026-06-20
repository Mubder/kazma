---
sidebar_position: 3
---

# Testing

## Test framework

- pytest with pytest-asyncio
- Coverage with pytest-cov

## Writing tests

```python
import pytest
from kazma_core.checkpoint import CheckpointManager

@pytest.fixture
async def manager(tmp_path):
    m = CheckpointManager(db_path=str(tmp_path / "test.db"))
    yield m
    await m.close()

@pytest.mark.asyncio
async def test_save_and_load(manager):
    state = {"agent_id": "test", "step": 1}
    await manager.save(state)
    loaded = await manager.load(agent_id="test")
    assert loaded["step"] == 1
```

## Running tests

```bash
pytest
pytest --cov=kazma_core --cov-report=html
pytest -v
```
