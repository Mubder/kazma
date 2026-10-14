---
id: time-travel-and-replay
title: Time travel, replay and forking
sidebar_label: Time travel & replay
description: Saved steps of the agent's state, and how to restore a conversation to one, branch it, or compare two
---

# Time travel, replay and forking

Kazma saves the agent's full state after steps of a turn (supervisor
iterations), so a conversation can be looked at, restored or branched from
any of those points. The code is `kazma_core/time_travel.py` (the steps) and
`kazma_ui/thread_rewind.py` (restore and fork, one service for every
surface).

## What is saved

After a supervisor iteration, `SnapshotRecorder.capture` stores one step: its
id, the chat, its turn (`turn_index`, the questions it holds), the iteration,
the time, the model, and the whole agent state as JSON (messages, plan,
routing, cost). Steps live in `snapshots.db` in the data folder and are read
straight from it; nothing is kept in memory. The chat stream announces each
new one with a `snapshot` event.

A step is named by its **id**. The iteration number restarts at 0 on every
turn, so it is shown as "turn 2, step 1", never used as an address.

In a long turn not every step is kept: every step up to the 20th, then every
10th, and always the turn's last. A state larger than 2 MB is stored with its
long texts cut and marked so: it can be read, but not restored or forked.

## Using it

**On the Time Travel page** (`/replay`): pick a conversation; its steps are
listed turn by turn. Open a step to read its messages, then **Restore here**,
**Fork**, or compare two steps on the Compare tab. A fork opens the new chat
when you confirm.

**In a chat app** (Telegram, Discord, Slack):

| Command | What it does |
|---|---|
| `/replay list` | This chat's saved steps, turn by turn, with their ids |
| `/replay <step>` | **Restore** this chat to that step (the first eight characters of its id are enough) |
| `/fork <step>` | **Fork**: branch that step into a new chat, owned by you. The original is not touched; the fork appears in the web sidebar |
| `/replay compare <a> <b>` | What differs between two steps |
| `/replay clear` | Delete this chat's saved steps |

**In the web chat**, `/replay` and `/fork` answer without the model: how many
steps were saved and a link that opens the Time Travel page on this chat.
`/replay clear` deletes this chat's steps. Restore, fork and compare are on
the page, where a step can be read before it is used.

**In the terminal UI**, `/replay list`, `/replay <step>`, `/replay show
<step>`, `/replay clear` and `/fork <step>` use the HTTP routes below.

**Over HTTP**, for your own conversations only (each route checks that the
thread is yours):

| Route | What it does |
|---|---|
| `GET /api/replay/threads` | Conversations that have steps |
| `GET /api/replay/snapshots/{thread_id}` | One conversation's steps: `snapshots` (in capture order) and `turns` (each turn once, its steps numbered) |
| `GET /api/replay/snapshots/{thread_id}/{snapshot_id}` | One step, with its messages |
| `POST /api/replay/restore` | Restore a conversation to a step: `{"thread_id", "snapshot_id"}` |
| `POST /api/replay/fork` | Branch a step into a new conversation: `{"thread_id", "snapshot_id"}` |
| `POST /api/replay/compare` | Compare two steps: `{"thread_id", "a", "b"}` (snapshot ids) |
| `DELETE /api/replay/threads/{thread_id}` | Delete a conversation's steps |

A restore or a fork that cannot happen answers with a code: 409
`turn_running` (a turn is running in the chat), 409 `approval_pending` (an
approval is waiting; the answer names the cards in `gates`), 409
`snapshot_truncated`, 404 `not_found`, 503 `store_unavailable` (the step
store cannot be read; a list never answers "no steps" for that).

## What a restore does

A restore is one operation, the same on every surface:

1. It is refused while a turn runs in the chat, and while an approval is
   waiting there. Answer the approval (or let it expire) first; nothing is
   decided behind your back.
2. It writes the step as a finished turn: nothing pending, nothing that runs
   on its own, and the step's scratchpad in place of the current one.
3. It takes the later turns back: from the chat's transcript (the rows after
   the step), from what Kazma remembered of them (their memories and facts
   are forgotten), and from Time Travel itself (the steps saved after it).
4. Every open tab of the chat reloads it.
5. It is written to the audit trail (`time_travel_audit.db`: who, from which
   surface, which chat and step, and the checkpoint the chat was at before),
   and so is a refused one.

The next message continues from the restored step.

## What a restore does not undo

Restoring rewinds the **conversation**, not the world. Files the agent wrote,
messages it sent, posts it published and commands it ran stay done. Look at
the tool calls after the point you restore to before you ask the agent to redo
them.

When a step is written back into a conversation, the chat-platform ids
(`chat_id`, `user_id`, `message_id`) are dropped from it: they never belong in
the agent's state.

## Deleting and forgetting

- Deleting a chat deletes its steps (they are copies of the chat).
- Forgetting a memory deletes the chat's steps from that turn on (each step
  holds the conversation up to it); forgetting a whole chat ("Also forget
  what Kazma learned", or `/memory off`) deletes all of them.
- `/reset` keeps the steps; `/replay clear` deletes them.
- Steps are **not backed up** and do not travel in a migration bundle: a copy
  elsewhere would bring back the steps of a chat deleted or forgotten since.
  The chats, their checkpoints and the audit trail are backed up and travel.

## Storage and settings

| Setting (`time_travel.*`) | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Save steps at all |
| `max_snapshots` | `50` | Newest steps kept per conversation, by when they were saved |
| `max_global_snapshots` | `2000` | Newest steps kept in the whole database |
| `max_snapshot_bytes` | `2097152` | Largest step stored whole; a larger one is stored cut |
| `max_thread_bytes` | `33554432` | Bytes of steps kept per conversation, oldest dropped first |
| `max_total_bytes` | `536870912` | Bytes of steps kept in the whole database |
| `max_per_turn` | `30` | Steps kept per turn: its first and its newest |
| `auto_maintain` | `true` | Run the daily clean-up |
| `retention_days` | `30` | The clean-up deletes steps older than this |

The budgets are checked in the database on every save. The clean-up runs once
a day (first pass two minutes after start), reads its two settings live, and
compacts the file only when at least a quarter of it, and 8 MB, is free.
A clean-up that did not remove what it deleted raises an operator alert.
The other settings are read when the server starts (Settings shows the
running value and says when a restart is needed).

`kazma_time_travel_capture_total{result}` counts each step by what happened
to it (`stored`, `truncated`, `skipped`, `failed`): a step whose save failed
is not announced. `kazma_time_travel_rewind_total{action,outcome}` counts
restores and forks.

Steps are separate from the chat's step history (LangGraph checkpoints,
which have their own retention: Settings → System → Chat step history).
