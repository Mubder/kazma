---
id: engineering
title: Engineering inside Kazma
sidebar_label: Engineering inside Kazma
description: What Kazma builds itself on top of LangGraph and its libraries, the problem behind each piece, where it lives in the code, and the test that holds it
---

# Engineering inside Kazma

Kazma uses LangGraph for its agent loop and ordinary libraries for the rest
(FastAPI, httpx, SQLite, Postgres, Chroma, Playwright). This page is about the
parts it builds itself. Most of them exist because something went wrong on a
real install, so each section says what the problem was, what Kazma does
about it, where the code lives, and which test proves it. Each of those tests
is also run against the old behaviour, to show it fails there: a check that
has never failed proves nothing.

For the path one message takes through all of this, see
[How a turn works](how-a-turn-works.md). For the Arabic work, see
[How Kazma handles Arabic](arabic-engineering.md).

## 1. One approval system, three ways to pause

**The problem.** An agent can pause for approval in three places: a chat turn,
a swarm worker, and a step of a multi-step pipeline. Each had its own idea of
whether a question was answered. A retried Approve click, sent after the
agent had moved on to its next question, approved a question the person had
never seen. A card could also show "Approved" for work that was never
resumed.

**What Kazma does.** Every approval, on every path, is one row in a gate
registry with a strict state machine: `pending`, `claimed`, `resuming`,
`settled` (plus `timeout`, `superseded` and `error`). Every change is a single
compare-and-set; a click that loses the race gets a 409 with the row's real
state. A decision names the exact question it answers (LangGraph's interrupt
id), so it can only resume that question. The registry decides *whether* a
question was answered; the LangGraph checkpoint says *whether the graph is
really paused*. The pages draw what those two say; they never guess.

A tool nobody has classified is gated by default, and allowlisting a program
in the shell does not allowlist what it can run (`find -exec`,
`git -c core.fsmonitor=...` are refused).

**Where.** `kazma_core/safety/hitl_gates.py` (the registry),
`kazma_core/agent/graph_tool_worker.py` (the chat pause),
`kazma_core/swarm/shared_approvals.py` (one shared decision across Telegram,
Discord, Slack and the web for swarm work), `kazma_core/safety/hitl.py` (tiers).

**Proof.** `tests/test_hitl_gates.py`, `tests/test_hitl_gate_reconciler.py`,
`tests/test_hitl_wiring.py`, and `test_every_registered_tool_has_a_tier` in
`tests/test_static_gates.py`.

## 2. Check the plan before it acts

**The problem.** A model asked to "remind me the day before my exam" invented
a date and scheduled it. A model asked to post a set of drafts posted text that
was no longer the draft the user approved. Approval cards catch some of this,
but only if the person reads closely.

**What Kazma does.** A commitment layer sits between the model and anything
lasting. Before a tool runs, `authorize_effect` resolves the request against
what Kazma knows. It anchors a relative date to the memory it refers to and
rewrites the tool's arguments to that date, in the operator's time zone. It
refuses a catastrophic shell command before any approval card appears. It
refuses the agent's changes to protected settings (`safety.*`, the commitment
layer itself). When the operator has set an allowlist of targets, it asks which
target was meant if an outbound message names an unknown one. An X post must name a saved draft, and the gate replaces the
model's text with the stored draft, so what was approved is what publishes. A
fact the user stated cannot be overwritten by a lower-trust source, such as a
fact the model inferred.

**Where.** `kazma_core/safety/commitment/authorize.py`,
`kazma_core/safety/commitment/relative_time.py`,
`kazma_core/safety/side_effects.py` (what each tool can change),
`kazma_core/memory/provenance.py` (the trust ladder for facts).
[The commitment layer](commitment-layer.md) has the full design.

**Proof.** `tests/test_commitment_authorize.py`,
`tests/test_x_post_proposal_gate.py`, `tests/test_memory_provenance.py`.

## 3. Fail honestly

**The problem.** When a model call failed after retries, the old final step
still asked the model to "summarize what happened", and it produced a
plausible answer over a turn that had done nothing. Users saw confident replies
to work that never ran.

**What Kazma does.** Every model failure is classified: network errors and
rate limits are *transient* and retried, while content and schema errors are
*permanent* and fail fast. A turn whose model call fails sets `turn_failed`,
and the final step skips its summary entirely. The user sees a ⚠️ message with
a hint, never a made-up answer. When Kazma falls back to another model, it
says so. When a thinking model spends its whole reply on hidden reasoning, the
truncation warning names that, and X's structured calls turn the reasoning
off.

**Where.** `kazma_core/llm_provider.py` (`LLMError` with `transient`, the
truncation detail, the reasoning switch),
`kazma_core/agent/graph_supervisor.py` (the retry loop),
`kazma_core/agent/graph_respond.py` (the `turn_failed` guard).

