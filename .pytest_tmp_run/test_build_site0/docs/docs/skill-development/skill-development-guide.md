---
sidebar_position: 1
title: Skill Development Guide
---

# Skill Development Guide

## Overview

This guide covers creating, testing, and publishing skills for the Kazma Hub.

## Creating a Skill

1. Create a directory with your skill name
2. Add a `skill_manifest.yaml` with required metadata
3. Create a Python entry point class with an `execute` method
4. Write tests

## Skill Manifest

See the [Skill Manifest specification](../skill-development/skill-manifest) for the full format reference.

## Entry Point

Your skill must define a class with:

- `__init__(self, config=None)` — constructor
- `async execute(self, context)` — main execution method
- `async cleanup(self)` — optional cleanup

## Testing

```bash
kazma hub validate ./my-skill
pytest tests/
```

## Publishing

```bash
kazma hub register ./my-skill
kazma hub publish ./my-skill
```
