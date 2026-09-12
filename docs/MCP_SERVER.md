# `kazma mcp` — your agent keeps its brain, its tools get a seatbelt

Kazma is mostly an MCP *client*: it consumes other people's tools. This is the
other direction, and it is the more useful one.

> **There are two MCP servers in this repo.** `kazma_gateway.mcp_server`
> ("kazma-ide", `python -m kazma_gateway.mcp_server`) is the older, narrow one:
> seven hand-written IDE tools behind a `KAZMA_SECRET`, gated with
> `check_sync()` — which can only *block* a danger tool, never queue it for
> approval. This page is about `kazma mcp`, the general one: the whole tool
> registry, routed through the real gate. Use the IDE server for a fixed,
> secret-gated file/test surface; use this one to hand an arbitrary agent
> Kazma's tools.

Point any MCP client — Claude Desktop, an editor, another agent — at
`kazma mcp`, and it can call Kazma's tools. Nothing dangerous happens without
going through the same gate the chat window uses — and when that gate cannot
reach a human, the dangerous tools are not offered at all.

That matters because the agent asking is usually not the thing you distrust.
What you distrust is `shell_exec` running unattended at 2am because a model
misread a webpage. Kazma does not replace your agent. It replaces the part
where nobody was watching.

---

## Setup

```bash
kazma mcp --help
```

Claude Desktop (`claude_desktop_config.json`), or any client that speaks MCP
stdio:

```json
{
  "mcpServers": {
    "kazma": {
      "command": "kazma",
      "args": ["mcp"]
    }
  }
}
```

Restart the client. Kazma's tools appear in its tool list. Ask it to read a
file and it just works. Whether *write* tools appear depends on whether an
approval path is reachable — see the next section, and check the startup
banner.

---

## What the client sees

Read-tier tools — `file_read`, `web_search`, `sqlite_search` — are marked
`readOnlyHint` and run immediately.

Danger-tier tools — `shell_exec`, `file_write`, `file_apply_patch_set`,
`git_push`, `send_message`, and the other 50-odd — are marked
`destructiveHint`, so a client that renders MCP annotations shows them as
destructive in its own UI without reading these docs. Their descriptions also
say, in words the model will read:

> `[Kazma] Danger-tier: this call pauses for a human approval in Kazma before
> it runs.`

**Whether a danger call waits or is refused depends on where the approval bus
is.** `kazma mcp` is a separate process, usually spawned by your MCP client,
and the bus that carries approval cards lives in the running Kazma server. If
this process cannot reach one, `safety.check()` does not queue anything — it
fails closed and **denies**.

So danger tools are published *only* when an approval path is actually
reachable. Otherwise they are withheld, and the banner says why:

```
[kazma mcp] 100 tools; HITL is enabled but no approval bus is reachable from
this process, so danger tools would be denied, not queued
```

That is the common case for a client-spawned server today, and it is the
honest one: 55 tools that can only ever be refused would just be something for
the client's model to plan around and fail on. Connecting a client-spawned
server to the running instance's bus is real work and is not done.

---

## Where the gate actually is

Nowhere in the MCP server, deliberately.

`tools/call` hands straight to `LocalToolRegistry.execute()`, the single
tool-execution chokepoint. Commitment authorization, PreToolUse hooks, the
`kazma-permissions.yaml` allowlist and the HITL bus all already live inside
it. The MCP server contains zero safety logic of its own and is meant to stay
that way — a second gate beside that one is the collision the architecture
notes call out by name (H-8), and it produces duplicate approval cards and
deadlocks rather than more safety.

The practical consequence: anything you do to tighten Kazma's gate applies to
MCP clients automatically, and nothing about MCP can loosen it.

---

## Fail-closed when nothing can approve

If HITL is disabled, `execute()` lets danger tools through unattended. That is
a reasonable default for a developer typing into their own chat window. It is
not reasonable for an MCP client calling in with nobody watching.

So when Kazma detects no approval path, **danger tools are withheld
entirely** — not listed, not callable. A tool a client can see is a tool its
model will plan around, so they are not advertised and then refused. The
startup banner (on stderr) tells you which mode you are in:

```
[kazma mcp] 155 tools; HITL enabled: danger tools require approval
[kazma mcp] 100 tools; HITL is enabled but no approval bus is reachable from this process, so danger tools would be denied, not queued
[kazma mcp] 100 tools; HITL is disabled (safety.hitl.enabled), so execute() would run danger tools unattended
```

Calling a withheld tool returns an error explaining how to fix it rather than
a bare refusal.

---

## Configuration

| Variable | Effect |
|----------|--------|
| `KAZMA_MCP_TOOLS` | Comma-separated allowlist. Unset publishes everything. Enforced on `tools/call` as well as `tools/list`, because a client can call a name it was never offered. |
| `KAZMA_MCP_ALLOW_UNGATED` | Publish danger tools even with no verified approval path. For a lab box where that is genuinely what you want. The banner says so loudly. |

Narrowing the surface is worth doing. A focused server is easier to reason
about than 155 tools:

```json
{
  "mcpServers": {
    "kazma": {
      "command": "kazma",
      "args": ["mcp"],
      "env": { "KAZMA_MCP_TOOLS": "file_read,file_write,shell_exec,web_search" }
    }
  }
}
```

---

## Protocol notes

- JSON-RPC 2.0, newline-delimited, on stdio. Hand-rolled like `kazma acp` and
  the MCP client; the MCP SDK is not a dependency and this does not add one.
- Protocol versions `2025-06-18`, `2025-03-26` and `2024-11-05` are
  negotiated; an unknown version falls back to the newest we implement.
- **stdout carries protocol frames and nothing else.** The server repoints
  `sys.stdout` at stderr for the duration, so a stray `print` from any library
  cannot land between two frames and break the session.
- Frames are written with `ensure_ascii=True`. Escaped `\uXXXX` is valid JSON,
  every client decodes it, and no stream encoding can reject it. Without that,
  one tool description containing an en-dash kills the server on a Windows
  console — which is how this was found.
- stdin and stdout are reconfigured to UTF-8, so non-ASCII arguments (Arabic
  paths, for one) survive the trip.

---

## What this is not

- **Not a remote server.** stdio only, on your machine, as a child process of
  your client. There is no network listener and no auth layer, because there
  is nothing to authenticate to. Do not expose it with a socket wrapper and
  assume the gate is an access-control system — it gates *actions* for a
  present human, not *identities*.
- **Not a sandbox.** Approval is consent, not containment. `python_exec` runs
  in Docker when `KAZMA_CODE_EXEC_DOCKER=force`; `shell_exec` after approval
  is host power. See `SECURITY.md`.
- **Not a second brain.** The client's model does the thinking. Kazma supplies
  tools and the gate in front of them.
