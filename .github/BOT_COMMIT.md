# Kazma Agent Bot Commits

This repository uses a **GitHub App** (`Kazma Agent`) to make bot commits
when Kazma's AI agent edits code.

## How it works

When Kazma's agent makes commits (via the `git_commit` tool or `/ide` commands),
the commit is authored as:

```
Kazma Agent <4310451+kazma-agent[bot]@users.noreply.github.com>
```

This uses the `GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment variables
(injected by `kazma_core/git_identity.py`), so the repo's `.git/config`
is never modified — the user's real git identity is always preserved.

## Configuration

See `kazma.yaml` → `git.bot_identity`:

```yaml
git:
  bot_identity:
    enabled: true
    name: "Kazma Agent"
    app_id: 4310451
    app_private_key_path: .keys/kazma-app.pem
    app_installation_id: 146867168
```

## Custom logo

The bot's avatar (the Kazma logo) is configured on the GitHub App settings
page at https://github.com/settings/apps/kazma-agent.

---

*This file was committed by Kazma Agent [bot] — its first commit.*
