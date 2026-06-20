---
sidebar_position: 2
---

# Hub API Reference

## KazmaHub

```python
from kazma_core.hub import KazmaHub

hub = KazmaHub(registry_path="~/.kazma/hub/registry.db")
```

### Registry operations

| Method | Description |
|---|---|
| register(manifest) | Register a skill in the hub |
| unregister(skill_id) | Remove a skill from the hub |
| get(skill_id) | Fetch a skill by ID |
| search(query, capabilities, tags, author) | Search for skills |
| list_installed() | List locally installed skills |

### Install operations

| Method | Description |
|---|---|
| install(skill_id) | Install a skill locally |
| update(skill_id) | Update to latest version |

### Agent registry

| Method | Description |
|---|---|
| register_agent(agent) | Register an agent |
| unregister_agent(agent_id) | Remove an agent |
| find_agents_by_capabilities(required) | Find agents by capability |
| list_agents() | List all agents |
| update_agent_reputation(agent_id, score) | Update reputation |

## SkillManifest

```python
from kazma_core.hub import SkillManifest

manifest = SkillManifest.from_dict(data)
result = manifest.validate()
```

## SkillValidator

```python
from kazma_core.hub import SkillValidator

validator = SkillValidator()
result = await validator.validate(skill_path)
```
