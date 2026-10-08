---
title: Native chat commands
---

Kazma accepts ordinary slash text in Telegram, Discord and Slack. Discord and
Slack also accept a native **`/kazma`** command with a command-and-arguments
field: `x help`, `ide help`, `documents list`, or `help`. All execution goes
through the existing gateway dispatcher and its approval and administrator gates.

## Telegram

At startup Kazma writes and reads back its default, private-chat and group-chat
menus in the default language, English and Arabic. Connector Test diagnostics
show whether every menu matches, or name a mismatch or safe failure category. An
owner-specific chat/member scope can override these menus; Kazma leaves those
custom scopes alone. Commands addressed to another bot are ignored. Commands
addressed to Kazma retain their arguments and use the same handlers as bare text.

## Discord

At Gateway READY, Kazma upserts **only `/kazma`** in each configured allowed guild,
or globally when no guild list is configured. It reads the registration back;
Connector Test summarizes verified scopes and registration failures. The installed application must
have the `applications.commands` scope. Registration alone does not prove that
an actual guild command reached Kazma: verify `/kazma command:help` in a test guild.

The adapter acknowledges authorized commands with a private deferred response
before queueing work. Final output completes that response; extra chunks are
private follow-ups. Unauthorized users and guilds never enter the queue. Existing
approval buttons retain their independent authorization checks.

## Slack

Configure one slash command on the existing Slack application. Merge this fragment
into its app manifest; preserve the application's existing scopes and features:

```yaml
features:
  slash_commands:
    - command: /kazma
      description: Run a Kazma command
      usage_hint: help | x help | ide help | documents list
      should_escape: false
settings:
  socket_mode_enabled: true
```

Save the manifest, reinstall the app if Slack requests it, and keep the existing
bot token and app-level Socket Mode token configured in Kazma. Bot and Socket Mode
tokens do not grant app-manifest editing rights. A Connector Test saying the
receiver is enabled is not evidence that this external manifest is installed.
Verify `/kazma help` in an allowed workspace/channel using an allowed user.

Native envelopes are acknowledged promptly, deduplicated, and checked against
team, channel and user allowlists. Delayed output uses the private response URL
supplied by Slack. Kazma accepts only Slack-owned HTTPS command endpoints.

## Delivery limits

Continuation credentials stay in bounded adapter memory; they never enter graph
state or persisted session metadata. Discord credentials expire after 15 minutes;
Slack permits five response-URL messages within 30 minutes. A restart, expiration
or limit violation produces a delivery failure instead of posting a private result
to a public channel. Use ordinary message commands or web chat for long-running
work and durable approval conversations. Native commands should primarily serve
short control/status operations.

Buttons in private replies keep their follow-up output private. A button callback
supplies a fresh interaction credential; Kazma validates and retains it only in
adapter memory. Missing or invalid private continuation data stops dispatch,
rather than rerouting the result to a public channel.

Discord native replies can upload private files up to 8 MiB. Slack response URLs
cannot upload private file bytes: Kazma reports that limitation in the private
reply and records delivery failure. Use web chat or an ordinary message command
for Slack files; Kazma never uploads them to a public channel as a fallback.

Web autocomplete uses the same capability catalog. `/x`, `/model`, `/documents`,
`/kb` and `/ide` provide links to their web management pages; gateway subcommands
are not silently treated as model instructions. Unsupported commands return help.

Protocol references: [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding),
[Slack slash commands](https://docs.slack.dev/interactivity/implementing-slash-commands/).
