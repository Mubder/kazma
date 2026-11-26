---
id: windows-smart-app-control
title: Kazma and Windows Smart App Control
sidebar_label: Windows Smart App Control
description: How Kazma works with Windows' file check on your PC, what it does when Windows checks one of its files, and the plan for signed releases
---
> This page is published at [kazma.ai/docs/windows-smart-app-control/](https://kazma.ai/docs/windows-smart-app-control/) ([Arabic](https://kazma.ai/ar/docs/windows-smart-app-control/)).

# Kazma and Windows Smart App Control

Windows 11 can check every program and library before it runs. **Smart App
Control** (and **App Control for Business**, its counterpart that companies
set for their computers) lets a file run when a known publisher signed it or
when Windows already knows it well. It is a good protection to have.

Kazma works with it. You keep Windows exactly as it is, and Kazma keeps
working: where Windows does not allow one of the files Kazma uses, Kazma uses
a signed alternative, keeps the version that is running, or tells you which
feature waits. Kazma never asks you to change Windows' protection.

## What Kazma does when Windows checks a file

| Part | When Windows does not allow it | What you notice |
|---|---|---|
| The database client library (bundled with psycopg) | Kazma uses the signed library that comes with PostgreSQL, which `kazma setup postgres` installs. While Smart App Control checks files, Kazma uses the signed one from the start | Nothing: everything works |
| Kazma's Docker command-line tool | Kazma uses the signed one Docker Desktop installs, when it is on the PC. While Smart App Control checks files, Kazma uses the signed one from the start | Nothing: everything works |
| A new version of Kazma | Before the guard switches to a new version, it checks that the new version can start on this PC. If it cannot, Kazma keeps running the version it had | One message: "An update waits; Kazma keeps running" |
| A part with no signed alternative on the PC | The feature that needs it waits for an update that brings a signed version; everything else keeps working | One message naming the feature |

In every case nothing is wrong with your PC. Windows is doing what it was
asked to do, and Kazma is doing the same.

## Seeing what Windows checked

| Where | What it shows |
|---|---|
| `kazma doctor` | The section "Windows' file check": whether Smart App Control checks files, and each of Kazma's native parts: allowed, replaced by its signed alternative, or the feature that waits |
| `kazma setup system` and `kazma setup install` | A note when Smart App Control checks files, and one for each part Windows did not allow, with what Kazma uses instead |
| The security report (`GET /api/security/hardening`) | The check `check_windows_file_check`: always information, never a finding, because a Windows protection is never a weakness of the install |
| Kazma's log at start | One line beginning `[windows] Smart App Control:` with each part and its state |
| Your alerts | Only when a feature has no signed alternative on this PC: "Kazma could not keep a feature working on this PC", naming the feature |

What each state means:

- **Allowed**: Windows lets the file run. Nothing to do.
- **Signed copy**: Windows checks each file, so Kazma uses the signed copy of
  the part from the start. Nothing to do.
- **Signed alternative**: Windows checked the file and did not allow it, and
  Kazma uses a signed file that does the same job. Nothing to do.
- **Waits**: Windows checked the file, no signed alternative is installed, and
  one feature waits for an update with a signed version. Everything else
  works.

## An update that waits

The guard is the part of Kazma that keeps it running and switches it to a new
version. Before it stops the running version, it starts a short check in a
separate process: can the new version load what it needs on this PC? When the
answer is no, the running version stays, the update waits, and you get one
message saying so. Nothing is lost. The next update or reload checks again.

## What Windows checked on a real PC

On 26 November 2026, Smart App Control was on on the PC Kazma is built on.
Most of Kazma's files, including ones that carry no signature yet, ran
normally: Windows already knew them well. Windows did not allow two:
psycopg's database client library (its Python 3.14 build) and the Docker
command-line tool Kazma's container engine had installed. Kazma now uses the
signed alternatives for both, as the table above says. A different PC, or a
newer version of a file, can be judged differently, so the plan below is
signatures rather than reputation.

## For company IT: App Control for Business

A company may manage its PCs with **App Control for Business** instead of Smart App Control. Unlike
Smart App Control, a company policy can name what it allows. If your IT team
manages the policy, they may choose to allow Kazma's signed files (by its
publisher certificate, once releases are signed) or the folder Kazma is
installed in. This is an option for the administrators who own that policy;
nobody using Kazma needs to do anything, and Kazma works the same way under
either: it uses signed alternatives, keeps the running version, or names the
feature that waits. `kazma doctor` lists each of Kazma's native parts and
what Windows decided, which is what an administrator needs to write a rule.

## What comes next

1. **Signed releases.** Kazma's own files signed with a code-signing
   certificate, so Windows knows who published them.
2. **A release bundle in which every file Kazma ships is signed**, including
   the libraries Kazma installs from the Python package index, which come
   unsigned from their authors today.
3. **Fewer native files.** What a running Kazma does not use moves out of the
   default install, so there is less for Windows to check.

## Questions

**Do I need to change anything in Windows?** No. Keep Smart App Control as it
is. Kazma works with it, and future releases are signed so that Windows can
recognise them.

**Is something wrong with my PC?** No. A message about Windows' file check
means Windows checked a file and Kazma adapted.

**My company manages the PC with App Control for Business.** Kazma reads it the
same way and behaves the same way. Your IT team can see each part Kazma uses
in `kazma doctor`.
