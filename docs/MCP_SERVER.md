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

**Whether a danger call waits or is refused depends on whether a human is
reachable.** `kazma mcp` is a separate process, usually spawned by your MCP
client, and the bus that carries approval cards lives in the running Kazma
server. A child process cannot see an in-memory bus in another process.

So there is a second path. The gate registry (`hitl_gates` in your data dir) is
a shared SQLite table, and the dashboard renders every pending row it finds
there. A bus-less process registers a gate and waits for the row to change
state; you get the same card, in the same place, and click the same button.

**It only engages when something is actually watching.** A running Kazma
instance heartbeats into that database from the loop that reads pending gates,
and `kazma mcp` refuses to queue without a fresh beat — because a card nobody
will ever see costs the caller the full approval timeout and then denies it
anyway. So there are two honest outcomes, and the banner tells you which:

```
[kazma mcp] 155 tools; no approval bus, but a live Kazma instance is watching
the gate registry (14s ago): danger tools queue for approval there

[kazma mcp] 100 tools; HITL is enabled, no approval bus is reachable from this
process, and no running Kazma instance is watching the gate registry, so
danger tools would be denied, not queued
```

In the second case danger tools are withheld rather than published and
refused: 55 tools that can only ever fail are just something for the client's
model to plan around and lose turns on.

Start Kazma, and your MCP client gets the write tools. Stop it, and they
disappear at the next reconnect.

| Variable | Effect |
|----------|--------|
| `KAZMA_BUS_BRIDGE=0` | Turn the bridge off. Restores the previous behaviour exactly: no bus, no approval, danger tools withheld. |
| `KAZMA_WATCHER_STALE_SECONDS` | How old a heartbeat may be and still count (default 120 — four missed watchdog ticks). |

**The server anchors to the Kazma install, not your editor's folder.** An MCP
client spawns `kazma mcp` with the working directory of whatever project it has
open, and Kazma's paths used to resolve by walking up from there — so it looked
for `kazma-data` beside an unrelated project, found no heartbeat, and withheld
every danger tool while reporting that no Kazma instance was running. It now
resolves from the package's own location. `KAZMA_PROJECT_ROOT` and
`KAZMA_DATA_DIR` still override, for a deliberately relocated install.

Verify any of this without an editor in the loop:

```bash
python scripts/mcp_probe.py /path/to/kazma
```

It performs the real `initialize` → `tools/list` handshake and prints the
banner, the gate database in use, the tool count, and whether the danger tools
are published. Asking an agent to describe its own tool surface does not work —
it reports the function list in its prompt, which is a different thing.

**What the bridge is not.** It carries a decision; it does not make one. There
is no "approve for the session" and no YOLO on this path: those are properties
of a chat thread, and a separate process has no thread whose later calls could
be re-checked against the grant. One decision, one tool call. And it fails
closed in every direction — no watcher, a stale one, a timeout, a vanished
row, an unreadable database all deny.

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
[kazma mcp] 155 tools; no approval bus, but a live Kazma instance is watching the gate registry (14s ago): danger tools queue for approval there
[kazma mcp] 100 tools; HITL is enabled, no approval bus is reachable from this process, and no running Kazma instance is watching the gate registry, so danger tools would be denied, not queued
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
