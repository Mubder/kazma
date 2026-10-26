---
id: running-work
title: Running work and incidents
sidebar_label: Running work
description: How Kazma watches every task it runs, tells you when one stalls, gets stuck or fails, and lets you act from the message
---

# Running work and incidents

Kazma runs many kinds of work that outlive one message: chat turns, swarm
tasks, scheduled jobs, document jobs, research sessions, crawls and research
pipelines, X posts, memory tasks and maintenance sweeps. The run supervisor
watches all of them in one place. When a piece of work stalls, gets stuck or
fails, Kazma opens an **incident** and tells you in the chat the work started
from, with buttons to act on it.

## What counts as stuck

Each kind is judged by its progress, not only by whether its process is alive:

| Verdict | Means |
|:---|:---|
| **Stalled** | The work's owner is alive, but nothing meaningful changed within the kind's budget |
| **Stuck** | Its owner is gone: no task runs it, its lease lapsed, or its process died |
| **Failed** | The kind's own store says it ended in failure |
| **Slow** | Still progressing, past its expected duration (shown, never paged) |

The budgets: a chat turn 10 minutes without a step, a swarm task or a
document job 10 minutes, research sessions and crawls 3 minutes (the research
monitor's). Work that waits for you, such as an approval card or a post held
for review, is never stalled. A verdict never cancels or fails anything by
itself.

## Where you are told

- **In the chat the work started from**, as a card with one button per
  action, on Telegram, Discord and Slack. Work with no chat (a background
  memory task, a maintenance sweep) goes to your ops channels.
- **On the Dashboard**, in the Running work card, with the same buttons, and
  in the notification bell, which links to the incident.
- **In any chat**, `/runs` lists what needs attention and what runs; see
  [`/runs`](../reference/slash-commands.md#runs).

You get one message per piece of work and condition, and one note when a
stalled piece moves again. Some conditions already have their own message:
scheduled jobs' failures (their alert outbox), research and crawl stalls and
outcomes (the research monitor), X post failures (the publication notices) and
memory dead letters. Kazma records those incidents, with their buttons on the
Dashboard and in `/runs`, without a second message.

## What the buttons do

| Button | Does |
|:---|:---|
| Cancel | The kind's own cancel: the Swarm page's, the Scheduled page's, the Documents page's, the web chat's Abort |
| Retry | Runs the work again through its page's Retry |
| Retry (I checked its effects) | The only retry offered when the work's effects outside Kazma are unknown (it may have sent or written something). You say you checked first |
| Close the turn | Ends a chat turn left open with nothing running it, so the chat can go on |
| Dismiss | Leave the work as it is. The same failure is not reported again |
| Ask Kazma to diagnose | Kazma looks at the work with read-only tools and answers in a few sentences: what happened, the likely cause, the safest next step. It does nothing else |

A button acts through the same function the kind's own page uses, with the
same checks. The first decision wins everywhere: a second press, on another
chat or on the Dashboard, is told what was decided, by whom and where.
Pressing a button on work that has moved on since does nothing and says so.
Acting is admin-only.

Retry, Cancel, Close the turn and Dismiss decide the incident: on Telegram
and Discord the card's buttons go once one is pressed (Slack keeps them, and
a later press is told what was decided). Ask Kazma to diagnose decides
nothing: the buttons stay on the card, and the diagnosis arrives below it, so
you can read it and then retry or dismiss from the same card.

In `/runs`, work you already decided about (retried, dismissed) no longer
counts as needing attention; `/runs all` lists it, under what finished in the
last day, as dealt with.

## Try it

To see a card without waiting for a real failure, ask Kazma in Telegram,
Discord or Slack to run a swarm task with a time limit too short to finish:

> Use the swarm to research the history of kazma.ai in depth, with a 5-second timeout.

The task times out (`dispatch_swarm` takes the limit you ask for, from 5
seconds to 30 minutes; 5 minutes when you name none), and within about 30
seconds a card arrives in the same chat with Retry, Dismiss and Ask Kazma to
diagnose. A retried task gets the same limit, so it fails again and sends a
second card; dismiss it when you are done.

## Automatic first steps

On the Dashboard's Running work card, under **Automatic first steps**, you can
let Kazma take one step before asking you, per kind of problem:

- retry a failed swarm task that changed nothing outside Kazma;
- retry a document job that ran out of attempts;
- retry a memory task that ran out of attempts;
- close a chat turn left open with nothing running it.

Each is off until you switch it on. Kazma takes at most one such step per piece
of work a day (a retry that fails again is not retried again by itself), never
on work whose effects are unknown, and then tells you what it did. The setting
is `runs.runbooks`.

## If something goes wrong with the supervisor

The supervisor checks every 30 seconds. If it cannot check for five minutes
(its database is away, for example), you get one "Run supervision is not
working" message, and one more when it works again. A kind whose store cannot
be read is shown as unreadable, never as "nothing running". A Kazma restart
in the middle of sending a message never sends it twice: the incident records
that the message may not have arrived. A restart in the middle of an action
never repeats it: the incident records that the action was interrupted.

## Where it lives

The read model, the verdicts, the incidents and the decisions are in
`kazma_core/runs/` (`contract.py`, `kinds.py`, `registry.py`,
`supervisor.py`, `incidents.py`, `decide.py`, `runbooks.py`, `diagnose.py`).
Each kind's adapter reads its own store and never writes it. Incidents are in
`run_incidents.db` in the data folder. The plan, with its phases and gates, is
`docs/plans/RUN_SUPERVISION_PLAN.md`.
