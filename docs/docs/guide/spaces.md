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
refusal names it. Today the per-person features are not built yet, so the
list is never empty. The plan, with every step and
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

## A daily limit per person

Your friends use your model keys, so each person but you has a daily
limit, counted from midnight: a number of tokens, which every model call
counts, and an amount in dollars, which the calls of known price count (0
for none). A model whose price Kazma does not know is still covered by the
tokens. When a person reaches their limit, their requests stop until
midnight and they are told; a request that had already started finishes.
Your own use is covered by the install's limit (Settings → System).

Settings → People sets the limit for people (Kazma's own is 1,000,000
tokens and $1.00 a day) and, for any person, a limit of their own, and
shows what each person used today: counts and money, never what they
asked. Each change is recorded. The limit counts what the per-call ledger
records, so with the ledger off a person's requests are refused.

## Taking your space with you, and deleting it

Settings, *This space*, *This space's data*, shows how much the space
holds. Its owner can:

- **Export it.** The export is one zip file, sealed with a password you
  choose (at least 12 characters). It opens with 7-Zip, WinZip or Keka; the
  zip tools built into Windows and macOS do not open sealed zips. Kazma
  does not keep the password.
- **Delete a shared space.** Everything in it is erased for good. A
  personal space is erased when its person is deleted: the admin erases it
  from People, without seeing what it holds.

Not yet on an install whose data is in Postgres: there, export and delete
are refused rather than done halfway.

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