**Proof.** `tests/test_retry_backoff.py`, `tests/test_source_grounding.py`
(a failed turn still skips synthesis), `tests/test_x_reasoning_off.py`.

## 4. A receipt for every change, and no blind replays

**The problem.** If Kazma restarts in the middle of a tool call that sends
an email or writes a file, a naive retry sends it again. Not retrying can lose
the work. Neither is acceptable for actions outside Kazma.

**What Kazma does.** Before a tool that changes anything runs, the effect
journal records a receipt naming the chat, the turn, the call and a hash of
its arguments. The result is written before the graph moves on. On a replay, a
finished call returns its recorded result. A call that started but never
finished is held as "effect unknown": the turn stops with an honest warning
instead of running it again. Tools mark failures that happened *before* they
did anything (`RefusedBeforeEffect`), so the model can correct those and try
again. A test reads every tool's body to check that refusals are marked this
way.

**Where.** `kazma_core/agent/effect_journal.py`,
`kazma_core/agent/tool_refusal.py`.

**Proof.** `tests/test_effect_journal.py` (a real child process killed
mid-call), `tests/test_tools_refuse_before_effect.py`.

## 5. Memory that only answers when it knows

**The problem.** The first memory always injected its top five memories,
whatever the question. On the retrieval benchmark, three of every four facts it
injected were noise, and questions with no answer still got "relevant"
memories.

**What Kazma does.** Recall scores each candidate on two things: how much
closer it is in meaning to the question than the question's background, and
how many of the question's
content words it holds. The background is the average similarity of the
candidates ranked 4th to 15th, because an embedding model puts almost
everything a little similar. Below a floor, nothing is injected. Behind the best
candidate by more than a gap, nothing is injected either. Meaning search
compares every stored memory exactly, with no partial index. Facts are
bi-temporal: they record when a fact became true and when it was written, so
an old statement read late cannot overwrite a newer one. Forgetting writes a
tombstone and a journal entry, so a restored backup forgets again, and no
background job brings a forgotten memory back.

The thresholds come from a versioned benchmark with held-out questions, and
its scores are a ratchet: they may rise, never fall.

**Where.** `kazma_core/memory/recall.py`, `kazma_core/memory/belief_mutation.py`,
`kazma_core/memory/forget.py`, `kazma_core/memory/forget_journal.py`,
`kazma_core/memory/benchmark.py`.
[Memory](memory-and-rag.md) has the full design.

**Proof.** `tests/test_memory_relevance.py` (swaps in the old ranking and
every recall path must break), `tests/test_memory_benchmark.py`,
`tests/test_memory_forget_converges.py`.

## 6. Untrusted text is fenced, and the fence is measured

