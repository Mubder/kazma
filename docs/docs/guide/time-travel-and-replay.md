---
id: time-travel-and-replay
title: Time travel, replay and forking
sidebar_label: Time travel & replay
description: Snapshots of the agent's state after every step, and how to rewind a conversation, branch it, or compare two points
---

# Time travel, replay and forking

Kazma records the agent's full state after every step of a turn (each
supervisor iteration), so a conversation can be looked at, rewound or branched
from any of those points. The code is `kazma_core/time_travel.py`.

## What is recorded

After each supervisor iteration, `SnapshotRecorder.capture` stores one
snapshot: the thread, the iteration number, the time, the model that answered,
and the whole agent state as JSON (messages, plan, routing, cost). Snapshots
live in `snapshots.db` in the data folder. The chat stream announces each new
one with a `snapshot` event.

## Using it

**In any chat** (web, Telegram, Discord, Slack):

| Command | What it does |
|---|---|
| `/replay list` | The snapshots of this conversation |
| `/replay <N>` | **Restore**: rewind this conversation to iteration N. Later turns are gone; use `/fork` to keep them |
| `/fork <N>` | **Fork**: copy iteration N into a new conversation. The original is not touched; the fork appears in the web sidebar |
| `/replay compare <A> <B>` | What differs between two snapshots |
| `/replay clear` | Delete this conversation's snapshots |

**In the web UI**: the **Time Travel** page (`/replay`) lists your
conversations and their snapshots, with Restore, Fork and Compare.

**Over HTTP**, for your own conversations only (each route checks that the
thread is yours):

| Route | What it does |
|---|---|
| `GET /api/replay/threads` | Conversations that have snapshots |
| `GET /api/replay/snapshots/{thread_id}` | One conversation's snapshots |
| `GET /api/replay/snapshots/{thread_id}/{iteration}` | One snapshot |
| `POST /api/replay/restore` | Rewind a conversation |
| `POST /api/replay/fork` | Branch into a new conversation |
| `POST /api/replay/compare` | Compare two snapshots |
| `DELETE /api/replay/threads/{thread_id}` | Delete a conversation's snapshots |

A comparison reports the message count, the iteration, the model, the next
step the agent would take, the tool calls and the cost of each side, and what
changed between them.

## What a rewind does not undo

Restoring rewinds the **conversation**, not the world. Files the agent wrote,
messages it sent, posts it published and commands it ran stay done. Look at
the tool calls after the point you rewind to before you ask the agent to redo
them.

When a snapshot is written back into a conversation, the chat-platform ids
(`chat_id`, `user_id`, `message_id`) are dropped from it: they never belong in
the agent's state.

## Storage and settings

| Setting (`time_travel.*`) | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Record snapshots at all |
| `max_snapshots` | `50` | Newest snapshots kept per conversation |
| `max_global_snapshots` | `2000` | Newest snapshots kept in total |
| `auto_maintain` | `true` | Run the daily clean-up |
| `retention_days` | `30` | The clean-up deletes snapshots older than this, then compacts the file |

The clean-up runs once a day (first pass two minutes after start) and reads
its two settings live, so a change in Settings needs no restart;
`max_snapshots` is read when the recorder is built.

Snapshots are separate from the chat's step history (LangGraph checkpoints,
which have their own retention: Settings → System → Chat step history).
