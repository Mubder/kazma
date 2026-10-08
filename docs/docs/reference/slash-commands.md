---
id: slash-commands
title: Slash Commands
sidebar_label: Slash Commands
description: Gateway slash commands reference (instant, no LLM)
---
Kazma's gateway handles slash commands in Telegram, Discord and Slack before a
message reaches the agent. Most answer at once, without a model call;
`/research`, `/swarm` and `/ide skill` start work that uses one. The order
they are tried in is under [Command Lifecycle](#command-lifecycle).

> **Web research:** deep research also has gateway/UI entry points (`/research deep …`, Web `/research` panel). Prefer normal chat or those entry points rather than inventing ad-hoc slash variants. See [Web research](../guide/web-research) and [Recent features](../guide/recent-features).

---

## Documents {#documents}

Surfaces the shared **Document Intelligence** platform (`DocumentIngestionService`)
via `/documents` (alias `/docs`) across Telegram, Discord, Slack, and other
gateway chats. Reads use opaque IDs; no raw server paths from the user.

| Subcommand | Usage | Description |
|---|---|---|
| *(help)* | `/documents` | Help + recent documents |
| `list` | `/documents list` | List id, title, state |
| `status` | `/documents status <id>` | Job/document durable state |
| `read` | `/documents read <id>` | Paged fenced content when ready |
| `convert` | `/documents convert <id> <format>` | Convert to pdf/html/docx/markdown |
| `pdf-info` | `/documents pdf-info <id>` | Structural PDF report |
| `redact` | `/documents redact <id> <term[,term…]>` | Physical redact → new artifact |
| `search` | `/documents search <library> <query>` | Search an indexed library |
| `health` | `/documents health` | Capability + worker readiness |

Alias: `/docs …` accepts the same subcommands.

Guide: [Document Intelligence](../guide/document-intelligence) ·
API: [API routes — Documents](./api-routes.md#documents--document-intelligence).

---

## 📋 Plan mode {#plan-mode}

Inspect and propose, then execute on approve. Mutating tools are **structurally**
blocked while plan mode is on (not a prompt nudge). HITL still applies after
approve.

| Subcommand | Usage | Description |
|---|---|---|
| *(status)* | `/plan` | Show whether plan mode is on |
| `on` | `/plan on` | Enter plan mode (write/exec/patch/shell blocked) |
| *(task)* | `/plan <task>` | Enter and plan that task in the same turn |
| `go` | `/plan go` | Approve the plan and execute (alias: **Proceed**) |
| `off` | `/plan off` | Leave without executing |

Kill-switch: `KAZMA_PLAN_MODE=0`. Web: Plan pill on the composer bar.

---

## 🔄 Session Commands

### `/sessions`, `/session` and `/new` {#new}

A **season** is one conversation, and the same season list is on every
platform: the Web UI's chat list and each chat app share it, so a
conversation begun on Telegram can be continued on the Web or in Slack.

| Command | Description |
|:---|:---|
| `/sessions` (`/seasons`) | List every season — Web, Telegram, Discord, Slack (the 40 most recent; archived ones left out) |
| `/session <#, id or name>` (`/season`, `/switch`) | Take a season over: this chat continues it (your own seasons only) |
| `/session all` | The list with archived seasons |
| `/session here` | Which season this chat is on, with its Web link |
| `/session new [name]` | Start a fresh season, named or not; `/new` does the same |

A new season keeps the old one: it stays in the list and in the Web UI
sidebar, and what it held in working memory moves to long-term memory.

**Usage:**
```
/sessions
/session 2
/session new release notes
```

**Required permissions:** None.

---

### `/reset`

Clears this chat's conversation and starts fresh: the saved conversation (its
checkpoints) is deleted and the Web UI's copy of the season is emptied.

**Usage:**
```
/reset
```

**Response:**
```
🔄 Conversation cleared and reset to default. Starting fresh!
```

**Side effects:**
- What Kazma learned is not forgotten: memory keeps it (`/memory off` keeps
  a chat out of memory and forgets what it left).
- Snapshot history is kept (`/replay clear` purges it).

**Required permissions:** None.

---

### `/compact`

Summarizes the chat's older messages now, as Kazma does by itself when the
conversation nears the model's window. Useful before a long task, or when
`/context` shows the conversation filling up.

**Usage:**
```
/compact
```

**Response:**
```
🗜️ Context compaction completed successfully! Your conversation history has been summarized and compressed.
```

**Required permissions:** None.

---

### `/undo`

Removes the last reply from the chat's saved conversation, with the tool calls
and results that produced it, so the next message continues as if it had not
been given.

**Usage:**
```
/undo
```

**Response (success):**
```
✅ Removed last assistant response. You can continue the conversation.
```

**Response (nothing to undo):**
```
↩️ No assistant response to undo.
```

**Side effects:**
- The reply already sent stays in the chat app; only the conversation Kazma
  keeps changes.

**Required permissions:** None.

---

### `/edit`

Replaces the last reply in the chat's saved conversation with your corrected
text, so the next turns build on it.

**Usage:**
```
/edit The corrected response text goes here.
```

**Response (success):**
```
✅ Replaced last response. You can continue the conversation.
```

**Response (missing text):**
```
✏️ *Usage:* `/edit <corrected text>`

Replaces the last assistant message in conversation history.
```

**Response (nothing to edit):**
```
✏️ No conversation history to edit.
```

**Side effects:**
- The reply already sent in the chat app is not edited.

**Required permissions:** None.

---

### `/replay`

Time-travel debugging: list snapshots, restore from a specific iteration, compare two runs, or clear snapshot history. Snapshots are captured after every supervisor iteration automatically.

Sub-commands:

| Command | Description |
|:---|:---|
| `/replay list` | Show all snapshots for the current thread |
| `/replay <N>` | **Restore** — rewind the live thread to iteration N (later turns are lost; use `/fork` to preserve them) |
| `/replay compare <A> <B>` | Diff two snapshots (messages, cost, model, routing) |
| `/replay clear` | Purge all snapshots for this thread |

### `/fork`

Branch from a snapshot into a **new thread** — the original stays intact.

| Command | Description |
|:---|:---|
| `/fork <N>` | Fork from iteration N into a new thread (seeded with the snapshot state + session context; appears in the Web UI sidebar) |

**Usage:**
```
/replay list
/replay 3
/replay compare 1 3
/replay clear
```

**Response (`/replay list`):**
```
🕰️ *Available snapshots:*

• Iteration `1` — 2026-06-26T14:30:00 — file_write: app.py
• Iteration `2` — 2026-06-26T14:31:15 — git_commit
```

**Response (no snapshots):**
```
📭 No snapshots available for this thread.
```

**Response (`/replay clear`):**
```
🗑️ Cleared 5 snapshot(s) for this thread.
```

**Dependency:** The `SnapshotRecorder` is wired into all graph-build sites by default (enabled via `time_travel.enabled: true` in `kazma.yaml`). If disabled:
```
⏳ Time travel not yet available.
```

**Required permissions:** None.

---

## 🧭 Running Task Commands

Out-of-band signals to a **running** turn. These intercept *before* the
in-flight turn's lock, so they take effect immediately rather than queuing
behind it. Available on every platform (Web, Telegram, Discord, Slack).

### `/steer` (soft)

Adds extra context to the running task. The text is folded into the agent's
**next step** — it does not interrupt the current one.

**Usage:**
```
/steer also cover the error-handling path
```

**Response:**
```
🧭 Steer noted — I'll fold it into the next step.
```

---

### `/steer!` (hard)

Pauses the running task, injects the requirement, then resumes. The graph
suspends via an interrupt, your text is injected, and the turn continues under
the new requirement.

**Usage:**
```
/steer! stop — use the Postgres backend, not SQLite
```

**Behavior:**
- If the task is already **finalizing** (can't pause), `/steer!` automatically
  **demotes to a soft steer** and tells you so — your input is still applied.
- If the resume fails, you get a clear `⚠️ Could not resume the task after steering.`

---

### `/abort`

Cancels and **abandons** the running task. The turn is marked `abandoned`,
`auto_continue` is cleared, and an abort marker is injected so the agent will
not continue the task unless you ask it to redo it.

**Usage:**
```
/abort
```

**Response:**
```
⛔ Task aborted — I won't continue it unless you ask me to redo it.
```

> `/steer`, `/steer!`, and `/abort` are resolved by the graph handler
> (`agent_handler/graph.py`), not the gateway slash resolver — they need live
> access to the running turn's checkpoint state.

### `/long` — budget/mission mode & the Partial protocol

`/long on` (soft Research budget — may end **Partial**), `/long mission`
(run-until-done with a hard wall, default 500 rounds), `/long status`,
`/long off`. A full reference lives in the diagnosis map (§11 Long-task);
the behavior that matters in chat:

- When a turn hits the budget/recursion limit, Kazma replies with the
  salvaged progress (**Partial**). Reply **Proceed** (or the remaining
  steps) and the stored continue-context is injected so nothing is re-done.
- Since **2026-08-19** the continue-context is only injected for
  continuation-shaped replies (short "proceed/continue/yes/…"): a fresh
  command after a Partial runs as a **new task** — never reframed as a
  note for the old mission.
- A Partial **pauses** the long task (baseline budgets, no mission
  framing) until you `/long` again or the TTL lapses; `/long off` clears
  it immediately.
- `/mission` is `/long mission`. A task written after `/long mission` (or
  `/mission`) runs under the new budget straight away.

### `/yolo`

Skips the approval card for danger tools **in this chat only**, for an hour
(`KAZMA_YOLO_TTL_SECONDS`) or until `/yolo off`; turning it on and off is
logged. X posts and git writes still ask, and the exec denylist still
refuses catastrophic commands. It does not raise the tool-round budget —
`/long` does. Refused where `KAZMA_ALLOW_YOLO=0`, and in production unless
`KAZMA_ALLOW_YOLO=1`, with the reason.

**Usage:**
```
/yolo
/yolo status
/yolo off
```

### `/long yolo` and `/unrestricted`

`/long yolo` is the research budget **and** YOLO. `/unrestricted` is the
mission budget and YOLO — full power for this chat — until
`/unrestricted off`.

**Usage:**
```
/long yolo
/unrestricted
/unrestricted off
```

### `/hitl`

Answers a paused danger-tool approval from the chat, as its buttons do.

| Command | Description |
|:---|:---|
| `/hitl approve [thread_id]` | Approve (also `yes`, `y`, `allow`) |
| `/hitl deny [thread_id]` | Deny |
| `/hitl approve_task [thread_id]` | Approve every danger tool of this task, until your next message (at most 10 minutes, `KAZMA_TASK_GRANT_TTL_SECONDS`) |
| `/hitl opt <thread_id> <option>` | Choose an option on a clarify / confirm card |

The thread defaults to this chat's. Only the person who started the paused
task can answer it, and a card whose turn has moved on says it expired
instead of approving anything.

---

## 🔧 Tool Commands [core]

The gateway answers these itself, from Kazma's settings and the agent's own
reports, without a model call.

### `/personality`

View or switch the agent's personality profile. 8 built-in profiles are available.

**Usage:**
```
/personality              # Show current personality
/personality list          # List all available profiles
/personality [name]        # Switch to a specific profile
```

**Available profiles:** `default` (🤖), `friendly_expert` (😊), `concise` (⚡), `gulf_engineer` (🛠️), `creative_partner` (🎨), `sysadmin` (🐧), `teacher` (📚), `code_reviewer` (🔍)

**Response (show current):**
```
🎭 Current personality: default 🤖
Professional AI assistant, efficient and helpful.
```

**Response (list all):**
```
🎭 *Available personalities:*

• `code_reviewer` 🔍 — Direct, constructive. Points to exact lines. Suggests alternatives.
• `concise` ⚡ — Short answers, no fluff. Bullet points preferred.
• `creative_partner` 🎨 — Playful brainstorming partner. Multiple angles. Uses emoji.
• `default` 🤖 — Professional AI assistant, efficient and helpful.
• `friendly_expert` 😊 — Warm, encouraging expert who explains concepts clearly.
• `gulf_engineer` 🛠️ — Kuwaiti engineering colleague. Gulf Arabic phrases. Practical, no-nonsense.
• `sysadmin` 🐧 — Terse, technical. Shell commands first. Assumes competence.
• `teacher` 📚 — Patient explainer. Breaks down concepts step by step. Checks understanding.

_Switch with `/personality <name>`_
```

**Response (switch):**
```
✅ Switched to **concise**: Short answers, no fluff. Bullet points preferred.
```

**Response (unknown profile):**
```
❌ Unknown personality: `unknown`

Available: code_reviewer, concise, creative_partner, default, friendly_expert, gulf_engineer, sysadmin, teacher

Use `/personality list` to see descriptions.
```

**Priority chain:** Runtime override > the `agent.personality` setting > `KAZMA_PERSONALITY` env var > `default`.

**Required permissions:** None to show or list. Switching changes every
reply, web chats included: admin-only (see [Permissions](#permissions)).

---

### `/context`

Shows how much of the model's context window this chat's saved conversation
fills, the threshold at which older turns are trimmed and summarized, and the
active workspace, model and provider. `/context detailed` adds a breakdown by
message role. It is the same report as the agent's `context_info` tool.

**Usage:**
```
/context
/context detailed
```

**Response:**
```
📊 Context Window
Tokens: 9,214 / 128,000 (7%)
Summarization threshold: 24,000 tokens (38% utilized)
Workspace: /home/you/kazma/kazma-data/workspace
Model: deepseek-chat  Provider: deepseek
```

**Response (`/context detailed`)** adds a line after the token count:
```
Role breakdown: assistant=5,902, system=1,840, tool=911, user=561
```

**Window:** the active model's context window (`context.max_context_tokens`,
or the model's known window). **Threshold:** the trim budget — 60% of the
window, at most 24,000 tokens unless `agent.trim.token_budget` sets it.

**Required permissions:** None.

---

### `/config`

An interactive configuration wizard. Show the current config, switch the
model or personality, turn memory on or off, switch MCP servers, and export
-- all without editing YAML. Each change writes the one setting it changes.

**Usage:**
```
/config                        # show current configuration
/config show                   # same as above
/config model <name>           # switch the active model and its provider
/config personality <name>     # switch personality (alias of /personality)
/config memory on|off          # turn memory on or off
/config tools list             # show the MCP servers
/config tools toggle <name>    # turn an MCP server on or off (now and at every start)
/config export                 # export config as JSON
```

**Required permissions:** None to show or export. `model`, `memory`, `tools`
and a personality switch change Kazma for every user and platform:
admin-only (see [Permissions](#permissions)).

---

### `/skill`

Manage **Agent Skills** (discoverable, HMAC-signed capability bundles). Skills
are published to the agentskills.io hub and installed from GitHub.

**Usage:**
```
/skill list                    # list installed Agent Skills
/skill install <owner/repo>    # install from GitHub (agentskills.io)
/skill activate <name>         # arm a skill for this chat
/skill deactivate              # clear the active skill
/skill uninstall <name>        # remove an Agent Skill
```

**Deep dive:** [Skill development](../skill-development/creating-skills) ·
[Kazma Hub](../kazma-hub/overview).

**Required permissions:** `install` and `uninstall` add or remove a skill
for everyone: admin-only (see [Permissions](#permissions)). The rest: none.

---

## ℹ️ Info Commands

### `/help`

Lists all available commands grouped by category.

**Usage:**
```
/help
```

**Response:**
```
*Available commands:*

🔄 *Session*
• `/sessions` (`/seasons`) — List every season (Web + Telegram + Discord + Slack)
• `/session 2` (`/season`, `/switch`) — Continue that season here (take over)
• `/session new [name]` — Start a fresh season (`/new` still works)
• `/research deep <topic>` — deep research via the same agent
• `/swarm <task>` — dispatch workers via the same agent
• `/reset` — Clear the conversation history and start fresh
• `/compact` — Manually trigger context window compaction
• `/undo` — Remove the last reply from the conversation
• `/edit <text>` — Replace the last reply with your corrected text
• `/replay list` — Show available snapshots
• `/replay <iteration>` — Restore from iteration (rewinds in-place)
• `/replay compare <a> <b>` — Compare two snapshots
• `/replay clear` — Clear snapshots for this thread
• `/fork <iteration>` — Fork from iteration into a new thread

🧭 *Running task*
• `/steer <text>` — Add context to the running task (applies next step)
• `/steer! <text>` — Pause the task, inject a requirement, then resume
• `/abort` — Stop and abandon the running task (won't continue unless re-asked)
• `/hitl approve` · `/hitl deny` — Answer a pending approval (`/hitl approve_task` — every approval of this task)

⚡ *Capacity & YOLO*
• `/long on` · `/long mission` (`/mission`) — raise tool-round budget (HITL stays on)
• `/yolo` — skip danger-tool approvals (does **not** raise the budget)
• `/long yolo` — research budget **and** YOLO
• `/unrestricted` — mission + YOLO (full power this chat)
• `/long off` · `/yolo off` · `/unrestricted off`

📋 *Plan mode*
• `/plan on` · `/plan <task>` — inspect and propose (write/exec blocked)
• `/plan go` · **Proceed** — approve and execute (HITL still on)
• `/plan off` · `/plan status`

🔧 *Tools*
• `/personality` — Show current personality
• `/personality list` — List all available personalities
• `/personality <name>` — Switch personality
• `/context` — Show context window usage
• `/x` — X Studio: drafts, accounts, scheduled posts (`/x help`)
• `/ide` — Workspace files, git and coding skills (`/ide help`)
• `/kb` — Knowledge libraries: list, crawl, search (`/kb help`)
• `/skill list` — List installed Agent Skills
• `/skill install <owner/repo>` — Install from GitHub (agentskills.io)
• `/skill activate <name>` — Arm a skill for this chat
• `/skill deactivate` — Clear the active skill
• `/skill uninstall <name>` — Remove an Agent Skill

📄 *Documents*
• `/documents list` (`/docs`) — List processed documents
• `/documents status <id>` — Durable job state
• `/documents read <id>` — Read a ready document
• `/documents search <library> <query>` — Search indexed docs

⚙️ *Config*
• `/config show` — Display current configuration
• `/config model <name>` — Switch model
• `/config personality <name>` — Switch personality
• `/config memory on|off` — Toggle memory
• `/config tools list` — Show the MCP servers
• `/config tools toggle <name>` — Turn an MCP server on or off (now and at every start)
• `/config export` — Export config as JSON

ℹ️ *Info*
• `/help` — Show this list
• `/status` — Gateway health overview
• `/model` (`/models`) — Show and switch the model
• `/memory` — Report memory usage; `/memory off` / `/memory on` — keep this chat out of memory, or let it back in
• `/cost` — Tokens and cost of this chat

For anything else, just ask the agent directly!
```

**Required permissions:** None.

---

### `/status`

Returns the gateway's current health: each chat app with what its
connection says (`connected`, `connecting`, `down`, or `running` when it
keeps no record), then the messages waiting in the gateway's queue and the
other messages being handled now. What it cannot read it shows as `?`.

**Usage:**
```
/status
```

**Response:**
```
*Gateway Status*
● Gateway: **running**
• Telegram: `connected`
• Slack: `connecting` — Slack refused the Socket Mode connection (invalid_auth)
• Queue depth: `0`
• Messages in progress: `1`
```

The first character is `●` (U+25CF) for running, `○` (U+25CB) for stopped,
and `?` when the gateway could not be read.

**Context keys:** `started`, `adapters`, `queue_depth`, `in_progress` — read from the `GatewayManager` (its stats, connection report and message handlers).

**Required permissions:** None.

---

### `/model`

Shows the providers and their models, and switches the active model. On
Telegram it opens a picker: tap a provider, then a model. Elsewhere it lists
each provider with up to five of its models, the active one marked; switch
with `/config model <name>`. `/models` is the same.

**Usage:**
```
/model
```

**Response (outside Telegram):**
```
Available providers:

  DeepSeek — 2 models
    deepseek-chat *(active)*
    deepseek-reasoner

Use `/config model <model_name>` to switch.
```

**Required permissions:** None.

---

### `/memory`

Reports how many facts Kazma holds for you and whether it remembers this
chat. `/memory off` keeps this chat out of memory from now on and forgets
what it left there; `/memory on` lets new messages back in (what was
forgotten stays forgotten).

**Usage:**
```
/memory
/memory off
/memory on
```

**Response:**
```
💾 Memory: `42` stored facts. This chat is remembered (`/memory off` to stop).
```

**Context key:** `memory_count` — the current facts of your memory tenant.

**Required permissions:** None.

---

### `/cost`

Shows the tokens and cost of this chat's model calls, from the per-call
ledger (`llm_calls.db`).

**Usage:**
```
/cost
```

**Response:**
```
💰 Session cost: `$0.0234` (2,481 tokens, 12 model calls)
```

**Context keys:** `total_tokens`, `total_cost`, `total_calls` — summed from the ledger for this chat's thread.

**Required permissions:** None.

---

## `/ide` — IDE Coding Commands

**Where handled:** `kazma_gateway/agent_handler/commands.py:_try_ide_command`
(intercepted in the gateway, skips the graph — same path as `/swarm`).

All `/ide` commands drive the transport-neutral `IdeService` in
`kazma_core/ide/`. Mutating/executing operations (`edit`, `delete`, `run`,
`git`) flow through the shared `LocalToolRegistry` + HITL danger-tool gate.

### Subcommands

| Command | Description |
|---------|-------------|
| `/ide` | Show help with all subcommands |
| `/ide ls [path]` | List a directory in the workspace |
| `/ide open <file>` | Read a file (shown in a code block) |
| `/ide edit <file> <text>` | Write content to a file (HITL-gated) |
| `/ide delete <file>` | Delete a file or directory (HITL-gated) |
| `/ide run <command>` | Run a shell command in the workspace (HITL-gated) |
| `/ide runfile <file>` | Run a script with its inferred interpreter |
| `/ide grep <pattern> [glob]` | Regex search the workspace |
| `/ide git <subcommand>` | Run a git subcommand (HITL-gated) |
| `/ide repo` | Manage workspaces (list, switch, clone, activate by slug) |
| `/ide skill [name] [file]` | Run a coding skill (refactor-file, write-tests, fix-lint, code-review) |
| `/ide swarm <task>` | Dispatch a coding task to the swarm |

### Examples

```
/ide                              → shows help
/ide open kazma_core/ide/service.py → reads the file
/ide edit config.yaml "key: value" → writes (HITL approval required)
/ide run pytest -q                → runs tests (HITL approval required)
/ide repo clone Mubder/kazma      → clones + activates as workspace
/ide skill write-tests kazma_core/ide/service.py → generates tests via swarm
```

**Danger-tier operations** (`edit`, `delete`, `run`, `git`) require HITL
approval — the same gate as the agent and swarm. See AGENTS.md §7.
`/ide repo switch`, `/ide repo clone` and `/ide repo <owner/repo>` change
the active workspace for everyone: admin-only (see [Permissions](#permissions)).

**Available on:** Telegram, Discord, Slack, Web (chat), TUI.

---

## `/swarm` — Swarm Dispatch (chat)

**Where handled:** `kazma_gateway/agent_handler/commands.py:_try_swarm_command`
(intercepted in the gateway; the chat form of the `kazma swarm` CLI). Danger
dispatches still go through the [swarm bus HITL gate](../guide/security-and-safety).

| Command | Description |
|---------|-------------|
| `/swarm` | Show help + worker list |
| `/swarm status` | Show swarm status |
| `/swarm list` | List registered workers |
| `/swarm config` | Show output-routing config |
| `/swarm config group <chat_id>` | Route swarm output to a Telegram group |
| `/swarm config clear` | Disable output routing |
| `/swarm broadcast <task>` | Dispatch to **all** workers |
| `/swarm pipeline <w1,w2,…> <task>` | Sequential pipeline |
| `/swarm consult <w1,w2,…> <task>` | Parallel consult |
| `/swarm fanout <w1,w2,…> <task>` | Parallel fan-out |
| `/swarm dispatch <worker> <task>` | Dispatch to one named worker |
| `/swarm <natural-language task>` | Auto-route to the best workers via `CapabilityRouter` |

### Examples
```
/swarm status
/swarm pipeline researcher,builder,validator "Build a CLI tool"
/swarm summarize today's AI news        # auto-routed
```

`/swarm config group` and `/swarm config clear` are admin-only (see
[Permissions](#permissions)).

**Deep dive:** [Swarm orchestration](../guide/swarm-orchestration) ·
[CLI reference](../guide/cli-reference).

---

## `/kb` — Knowledge Library (chat)

**Where handled:** `kazma_gateway/agent_handler/commands.py:_try_kb_command`.
Ingest documentation sites into searchable RAG corpora from any chat platform.

| Command | Description |
|---------|-------------|
| `/kb` | Show help + library list |
| `/kb list` | List libraries (id, chunks, seed) |
| `/kb add <id> <url>` | Create-or-use a library, ingest **one** page (sync) |
| `/kb crawl <id> <url> [N]` | Ingest the **whole** doc tree (background job) |
| `/kb refresh <id>` | Re-crawl a library from its seed URL (background) |
| `/kb search <id> <query>` | Direct search (useful without an LLM call) |
| `/kb status <id>` | Live progress of a running crawl/refresh |
| `/kb delete <id>` | Delete a library + all its chunks |

### Example
```
/kb crawl fastapi https://fastapi.tiangolo.com     # ingest the whole site
/kb search fastapi "dependency injection"
/kb status fastapi
```

**Deep dive:** [Knowledge Library](../guide/knowledge-library).

---

## `/research` — Deep Research (chat)

**Where handled:** `kazma_gateway/agent_handler/commands.py`. Gateway entry
point to the deep-research pipeline (multi-query search → parallel acquire →
digest → LLM synthesis → report).

| Command | Description |
|---------|-------------|
| `/research deep <topic>` | Run a full deep-research pass (progress pings while running) |

You can also start research from the **Web `/research` panel** or by phrasing a
request as deep research in normal chat. Disable routing with
`KAZMA_RESEARCH_ROUTE=0`.

**Deep dive:** [Web research](../guide/web-research) ·
[Recent features](../guide/recent-features).

---

## Command Lifecycle

A chat-app message that starts with `/` reaches the gateway's handler
(`kazma_gateway/agent_handler/graph.py`), which tries, in this order:

1. The commands that act on the chat's saved conversation or on a running
   turn: `/model`, `/hitl`, the session commands, `/reset`, `/compact`,
   `/long` / `/mission` / `/yolo` / `/unrestricted`, `/plan`, `/undo`,
   `/edit`, `/replay <n>`, `/fork`, `/steer`, `/abort`.
2. The resolver. `_build_slash_ctx()` (`agent_handler/commands.py`) reads
   what the command reports from where it lives, and `resolve_slash_command()`
   (`kazma_gateway/slash_commands.py`) answers `/help`, `/status`, `/memory`,
   `/cost`, `/context`, `/personality`, `/config` and `/replay list|compare|clear`
   without a model call.
3. `/swarm`, `/ide`, `/kb`, `/documents`, `/research`, `/x` and `/skill`.

Anything else goes to the agent as a normal message.

## Adding a New Slash Command

1. **Answered without the model:** a `_cmd_<name>()` in
   `kazma_gateway/slash_commands.py` and a branch in
   `resolve_slash_command()`. A fact it reports is filled in
   `_build_slash_ctx()` from where it lives, never with a constant:
   `tests/test_gateway_slash_facts.py` fails on a key a command reads that
   nothing fills, or fills with a literal.
2. **Needs the chat's conversation or running turn:** an intercept in
   `agent_handler/graph.py`, before the resolver.
3. **Changes Kazma for everyone** (code, the model, configuration): add it
   to `changes_global_config()` in `slash_commands.py`, the admin gate's one
   rule (`tests/test_slash_admin_gate.py`).
4. **Either way:** a line in `_cmd_help()`, an entry in
   `BOT_MENU_COMMANDS` (the "/" menu Telegram shows) and a section on this
   page. `tests/test_slash_help_and_menu.py` fails when the three disagree,
   and when the `/help` block above is not what the code sends.

## Permissions

A chat app takes messages from the users its allowlist admits; an empty
allowlist admits everyone in the chat. Within that:

- A change that applies to every user and platform is admin-only: the
  model (`/config model`, or a pick in the `/model` menu), `/config memory`,
  `/config tools`, a personality switch, `/skill install` and `uninstall`,
  switching or cloning the active workspace (`/ide repo`), and the swarm's
  output routing (`/swarm config`). An admin is a user named in
  `KAZMA_GATEWAY_ADMINS` (user ids, or `platform:id` for one platform), which
  decides alone when it is set; otherwise a user on the platform's allowlist.
  With an empty allowlist and no `KAZMA_GATEWAY_ADMINS`, no one is.
- `/hitl` answers only a task you started, and `/session` takes over only
  your own seasons.
- `/yolo` and `/unrestricted` are refused where policy forbids them
  (`KAZMA_ALLOW_YOLO=0`, production).
- A danger tool a command runs (`/ide run`, `/ide edit`, …) still stops at
  the approval card.


