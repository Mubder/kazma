# Slash Commands Reference

Kazma's gateway intercepts slash commands and resolves them **instantly (<50ms)** without any LLM call. Commands that involve `kazma_core` tools (marked `[core]`) are processed by the agent's tool layer with minimal overhead.

---

## 🔄 Session Commands

### `/reset`

Clears the current conversation history. The agent forgets everything and starts fresh.

**Usage:**
```
/reset
```

**Response:**
```
🔄 Conversation has been reset. Starting fresh.
```

**Side effects:**
- All messages in the current thread are cleared from the agent's context.
- Memory items (RAG) are NOT cleared — only conversation history.
- Snapshot history is preserved (use `/replay clear` to purge snapshots).

**Required permissions:** None. Available to all users.

---

### `/undo`

Removes the last agent response from the chat. Pops the last user–bot exchange from the message tracker.

**Usage:**
```
/undo
```

**Response (success):**
```
🔄 Last response removed.
```

**Response (nothing to undo):**
```
📭 Nothing to undo — no recent responses.
```

**Side effects:**
- The dispatch tracker's last entry is popped — `/undo` on the same response twice returns "Nothing to undo."
- The platform-level message deletion depends on adapter support (Telegram: `deleteMessage`).

**Required permissions:** None.

---

### `/edit`

Replaces the last agent response with corrected text. Pops the old response and stores the new text.

**Usage:**
```
/edit The corrected response text goes here.
```

**Response (success):**
```
✏️ Last response edited to:

The corrected response text goes here.
```

**Response (missing text):**
```
✏️ Usage: `/edit <corrected text>` — provide the new text.
```

**Response (nothing to edit):**
```
📭 Nothing to edit — no recent responses.
```

**Side effects:**
- The message tracker pops the last entry.
- On Telegram, uses `editMessageText` for in-place editing if the adapter supports it.

**Required permissions:** None.

---

### `/replay`

Time-travel debugging: list snapshots, replay from a specific iteration, compare two runs, or clear snapshot history.

Sub-commands:

| Command | Description |
|:---|:---|
| `/replay list` | Show all snapshots for the current thread |
| `/replay <N>` | Replay from iteration N |
| `/replay compare <A> <B>` | Diff two replay runs |
| `/replay clear` | Purge all snapshots for this thread |

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

**Dependency:** Requires `kazma_core.time_travel.ReplayEngine`. If not available:
```
⏳ Time travel not yet available.
```

**Required permissions:** None.

---

## 🔧 Tool Commands [core]

These commands are processed through the agent's tool layer (`kazma_core.tools`) rather than the gateway slash router. They still resolve quickly but involve the core.

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

**Priority chain:** Runtime override > `kazma.yaml: agent.personality` > `KAZMA_PERSONALITY` env var > `default`.

**Required permissions:** None.

---

### `/context`

Shows current context window usage: token count, percentage, and summarization threshold status. Optionally shows a breakdown by message role.

**Usage:**
```
/context
/context detailed
```

**Response:**
```
📊 Context Window
Tokens: 2,481 / 16,000 (16%)
Summarization threshold: 4,000 tokens (62% utilized)
```

**Response (`/context detailed`):**
```
📊 Context Window
Tokens: 2,481 / 16,000 (16%)
Role breakdown: user=1,250, assistant=980, tool=251
Summarization threshold: 4,000 tokens (62% utilized)
```

**Threshold:** Auto-summarization triggers when token count exceeds 4,000 tokens (`TOKEN_THRESHOLD` in `kazma_core.summarizer`).

**Required permissions:** None.

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
• `/reset` — Clear conversation history
• `/undo` — Remove last agent response
• `/edit <text>` — Correct last agent response
• `/replay list` — Show available snapshots
• `/replay <iteration>` — Replay from iteration
• `/replay compare <a> <b>` — Compare two runs
• `/replay clear` — Clear snapshots for this thread

🔧 *Tools*
• `/personality` — Show current personality
• `/personality list` — List all available personalities
• `/personality <name>` — Switch personality
• `/context` — Show context window usage

ℹ️ *Info*
• `/help` — Show this list
• `/status` — Gateway health overview
• `/model` — Show active model
• `/memory` — Report memory usage
• `/cost` — Token spend this session

For anything else, just ask the agent directly!
```

**Required permissions:** None.

---

### `/status`

Returns the gateway's current health overview.

**Usage:**
```
/status
```

**Response:**
```
*Gateway Status*
● Gateway: **running**
• Adapters: `telegram`
• Queue depth: `0`
• Active threads: `1`
```

The first character is a unicode circle: `●` (U+25CF) for running, `○` (U+25CB) for stopped.

**Context keys:** `started`, `adapters`, `queue_depth`, `active_threads` — all populated by the `GatewayManager`.

**Required permissions:** None.

---

### `/model`

Shows the currently active model.

**Usage:**
```
/model
```

**Response:**
```
🧠 Active model: **deepseek-chat**
```

**Context key:** `model` — set by the gateway at dispatch time from the active `ModelRouter` configuration.

**Required permissions:** None.

---

### `/memory`

Reports the number of facts stored in the agent's vector memory (RAG).

**Usage:**
```
/memory
```

**Response:**
```
💾 Memory: `42` stored facts.
```

**Context key:** `memory_count` — populated from `VectorMemory.count()`.

**Required permissions:** None.

---

### `/cost`

Shows the accumulated token spend and cost for the current session.

**Usage:**
```
/cost
```

**Response:**
```
💰 Session cost: $0.0234 (2,481 tokens)
```

**Context keys:** `total_tokens`, `total_cost` — tracked by the gateway's cost accounting layer.

**Required permissions:** None.

---

## Command Lifecycle

1. User sends text starting with `/`.
2. `MessageDispatcher.resolve()` calls `is_slash_command()`.
3. For gateway-handled commands: `resolve_slash_command()` returns the response instantly (<50ms).
4. For core-tool commands (`/personality`, `/context`): the dispatcher returns `None`, the message flows to the agent graph, and the tool layer processes it.
5. If no command matches, the text is passed to the LLM as normal.

## Adding a New Slash Command

1. **Gateway-level** (no LLM call needed): Add a handler in `kazma_gateway/slash_commands.py`:
   - Add a `_cmd_<name>()` function.
   - Register it in `resolve_slash_command()`.
   - Add it to `_cmd_help()` output.
2. **Core-level** (needs tool access): Add a handler in `kazma_core/tools/` and register it in the tool registry.

## Permissions

All slash commands listed here require **no special permissions**. They are available to every user in every chat. For tool-level access control (HITL gated tools), see `kazma_core/permissions.py`.
