---
sidebar_position: 4
---

# Pull Requests

> This page is published at [kazma.ai/docs/contributing/pull-requests/](https://kazma.ai/docs/contributing/pull-requests/) ([Arabic](https://kazma.ai/ar/docs/contributing/pull-requests/)).

## Process

1. Fork the repository
2. Create a feature branch
3. Make changes
4. Run tests: `pytest`
5. Lint: `ruff check .`
6. Commit with descriptive message
7. Push and create PR

## Commit messages

```
feat: add weather skill
fix: resolve checkpoint loading race condition
docs: update skill manifest spec
refactor: simplify dialect detection
test: add integration tests for delegation
```

## Review process

0. A change that adds or fixes behaviour comes with a test in the automated
   suite that fails without the change (a fix covers the whole class of the
   bug; `AGENTS.md` lists the gates that hold each rule)
1. Automated CI must pass
2. At least one maintainer approval
3. No unresolved conversations
4. Documentation updated if needed
