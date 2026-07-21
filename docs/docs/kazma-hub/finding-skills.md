---
sidebar_position: 3
---

# Finding Skills

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

```bash
kazma hub install author/skill-name
# or with version if your registry uses @version ids:
kazma hub install author/skill-name@1.0.0

# Interactive installer
kazma wizard
```

Uninstall:

```bash
kazma hub uninstall author/skill-name
```

## Built-in native skills

Many tools ship in-repo under `kazma-skills/kazma_skills/native/` and load automatically (vault, git, cron, web crawl, …). You do **not** need hub install for those — see [Tools catalog](../reference/tools-catalog).

## Related

- [Hub overview](./overview)  
- [Creating skills](../skill-development/creating-skills)  
