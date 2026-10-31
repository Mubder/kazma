---
sidebar_position: 3
---

# Finding Skills

> This page is published at [kazma.ai/docs/hub/finding-skills/](https://kazma.ai/docs/hub/finding-skills/) ([Arabic](https://kazma.ai/ar/docs/hub/finding-skills/)).

Discover and install skills from the Hub registry and built-in natives.

## Search

```bash
kazma hub search "weather"
kazma hub search --capabilities "image_analysis,data_processing"
kazma hub search --tags "utility"
kazma hub search --author "kazma-team"
```

## Browse installed

```bash
kazma hub list
kazma hub info author/skill-name
kazma hub stats
```

## Install

Installing a hub skill is not implemented. `kazma hub install` (and the
`kazma wizard` installer, and the Skills page for a `kazma-hub://` id) says so
and installs nothing: the hub registry lists skills but cannot fetch one or
give the agent its tools. Install an Agent Skill instead (an `owner/repo` or a
GitHub URL, from the Skills page or `kazma agent-skills`).

Uninstall:

```bash
kazma hub uninstall author/skill-name
```

## Built-in native skills

Many tools ship in-repo under `kazma-skills/kazma_skills/native/` and load automatically (vault, git, cron, web crawl, …). You do **not** need hub install for those — see [Tools catalog](../reference/tools-catalog).

## Related

- [Hub overview](./overview)  
- [Creating skills](../skill-development/creating-skills)  
