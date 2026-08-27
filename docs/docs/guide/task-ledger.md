---
id: task-ledger
title: Task Ledger
sidebar_label: Task Ledger
description: Durable task state that intent resolves against — continuation binding, structural clarify, and git-write blast-radius limits (2026-08)
---

# Task Ledger

> **Born from a live incident (2026-08-27).** The user replied
> *"proceed with next"* to a running brand-name sweep. The turn's transcript
> had just been truncated to a 158-char fragment by a mid-stream stop, so
> the loudest signal left in the prompt was the ambient workspace
> ("uncommitted changes…") — and the model resolved "next" to a
> `git commit` of unrelated config. No approval card appeared because a
> 4-hour YOLO window was still active.
>
> The ledger makes that entire class structurally impossible: **short
> continuations resolve against durable task state, never the transcript;
> unresolved continuations can only produce a question; and repo mutations
> always gate, even under YOLO.**

## What it is

One **active ledger per conversation**, stored in its own SQLite database
(`kazma-data/task_ledgers.db`, WAL) — never inside the chat transcript:

| Field | Meaning |
|-------|---------|
| `goal` | The mission in one sentence (seeded from your first ≥40-char message; the agent refines it via the tool). |
| `steps` | The declared plan — each step with `pending / running / done / failed` status and a one-line result. |
| `next_action` | **The binding target**: the agent's own declared next step. |
| `findings` | Durable results worth keeping (e.g. the green-names list). |
| `open_questions` | Unresolved forks to ask the user about. |

Because it lives in SQLite, it survives restarts, browser refreshes,
truncated replies, and new sessions — the resolution surface cannot be
corrupted by transcript bugs.

## How it stays current

Two writers, no extra LLM calls required:

1. **Deterministic extraction** — every assistant reply's
   ```` ```plan ```` fence becomes the ledger's steps, and its closing
   declared-intent line — *"Now the social sweep for X, Y, Z"*, *"Next:
   …"*, *"التالي: …"* — becomes `next_action` (English and Arabic
   patterns; the last declaration wins, since the agent re-declares as it
   re-plans).
2. **The `task_ledger_update` tool** — the agent maintains goal, next
   step, findings, and step status deliberately. Maintaining the ledger is
   how it makes "next" unambiguous.

## How intent resolves

When your message is a short continuation ("proceed", "next", "continue",
كمّل، تابع), the supervisor consults the ledger **before** the turn runs:

- **Declared next action exists → BINDING.** The turn context states
  exactly which step "next" means — *"CONTINUATION BINDING: this means
  'the social sweep for potensfit, acerfit, voimfit'"* — with an explicit
  escape clause so a genuinely-new task still overrides it.
- **No declared next action → STRUCTURAL CLARIFY.** The turn is locked to
  a single clarifying question and **all tools are removed for that
  turn** — the agent physically cannot act on a guess. A misread costs
  one question, never an action.
- **Topic shift → SUPERSEDE.** A detected pivot marks the old ledger
  superseded (kept for history) and a fresh one seeds automatically.

## Blast radius

Two hard limits so even a genuine misunderstanding stays cheap:

- **Git write commands always gate.** `git commit`, `push`, `merge`,
  `rebase`, `reset`, `checkout --`, `restore`, `clean`, `rm`, and friends
  require an approval card on **every** execution path (graph tool worker
  and swarm/IDE registry) — **YOLO cannot auto-approve them**. Read-only
  git (`status`, `log`, `diff`) is exempt.
- **YOLO windows are 1 hour by default** (was 4). Override with
  `KAZMA_YOLO_TTL_SECONDS` (`0`/`off` = no expiry) if you truly need
  longer.

## Operating notes

- The store is best-effort and never raises — a broken ledger degrades to
  "no ledger", never a failed turn.
- Ledgers are per-conversation thread; history of superseded ledgers is
  retained per thread.
- Wiring lives in `kazma_core/agent/task_ledger.py` and the supervisor
  (`graph_supervisor.py`); tests in `tests/test_task_ledger.py`.
- Related: [Commitment layer](./commitment-layer) (semantic gates) and
  [Security & safety](./security-and-safety) (HITL tiers).
