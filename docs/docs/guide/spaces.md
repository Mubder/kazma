---
id: spaces
title: Spaces -- several people on one Kazma
sidebar_label: Spaces
description: What Spaces keeps apart, what the admin can and cannot do, and what it does not promise
---

# Spaces -- several people on one Kazma

> This page is published at [kazma.ai/docs/spaces/](https://kazma.ai/docs/spaces/) ([Arabic](https://kazma.ai/ar/docs/spaces/)).

Spaces lets the person who runs a Kazma share it with a few people they
know -- family, a small team -- so that each person has a space of their own:
their chats, memory, documents and connected accounts, which the others
cannot see.

## Where it stands

**Spaces is not switched on in any release yet.** An install has one
account, and everything works exactly as it always has. What is built is
the foundation: every part of Kazma keeps a space's data apart once Spaces
is on, and each part has a test that runs one space against another.

Before a second account can be switched on, these must hold: work that
names no space is refused everywhere, every secret has one value, each
person has a daily spending limit and a list of features they may use, no
plain copy of the backup leaves the machine, the deploy check signs in with
the owner's own API token, and every space has its key. Kazma checks them
itself:

```bash
kazma spaces migrate --check
```

It changes nothing and lists each one as holding or missing, with what
closes it. While one is missing, adding a second person is refused and the
refusal names it. Today some of Kazma's own code still falls back to the
owner's space when no space is named, so the list is never empty. The plan, with every step and
its state, is `docs/plans/SPACES_PLAN.md`.

This page describes what Spaces does once it is on.

## What a space is

- **Every person has a personal space.** Only they are in it. A new
  person's space is *sandboxed*: it works in a folder of its own and has
  none of the tools that act on the machine itself.
- **The admin's personal space** holds the install's own data -- everything
  made before Spaces -- and the tools that act on the machine (the shell,
  git, the IDE, the workspace, MCP servers).
- **Shared spaces**, which several people use together, come in a later
  stage.

Which space a page is in is shown on the page, and a request always names
its space: the server checks on every request, and every socket, that the
person is a member of it. Being an admin does not make anyone a member.

## What a space keeps apart

Once Spaces is on, each of these belongs to one space, and a person sees
those of the spaces they are a member of only:

- chats, their history, Time Travel's steps, approvals waiting for an
  answer, and drafts;
- memory: facts, past conversations, weekly summaries, About me, and what
  Kazma learned from the conversations (its lessons);
- documents, the Knowledge Library, research, scheduled jobs and swarm
  tasks;
- connected accounts: mail, calendar, X and GitHub sign-ins, and the
  secrets in the vault;
- files: uploads, generated images and documents, and the space's working
  folder;
- running work, its incidents, and the notices about them;
- a finished answer's push notification, which goes to the devices of the
  person whose chat it is.

The admin's own Settings, `/config` and the agent's `config_read` show the
install's settings and their own space's, never another space's, and none
of Kazma's own records.

The tools a space has follow its kind: a sandboxed space has no shell, git,
IDE or MCP servers, and its code runs in a container that sees only the
space's folder.

Code Kazma runs for anyone but you has no network unless you allow it for
that person on People. Allowed, on Kazma's own container engine only, it
reaches the public internet, including this network's own public address:
your router's public side, so anything your router forwards from the
internet, and its admin page if the router answers there. It never reaches
this computer, your local network or a cloud machine's metadata, and it
names hosts through Docker's resolver. Before each such run Kazma checks
that the rules holding it there are in effect, and refuses the run when they
are not; Docker does not start on the engine without them.

In a shared space with a project folder, a person you give Development to
gets git, GitHub and the project's files, and nothing of the project's runs
on this computer for them: no shell, no tests or package installs, git with
its hooks and the project's own git programs switched off, nothing written
in the repository's `.git` folder, and none of your MCP servers. A
repository whose git configuration names a program (a filter or merge
driver) is yours alone until you remove it. The filesystem MCP server is
never in a shared space, for you either. Outside your own space, git signs
in with the space's own GitHub account and never with this computer's SSH
keys or saved passwords.

## Moving a chat or a document

Private means in your personal space. Once Spaces is on, a chat you started,
or a document of yours, can move from your own space into a shared space you
are in, and back out:

- **A chat:** the chat list's menu offers "Share with…" in your own space and
  "Take out to my space" in a shared one. Moving a chat moves the
  conversation. What Kazma remembered from it stays where it was said, and
  from now on it remembers what you say in the new space. Taking a chat out
  of a shared space makes that space forget it: its memories, notes,
  summaries and lessons. It does not bring back what the chat's facts had
  replaced there, and a fact another chat of that space states too can go
  with it. Your own space's memory is never changed by a move, nor by an undo
  in the shared space.
  The moved chat is a new chat there, with the conversation, and the one it
  was is deleted where it was: a tab still open on it is told so, and your
  message goes to a new chat. The files Kazma shared in it go with it. Its
  Time Travel steps, thoughts and approval cards do not come along, and a
  file you attached has to be attached again.

  *When you share it,* its drafts and plans, and the reminders and scheduled
  tasks you booked from it, stay in your own space. *When you take it out,*
  the shared space keeps none of its drafts, plans, confirmations or finished
  swarm tasks (nor their workers' log), and none of the web searches it made;
  a search another chat there made too stays. A deep research report stays:
  it is a file of the space, read by its members. The reminders and tasks you
  booked in it come with you (another member's stay). Approvals you gave for the chat, YOLO, `/long` and
  folder grants end with the chat it was.

  What you asked Kazma to forget of it stays forgotten where it was said; the
  chat's own words stay in the chat. A chat from a chat app (Telegram,
  Discord, Slack, WhatsApp) stays in its space. One with an answer cut off
  half way waits until you continue or close it, one that started a swarm
  task until the task is over, and one in plan mode until you approve or
  cancel the plan. If something is written in the chat while it moves, it
  stays where it was and you can try again; if both the chat and its copy were
  written in, both are kept.
- **A document:** the Documents page offers the same two moves.

A chat someone else wrote in stays in the shared space: their words are the
space's. Taking something out leaves the shared space a line saying who took
it out and when, with its title. A move is always your own click: the agent
never moves a chat or a document.

## What the admin can and cannot do

| The admin can | The admin cannot |
|---|---|
| Add a person, reset their password, delete them | Open a space they are not a member of, by any page, route, tool or command |
| See each person's spend and how much work they ran | See the titles or the text of that work |
| Run updates, backups and restores of the whole install | Export a member's space, or write into it through Settings |
| Set the install's model keys and chat-app bots | Use a member's connected accounts |

A password reset is never silent: it is recorded in the identity store's
record, which cannot be edited without the edit showing, and the person is
told the next time they open Kazma.

People shows each person whole: their features, tools, daily limit and
models, the spaces they are a member of, when they last signed in and how
many sessions they have open. Ending their sessions signs them out on every
device; it is recorded, and they are told the next time they sign in.

## A daily limit per person

Your friends use your model keys, so each person but you has a daily
limit, counted from midnight: a number of tokens, which every model call
made for their requests counts -- chat, research, the swarm, X Studio --
and an amount in dollars, which the calls of known price count (0 for
none). A model whose price Kazma does not know is still covered by the
tokens. The memory work Kazma does after a turn (learning facts and
lessons from it) is not counted yet. When a person reaches their limit, their requests stop until
midnight and they are told; a request that had already started finishes.
Your own use is covered by the install's limit (Settings → System).

Settings → People sets the limit for people and, for any person, a limit
of their own, and shows what each person used today: counts and money,
never what they asked. Each change is recorded. The limit counts what the
per-call ledger records, so with the ledger off a person's requests are
refused.

Kazma's own limit for people is 3,000,000 tokens and $1.00 a day. Every
model call sends the instructions, the turn's notes and the conversation
again, so a turn costs more than its words: on Kazma's own install in
November 2026 a turn used about 72,000 tokens in the middle of the range,
and one in ten used more than 189,000. 3,000,000 tokens is about 40
ordinary turns a day; the dollar amount binds only a model whose price
Kazma knows. Raise or lower it for everyone, or for one person.

## A person's own model key

A person may use a key of their own for a provider your install has,
instead of yours: Settings → My account → Use my own key, on their own
page (you have none: your keys are the install's, under Providers). Kazma
first sends one short message through the key; a key the provider refuses
is not kept. The key is kept sealed in their personal space, with that
space's own key. Nobody else's work reads it, you included, and no page
shows it, theirs included: they see only that one is kept.

Every model call made for them to that provider then goes with their key
alone, from any of their spaces. If the provider refuses it, the call
fails and says so; Kazma never falls back to your key. What they spend
with their own key is theirs: it does not count against their daily limit,
nor against the install's. A person who has already reached their daily
limit is still stopped, even when their next request would run on their
own key. Use the install's key, beside it, forgets their key.

## Features per person

What each person may use is a switch per feature, on Settings → People:
X Studio, email and calendar, documents, knowledge, research, the swarm,
scheduled tasks, code, the web, the browser, images and voice, and
Development. A new person starts with chat and its memory, documents,
knowledge, the web, research, and images and voice; the rest is off until
you turn it on. Grants are per person, in a shared space too, and only you
make them.

## Limits per space

One space at its limit does not slow or stop another. Each space has its
own limits beside the install's:

- **Work running at once.** A space runs at most its share of the
  install's slots: by default 2 of the 4 Knowledge crawls, 2 of the 4
  research runs and 5 of the 10 swarm tasks, so two spaces at their limit
  still leave each other room. Work past its share is refused at once and
  can be started again when one ends; it never waits in the install's
  queue.
- **Storage.** The files in the space's own folder (2 GiB by default), its
  documents (10 GiB) and its Knowledge libraries (256 MiB). A space at one
  of these takes no more of it -- an upload, a saved file, a generated
  image, a new document or library page is refused with the reason -- and
  everything it holds stays. Your own space keeps the install's folders and
  has no files limit.

Settings → People shows each space's usage beside its limits (counts and
sizes, never what it holds), and sets the limits for spaces and, for any
space, limits of its own. Each change is recorded.

## Activity

Every change made in a space -- a message sent, a document moved, a
setting saved, an approval answered -- is recorded under the name of the
person who made it: when, who, which action and whether it was done.
What was sent is never kept there. The members of a space see its record on
the Activity page; nobody else does, you included. It is kept for 90 days.

A feature that is off is gone for that person: no menu entry, its pages
say it is off, its routes and live voice refuse, its chat commands refuse,
its tools are neither offered to the model nor run, and a request that
would start its work (asking for a deep research, booking an X post from
Scheduled) starts nothing. Work they started before it was switched off
waits: a booked X post stays booked and goes once the feature is on again,
a Knowledge crawl stops before its next page and finishes on Resume, a
scheduled job does not fire, a swarm worker does not run, and a document
job stops and can be retried. A research run already going finishes. Their
data stays, and turning the feature on again brings it back. Putting a
document into a library, or searching libraries through Documents, needs
Knowledge too. You have every feature; Development is the one you can
switch off for yourself.

## Taking your space with you, and deleting it

Settings, *This space*, *This space's data*, shows how much the space
holds. Its owner can:

- **Export it.** The export is one zip file, sealed with a password you
  choose (at least 12 characters). It opens with 7-Zip, WinZip or Keka; the
  zip tools built into Windows and macOS do not open sealed zips. Kazma
  does not keep the password.
- **Move a shared space to another Kazma.** Only an owner moves it. *Move
  this space* makes one file sealed whole with a password you choose:
  scrypt, then AES-256-GCM over every byte, so nobody without the password
  can read it, change it, or drop or rename anything in it. The key this
  space's secrets were sealed with goes inside it, so the space gets a new
  one here as the bundle is made. It holds the
  space's chats, memory, files, documents, libraries, settings and secrets.
  **Only you arrive with it:** the other members leave it on arrival and
  are invited again there. What Kazma remembers about them, their scheduled
  jobs, tasks, research and posts, and the accounts they connected stay
  here, and so do the files X Studio drafts from: they are paths of this
  machine. What they wrote stays the space's, by someone who left. **What the
  space was running arrives stopped**, since the space keeps running here
  too: a scheduled job arrives cancelled (restore it there, and it goes to
  your own chat there), a swarm task cancelled, a research run interrupted,
  an X post due to publish waits for approval, and an approval a chat was
  waiting on arrives expired. **Records of this install never travel:**
  model spend, the forget journal, the record of actions, approval cards,
  Time Travel's steps. **A project folder's files stay on this machine:**
  the space arrives as a plain shared space. On the other install, its
  admin brings it in (Settings, *People*, *Shared spaces*, *Bring in a
  space*) with the file and the password, and chooses who owns it there.
  Nothing is merged: if anything of the space, or any of its ids, is
  already on that install, nothing is brought in, and an import that stops
  halfway is taken back. The space stays where it was until you delete it.
  A personal space does not move; export it to keep a copy.
- **Delete a shared space.** Everything in it is erased for good. A
  personal space is erased when its person is deleted: the admin erases it
  from People, without seeing what it holds.

Not yet on an install whose data is in Postgres: there, export, move and
delete are refused rather than done halfway.

## Backups

A backup is of the whole install. It carries every space, and the record
of who is in which, so a restore puts every chat back in its space.

- The admin's Settings file (Settings, System, Download backup) holds the install's settings
  and the admin's own space's, never another space's, and restoring an
  older file never writes into another space.
- Once a second person is added, the only copy of the backup that leaves
  the machine is restic's encrypted copy. The plain copies (a cloud
  provider's zip, an rclone folder) are refused and the backup says so.

## What Spaces does not promise

- **Spaces keeps people who use Kazma apart. It does not keep anything from
  the person who runs the machine.** They can read the disk, the databases
  and the backups, and every space is in them. Encrypting a space so that
  only its members can open it is planned, not built.
- **A space's secrets are sealed with a key of its own, and that key is
  wrapped by the install's.** Its mail, calendar, X and GitHub sign-ins do
  not open with another space's key, and erasing a space removes its key.
  The person who runs the machine holds the key that wraps every space's,
  so this keeps spaces apart from each other, not from them.
- **It has not been reviewed independently yet.**
- **Chat apps belong to the admin until a later stage.** Telegram, Discord,
  Slack and WhatsApp answer the admin's own accounts and work in the admin's
  own space; a chat that belongs to another space is refused there. Other
  people use the web. Until they link their own chat apps, only the admin
  books scheduled jobs, because a job is delivered to a chat.
- **A backup taken before an erase** still holds the erased space until it
  ages out.

What each safeguard does and does not stop is in the
[threat model](https://kazma.ai/docs/security/threat-model/), and how the
boundary is built is in the
[safety design](https://kazma.ai/docs/security/safety-design/).
