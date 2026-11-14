---
sidebar_position: 5
---

# Safety design

> This page is published at [kazma.ai/docs/security/safety-design/](https://kazma.ai/docs/security/safety-design/) ([Arabic](https://kazma.ai/ar/docs/security/safety-design/)).

Kazma runs a language model that can run code, change files, send messages
and spend money on your behalf. This page describes how its safety is built:
where each decision is made, what checks it, and how the project keeps the
checks honest. It is written for people who want to understand the design,
and for researchers who want to test it.

What each mechanism does **not** stop is on its own page, the
[threat model](https://kazma.ai/docs/security/threat-model/), which is meant
to be read alongside this one. In short: Kazma protects one operator from the
agent -- its mistakes, and the untrusted pages, documents and messages it
reads. It does not protect an install from its own operator, and it is not a
boundary between people who do not trust each other. No independent security
review of Kazma has been done yet.

## The principles

1. **One decision, one place.** Each safety question has one answer in the
   code: one list of dangerous tools, one registry of approval decisions, one
   rule for which files are Kazma's own, one masker of secrets. A second copy
   of a decision drifts, and the drifted copy is where the hole is.
2. **Closed by default.** A tool nobody classified asks for approval. An API
   route is signed-in only unless it is listed as open. A chat app answers
   nobody until its owner is known. A policy engine that fails refuses.
3. **Never pretend.** A failed step is reported as failed, never as an
   answer: a turn whose model call failed says so instead of writing a
   plausible reply over it, and an action whose outcome is unknown is never
   repeated as if it had not happened.
4. **Every safeguard is tested against its own absence.** Each fix lands
   with a test that fails on the old code (a *negative control*), and the
   classes of mistake that recur are held by *class gates* that read the
   whole codebase -- for example, every tool must have a risk tier, and every
   process a tool starts must get a scrubbed environment.

## 1. Asking before acting

Every tool has a risk tier: read, safe (reads private data), write, or
danger. Danger tools -- running commands, sending email, posting, deleting,
installing -- pause the turn and ask the operator, on the web or in the chat
app the turn came from. A tool with no tier is treated as dangerous.

- **One decision per question.** Approvals from every path (the chat graph,
  the swarm, pipelines, clarifying questions) are rows in one registry, each
  moving through its states by a compare-and-set. The first answer decides;
  every other card shows that answer. A decision names the exact question it
  answers, so a retried click cannot approve a later question the person
  never saw.
- **The card shows what will happen.** Every argument is shown (secrets
  hidden, the arguments' digest kept). An action that cannot run anyway --
  a shell command the policy refuses, a browser the machine cannot start --
  is refused before a card is shown.
- **Some things always ask.** Publishing to X and widening the folders a
  tool may read always ask, every time: no standing approval or approval-free
  window covers them, and their card offers "Approve once" only. A draft is
  published only after the person has seen it in an earlier turn, on a card
  that says it publishes.
- **Reading the web after reading untrusted text asks.** Once a turn has
  read outside text, a fetch of an address the person did not name and no
  search found asks first, so a page cannot quietly send your data to
  another site in a URL.
- **What ran without asking is reported.** Approval-free windows and
  standing approvals are allowed, and every call one of them let through is
  listed in the next day's digest.

## 2. Policy before effects

A policy layer sits between the model and anything durable, and it runs
before the approval card, so it can correct or refuse first:

- A reminder's time is resolved against what memory knows ("the day before
  my exam" is the stored exam date), and the tool's arguments are rewritten
  to it; a recurring request is never booked as a one-off.
- Catastrophic commands (`rm -rf /`, fork bombs, `curl | sh`, disk wipes) are
  refused outright, before any card.
- The agent cannot change its own safety settings: approval, commitment,
  secrets, MCP commands and hooks are protected configuration.
- Messages can be limited to an allowlist of recipients.
- A swarm worker runs with a narrower scope than the main agent, and a
  scheduled task is offered only the tools it declared when it was booked.

If the policy engine itself fails, the action is refused.

## 3. Untrusted text stays data

Everything the agent reads from outside -- web pages, search results,
documents, emails, MCP output, Knowledge Library text, even memories -- is
wrapped in a labelled data fence before the model sees it, telling the model
it is material to read, not instructions. The fence cannot be forged by the
text inside it, and chat-template role tokens are removed. The threat model
says how much this reduces attack success on real models (a reduction, not a
guarantee).

Around the fence:

- **What the user said outranks what the model inferred.** A fact you stated
  can only be replaced by your own words; a memory tool the model calls on
  its own writes facts at a lower trust, and text that tries to give
  instructions is refused before it is stored.
- **Lessons and memories come only from turns memory keeps.** A failed turn,
  or a chat you keep out of memory, teaches nothing.
- **A tool gets its arguments whole.** Nothing in the executor reinterprets
  an argument as a permission.

## 4. One record of every turn

- **Honest failure.** When the model call fails after its retries, the turn
  is marked failed and the reply says so; the final summarizing call is
  skipped, because writing a confident answer over a broken turn was exactly
  how failures used to look like success.
- **Effects are journaled.** A tool that changes something is recorded
  before it runs and its result after. If Kazma stops in between, the effect
  is *unknown*: it is reported, and never run again automatically. A tool
  that refuses before doing anything says so, and the turn goes on.
- **A restart does not lose a turn.** A turn cut off by a restart continues
  by itself when it is safe to, and waits for the person when an effect is
  unknown.

## 5. Kazma's own data is not the agent's

- **Kazma's stores and secret files are off limits to tools.** Every tool
  that touches a path -- file tools, the SQL tools, `python_exec`,
  `shell_exec`, research readers -- asks the same rule and refuses Kazma's
  databases, its `.env` files, the vault and backup copies, naming the tool
  that reads the data properly instead.
- **Processes Kazma starts get no secrets.** A command, a test run, an
  installer or a hook gets an environment without Kazma's variables,
  credentials or passwords in URLs, a home folder of its own, and no
  repository-chosen program runs during Kazma's own git reads.
- **Secrets live in the vault.** Settings, provider rows, MCP servers and
  backups hold pointers; every page and API masks secrets by name and by
  value (a password inside a URL); secrets reach other programs on stdin,
  never on a command line; offsite backups leave secret files out.

## 6. Risky work runs boxed in

- **Code.** `python_exec` runs in a Docker container with no network,
  read-only mounts, limits on memory and processes, and the snippet's own
  restricted builtins; a cloud microVM (E2B) is available for code that is
  genuinely untrusted. `shell_exec` runs only allowlisted programs, refuses
  their flags that start other programs, and keeps paths in the workspace.
- **Documents.** Parsing, OCR and rendering run in a separate worker
  process with time, memory and CPU limits, or in a per-job container with
  no network and a read-only root (the profile to use for documents you do
  not trust). PDF active content -- scripts, launch actions, embedded files,
  links that reach outside -- is refused inside the worker.
- **The web.** Every fetch resolves the name in a bounded worker, refuses
  private addresses, pins the connection to the checked address and checks
  the address it actually reached. The browser runs inside an operating
  system limit on memory, processes and CPU, with every request of the page
  (redirects and assets included) brokered through the same checks.
- **The swarm.** Handoffs between workers are bounded in depth and visits;
  a worker's text reaching another model is fenced; a store fault is never
  recorded as a success.

## 7. The doors into Kazma

- Every `/api` route requires sign-in unless listed as open; a session's
  role is enforced on every request; changes must come from Kazma's own
  pages (same-origin), and a request through a proxy never inherits the
  trust of a local one.
- Pages load scripts only from Kazma itself and connect only to Kazma (and
  a configured LiveKit server); sign-ins run in the same tab and each
  attempt is bound to the browser that started it.
- Chat apps answer only the senders their owner allowed (with an empty list,
  only the owner's own known accounts); webhooks check the platform's
  signature on every call.

## 8. The person decides what is kept

Forgetting is a tombstone, not a deletion, so nothing that rebuilds memory
can bring a forgotten turn back: the chat store, turn reconciliation,
backups restored later and summaries all check the forget ledger. A restore
replays every forget made since. "Don't remember this chat" keeps a whole
chat out, and a fork inherits it. Rewinding a chat rewinds what memory
learned from the turns taken back.

## 9. Operating honestly

- One problem pages once, from one sender, with one recovery message;
  Kazma, its guard and an outside watcher agree who speaks.
- An outside watcher on the operator's own Cloudflare account notices what
  nothing on the machine can: a machine that is off, asleep or cut off.
- A backup proves it can write before it reports success, and restore paths
  are drilled with the real tools.
- Every alert, approval and automatic decision lands in the daily digest and
  the weekly report.

## How it is tested, and what is not yet

- Thousands of tests, many of them driving the real graph, the real stores
  and a real browser; class gates over the whole codebase; long soak runs of
  the swarm, memory, scheduler, Knowledge Library and document pipeline with
  injected faults and killed processes; drills on clean Windows and Linux
  machines.
- A public [benchmarks page](https://kazma.ai/docs/benchmarks/) for memory,
  search and document reading, written from the committed results.
- Prompt-injection resistance is measured per model and published with its
  limits ([threat model](https://kazma.ai/docs/security/threat-model/),
  section 4).
- **Not yet:** an independent security review. Until there is one, treat
  every claim on this page as the authors' description of their own design,
  tested by them. Vulnerability reports are welcome: see
  [Vulnerability reporting](vulnerability-reporting).
