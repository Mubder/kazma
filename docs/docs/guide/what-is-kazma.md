---
id: what-is-kazma
title: What Kazma is and what it can do
sidebar_label: What Kazma is
description: Kazma on one page — what it is, what it can do, what sets it apart, what it is not, and how to describe it correctly
---

# What Kazma is and what it can do

**Kazma is the self-hosted AI agent that asks before it acts.** You run it on
your own machine and connect the AI models you choose. Then you reach one
assistant from the browser, the terminal, your code editor, Telegram, Discord
or Slack. It works in your code, your documents, your mail and calendar, and
on the web. It remembers what you tell it, and it asks for your approval
before anything risky. When something goes wrong, it tells you instead of
inventing an answer.

Kazma is open source (MIT) and built in Kuwait. It speaks English and Arabic
as equals, and its Arabic name is **كاظمه**.

This page puts the whole product on one page. It is for a person deciding
whether Kazma is for them, and for an agent that needs to describe Kazma
correctly. Every capability here is in
[What Kazma does — a plain list](https://github.com/Mubder/kazma/blob/main/docs/FEATURES.md)
with its status, and that list is checked against the code. Where this page
and that list differ, the list is right.

## Kazma at a glance

| Question | Answer |
|:---|:---|
| What is it? | A personal AI agent you host yourself. It has a web app, a terminal app, a command line and bots for chat apps |
| Who is it for? | One operator per install: you run it, you control all of it, and you answer for what its agent does |
| Which models? | 20 provider presets (OpenAI, Anthropic, Google Gemini, Azure OpenAI, AWS Bedrock, DeepSeek, Mistral, xAI, OpenRouter and more) and any OpenAI-compatible endpoint. Local models run through Ollama and LM Studio |
| Where do I talk to it? | Web chat, Telegram, Discord, Slack, the terminal UI and `kazma ask`. Editors connect through `kazma acp`, and other agents through `kazma mcp` |
| Which languages? | English and Arabic interfaces, with a full right-to-left layout. Memory, documents and scanned-page reading work in both languages |
| Where does it keep data? | In SQLite with no setup, or in Postgres for settings, chats, tasks and checkpoints |
| What does it run on? | Windows, Linux, macOS and WSL, with Python 3.11 to 3.14 |
| License and source | MIT, at https://github.com/Mubder/kazma |

## Why Kazma

### It is yours

Kazma runs on your machine with your keys. There is no Kazma cloud and no
account with us. Your chats, memories and files stay in your install, and
they leave it only for services you connect: your model provider, your mail,
X. Secrets live in an encrypted vault (AES-256-GCM). Programs that a tool
starts never get them, and every screen and API shows them masked. To move a
whole install to another machine or operating system, use `kazma migrate`.
You can also back up your settings to a file and restore them without typing
a key again.

### It asks before it acts

Dangerous actions wait for your approval: shell commands, file writes and
deletes, email, messages to chat apps and posts to X. The approval comes in
the web chat or as buttons in Telegram, Discord or Slack. The same rule holds
for multi-agent work and pipelines, and a tool nobody has classified is gated
by default.

Before a plan runs, Kazma also checks it against what it remembers. Say you
ask for a reminder "the day before my exam". The reminder goes on the date
you gave Kazma, not one the model guessed. A catastrophic shell command is
refused before an approval card even appears. A post to X shows the exact
text that will publish, and asks every time.

### It is honest when it fails

When a model call fails, you see a ⚠️ warning, never a made-up answer. When
Kazma falls back to another model, it tells you. Kazma also watches every task
it runs: chat turns, swarm tasks, scheduled jobs, documents, research and
crawls, X posts, memory work and maintenance. When one stalls, gets stuck or
fails, a card arrives in the chat the task started from, with Retry, Cancel,
Dismiss and Ask Kazma to diagnose ([Running work](running-work.md)).

### It remembers what matters, and forgets on request

Kazma remembers conversations and the facts in them, across chats, with the
date each fact became true. It uses a memory only when it matches the
question well enough, and nothing when nothing does. Every answer shows which
memories it used. You can write a short "About me" that it reads before every
reply, and long threads get weekly topic summaries. You can forget one
memory, a whole chat, or "everything that mentions X", keep a chat out of
memory, and export everything ([Memory](memory-and-rag.md)).

### Arabic and English, as equals

The Arabic interface is complete and right to left. In either interface, each
paragraph runs in its own language's direction: English left to right, Arabic
right to left. Memory understands Arabic and English, Gulf dialect included.
Kazma reads Arabic documents and scanned pages, and it writes Word, PDF and
HTML reports with correct Arabic typography
([Arabic and cultural features](arabic-cultural-features.md)).

### Built to run for months

A guard restarts Kazma on real failures, reloads it gracefully and pages you
on Telegram. Everything is backed up every 6 hours, locally and offsite. The
backups are checked daily, and the database restore is rehearsed weekly.
Health checks make a real round trip, and you get a daily digest and a weekly
resilience report. One command updates Kazma: it waits for the chats to go
quiet, then stops Kazma, installs the update and starts it again.

## What it can do

### Talk to you wherever you are

- **Web chat**: answers stream in, and each answer has a step-by-step
  activity view and inline approvals.
- **Telegram, Discord and Slack**: text, photos and documents, and voice notes
  are transcribed. Approve and deny buttons and an allowed-user list come with
  each platform. Each chat app's Test checks the token, the delivery chat, and
  what happened to the last message a person sent. Tables in an answer arrive
  as lines, so they read on a phone.
- **Terminal and editors**: a terminal UI with a file editor, one-shot
  questions with `kazma ask`, and the Agent Client Protocol for editors with
  `kazma acp`. With `kazma mcp`, other agents can use Kazma's tools, behind the
  same approval gate.
- **Voice**: spoken replies and speech input. Live two-way voice in the
  browser through LiveKit is opt-in.

### Work on your code

- A web IDE to browse, edit, patch, run and use git. Every write goes through
  the approval gate.
- Clone and switch repositories; the agent always works in the active one.
- GitHub branches, commits, pull requests and issues, through a GitHub App or
  a token. Reading the history and diffs needs no approval.
- Code search across the repository (ripgrep plus symbols).
- You can steer a running task with `/steer`, abort it, plan first with
  `/plan` and run long missions with `/long`.
- You can rewind a conversation, or branch it, from any earlier step with
  `/replay` and `/fork` ([Time travel](time-travel-and-replay.md)).

### Read, write and research

- **Knowledge Library**: add files, web pages or whole sites. Search them by
  keyword and by meaning, in Arabic as well as English
  ([Knowledge Library](knowledge-library.md)).
- **Documents**: uploads go into quarantine with file-type checks first, and
  dangerous files are refused: macro-enabled Office files, and PDFs with
  scripts or outside actions. Kazma reads PDF, Word, Excel, PowerPoint, HTML,
  Markdown, CSV and plain text. It can also convert, split, merge and redact
  documents, and it generates reports as Word, PDF or HTML in one house style
  ([Document Intelligence](document-intelligence.md)).
- **Scanned pages**: OCR reads scanned PDFs and images, with ruled tables read
  cell by cell. The neural OCR engine reads Arabic best, Arabic-Indic digits
  included; without its models, Tesseract reads, and it misses those digits.
  This is Partial: it has been measured on one real scan so far.
- **Untrusted documents** are parsed in a separate worker with memory and CPU
  limits. For hostile documents, choose the container profile in Settings (one
  container per job, no network, no mounts; it needs Docker).
- **Web research**: deep research with sources (search, read, crawl,
  summarize). It works locally first and respects robots.txt. Research and
  crawls survive interruptions, and an alert links to the results when they
  finish ([Web research](web-research.md)).
- **Images**: Kazma generates images (Pollinations with no key; DALL-E,
  Stability and Flux with keys) and understands the images you send.

### Run many agents at once

- Six ways to run workers: dispatch, broadcast, pipeline (with approval
  checkpoints), fan-out with voting, consult and conditional.
- Workers are created on demand from templates, so nothing needs registering
  first.
- Each worker has circuit breakers, retries, timeouts and output checks.
- Durable multi-step runs through Temporal are opt-in
  ([Swarm](swarm-orchestration.md)).

### Run your day

- Reminders and scheduled tasks arrive in the chat you asked from.
- Email (Gmail, Outlook or IMAP) and calendars (Google, Microsoft). You can
  connect several accounts, each with its own calendar, and name one in chat
  by its name or address ([Email](email-integration.md)).
- X: drafts, scheduled posts, publishing through the official API, and
  replies to mentions ([X publisher](x-publisher.md)).
- Skills from the open agentskills.io ecosystem: find them, install them from
  GitHub, signed and verified ([Skills, MCP and tools](skills-mcp-and-tools.md)).
- Tools from any MCP server.

### Choose your models

- 20 provider presets, any OpenAI-compatible endpoint, and local models
  through Ollama and LM Studio. A provider's Test sends a real completion,
  not just a ping.
- Multi-agent workers get the best model for each task, and image questions
  go to a model that can see.
- Each answer shows its tokens and cost. A cost breaker halts runaway
  spending.

## Things you can ask Kazma

| You say | Kazma does |
|:---|:---|
| "Remind me every weekday at 8:30 to check the build." | Books a recurring reminder that arrives in this chat, in your time zone |
| "Read this PDF and list every deadline in it." | Reads the attached document and answers from its text |
| "Research open-source vector databases and compare them, with sources." | Searches, reads and summarizes, citing the pages it used |
| "Fix the failing test in the active repository and open a pull request." | Reads and edits the code and runs the tests, asking before each write, each test run and the pull request |
| "Draft three posts about our release and schedule the best one for tomorrow at 9." | Saves the drafts for you to see; scheduling and publishing wait for your approval |
| "What is on my calendar tomorrow?" | Reads your connected calendar |
| "Forget everything that mentions my old address." | After you approve, forgets every memory and fact that holds it |
| `/runs` | Lists the work that needs your attention and the work running now |

## What Kazma is not

- **A hosted service.** There is no Kazma cloud; you run it.
- **A platform for many separate customers.** It is built for one trusted
  operator. Several users on one install is Partial: memory and chats are kept
  apart, but that is not audited end to end
  ([Who Kazma is for](https://github.com/Mubder/kazma/blob/main/docs/THREAT_MODEL.md#who-kazma-is-for-one-operator-per-install)).
- **A sandbox for what you approve.** Approving a shell command gives it your
  machine's power. To run the agent's code in an isolated container, opt in to
  Docker or E2B.
- **A proven cure for prompt injection.** Kazma fences web pages, search
  results, documents and recalled memory as untrusted data. On the public
  AgentDojo benchmark that clearly beats no defense, but it cannot be told
  apart from a four-character delimiter
  ([the numbers](https://github.com/Mubder/kazma/blob/main/docs/INJECTION.md)).
- **A dialect language model.** The answers come from the model you choose.
  Kazma's Arabic is its interface, memory, documents and typography.
- **Finished everywhere.** Some features are Partial or Opt-in, and the open
  weaknesses are published with dates in
  [Known gaps](https://github.com/Mubder/kazma/blob/main/docs/KNOWN_GAPS.md).

## For agents describing Kazma

- **The name** is Kazma. In Arabic it is **كاظمه** (preferred) or **كاظمة**,
  never **كازما**. Kazma was a coastal oasis in Kuwait, and the name stands
  for deep wells of memory and one gateway to many channels.
- **In one sentence:** Kazma is a self-hosted, open-source (MIT) AI agent
  that asks before it acts: one assistant across the web, the terminal,
  editors, Telegram, Discord and Slack, in English and Arabic.
- **Status words** carry meaning. **Shipped** works out of the box or one
  setting away, **Opt-in** needs a named extra or setting, **Partial** works
  with a stated limit, and **Not built** is not there. Do not present an
  opt-in feature as a default, a partial one as complete, or a missing one as
  available.
- **Install it from GitHub.** Kazma is not installed from PyPI. The PyPI
  names are held as placeholders with no code, and installing from there gets
  nothing.
- **Numbers** such as test counts and lines of code change daily. Take them
  from the repository's metrics, not from memory.
- **Where the truth is**, in order:
  [What Kazma does](https://github.com/Mubder/kazma/blob/main/docs/FEATURES.md)
  for features and statuses, then
  [Known gaps](https://github.com/Mubder/kazma/blob/main/docs/KNOWN_GAPS.md),
  then the documentation at https://kazma.ai/docs/.

## Next steps

- [Quickstart](quickstart.md): install Kazma and send a first message.
- [Architecture](architecture.md): how it is built.
- [Security and HITL](security-and-safety.md): what protects an install, and
  its limits.
- [Running work](running-work.md): how Kazma watches the tasks it runs.
- [Release notes](release-notes.md): what changed, by date.