**The problem.** Web pages, search results, documents, emails and recalled
memory can all carry instructions written by someone else ("ignore your rules
and..."). A model reading them raw may follow them.

**What Kazma does.** Every tool that returns outside text wraps it in a fence
that marks it as data, not instructions. A test fails the build when a web tool
returns text unfenced. The effect is measured rather than assumed. On Kazma's
own attack corpus it cut attack success by 33 to 42 points on the three models
the attacks worked on (two others resisted them with or without it). On the
public AgentDojo benchmark it clearly beats no defense, but it cannot be told
apart from a four-character delimiter. Both results are published.

**Where.** `kazma_core/safety/prompt_fence.py`, `scripts/injection_report.py`,
[Prompt injection: the numbers](https://github.com/Mubder/kazma/blob/main/docs/INJECTION.md).

**Proof.** `tests/test_injection_containment.py`,
`test_no_unfenced_web_tool_output` in `tests/test_static_gates.py`.

## 7. Delivery you can reload

**The problem.** A chat answer streams over many small messages. Behind a
proxy that splits them differently, the page dropped tool rows and approval
cards. A reload in the middle of a turn painted the turn twice or not at all.

**What Kazma does.** Every turn is a journal of numbered events, and the page
draws from that journal. It never writes a turn's state itself. Exactly one
function decides whether a turn is open, waiting on an approval or done
(`close_turn`). Its result is saved before the browser is told. A reload
resumes from the last event the page read. The stream reader keeps a
half-received message until it is complete, however the network split it.

**Where.** `kazma_ui/turn_runtime.py`, `kazma_ui/static/js/streaming.js`,
`kazma_ui/static/js/modules/turn_document.js`.

**Proof.** `tests/test_turn_runtime.py`, `tests/js/test_sse_parser.js` (every
split position), `tests/e2e/test_chunked_stream_browser.py` (a real browser
behind a proxy that splits every message into 1-40 byte pieces).

## 8. Every task is watched

**The problem.** Kazma runs work that outlives a message: swarm tasks,
scheduled jobs, documents, research, crawls, X posts, memory upkeep. When one
stalled, nothing said so.

**What Kazma does.** One supervisor reads every kind of work from its own
store without writing to it. It judges each by its progress, not by whether
its process is alive, and opens an incident when one stalls, gets stuck or
fails. The incident arrives as a card with buttons in the chat the work
started from. The card is recorded with its delivery state before it is sent:
a restart never sends it twice, and a card that was never sent goes out at the
next start. The first decision wins on every surface.

**Where.** `kazma_core/runs/` (`supervisor.py`, `kinds.py`, `incidents.py`).
[Running work](running-work.md) has the user's view.

**Proof.** `tests/test_run_kinds_declared.py` (every store with a status
column is watched or declared), `tests/test_run_incident_crash.py` (a real
process killed at each stage), `tests/test_run_decisions.py`.

## 9. Fetching the web without being turned against the host

**The problem.** A page Kazma fetches can redirect to an internal address, or
use a DNS answer that changes between the safety check and the connection,
to reach services on the host's own network.

**What Kazma does.** Every address is checked in an isolated resolver
process with time and memory limits. The connection is pinned to the checked
address while keeping the real host name, and the peer is checked again after
connecting. The browser's requests are intercepted one by one, including
redirect hops, and fetched through the same checks. Automated crawls share
each site's rate limits and follow its robots rules.

**Where.** `kazma_core/security/ssrf.py`, `kazma_core/security/ssrf_pin.py`,
`kazma_core/web_acquire/dns_worker.py`,
`kazma_core/web_acquire/browser_network.py`.
[Web research](web-research.md) has the details.

**Proof.** `tests/test_ssrf_pin_authority.py`, `tests/test_acquisition_dns.py`,
`tests/test_browser_acquisition_broker.py`.

## 10. Hostile documents stay outside the server

**The problem.** A PDF or Office file can carry scripts, outside actions, or
a parser exploit. Parsing it inside the server process trusts it with
everything the server can touch.

**What Kazma does.** Documents are parsed in a separate worker with memory
and CPU limits. On request, each job runs in its own container with no network
and no mounted folders. The container is started by a broker, the only process
that holds Docker access. A PDF policy walks the file's object graph and
refuses scripts, outside actions, attachments and links to files or network
shares, before parsing and after every change Kazma makes.

**Where.** `kazma_core/documents/pdf_policy.py`,
`kazma_core/documents/worker_broker.py`.
[Document security](../security/document-security.md) has the threat model.

**Proof.** `tests/test_document_pdf_policy.py`,
`tests/test_document_worker_container.py`.

## 11. Secrets never leave through a side door

**The problem.** A program a tool starts (a test runner, a package install,
a git hook) inherits the server's environment, with its keys, unless something
stops it.

**What Kazma does.** Secrets live in an encrypted vault (AES-256-GCM).
Programs started by tools get a cleaned environment with no Kazma variables,
nothing named like a credential, and no URL carrying a password. Masking works
by key name and by value, so a password inside a database URL is masked too. A
test finds every place a tool, the IDE, a skill or the chat apps start a
process, and checks where its environment comes from.

**Where.** `kazma_core/security/vault.py`, `kazma_core/security/child_env.py`.

**Proof.** `tests/test_child_env.py` (runs real pytest and a real git hook),
`tests/test_url_credentials.py`.

## 12. A supervisor that knows a restart from a reload

**The problem.** A process manager restarts a server that stops answering.
But a server waiting out a database restart is not broken. Restarting it
cannot bring the database back, and only adds an outage.

**What Kazma does.** `kazma_guard.py` runs Kazma and reads its health answers.
No answer three times in a row means a restart. An answer that says "not
ready: the database is down" means waiting, with one page to the operator, and
a restart only after ten minutes. A reload is graceful: the guard asks Kazma to
stop, waits for chat turns to finish (`--when-idle`), and starts the new code.
It writes a heartbeat, so Kazma pages the operator if its guard ever dies, and
it keeps the server's last words when it crashes.

**Where.** [scripts/service/kazma_guard.py](https://github.com/Mubder/kazma/blob/main/scripts/service/kazma_guard.py).

**Proof.** `tests/test_guard_integration.py` (the real guard against a fake
server: graceful reload, a database outage ridden out, a crash's last lines
kept), `tests/test_guard_owns_reload.py`.

## How fixes are made to last

Every fix in this list ships with three things. A test reproduces the
original failure. A *class gate* finds every sibling of the broken code from the
source itself (every tool, every store, every route), so the next one cannot
repeat the mistake. A *negative control* runs that gate against the old code to
show it fails there. `tests/test_store_registry.py`, `tests/test_static_gates.py`
and `tests/test_child_env.py` are examples. The full list of open weaknesses is
[Known gaps](https://github.com/Mubder/kazma/blob/main/docs/KNOWN_GAPS.md),
dated.
