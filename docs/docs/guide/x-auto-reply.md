---
id: x-auto-reply
title: X auto-reply
sidebar_label: X auto-reply
description: Mention the account and Kazma drafts a reply — declared sides, summon emoji, quotes, and Conversations.
---

# X auto-reply

Kazma can draft a reply when someone **mentions** the connected account on X. It is **off by default**. Credentials, ToU caps, and the audit log are the same as [X publisher](./x-publisher.md).

A reply is always **one subject + one side**. Emoji is **tone** when a Settings card matches, and **side** when none does.

## How a summon is decided

Walk this list. The first hit wins.

1. **Keyword on a Settings card** — whole word for ASCII, containment for other scripts. Cards are tried **top to bottom**. Put specific topics above broad ones.
2. **Opinion ask** — `شرايك` / `what do you think` / a `?` / `؟` / how-what-why / both 👍 and 👎 (or ❤️ and 😂) in the same mention. Reacts to *this* post in voice. Does not pick a country card.
3. **Summon side** — one polarity in the mention:
   - 😂 🤣 💀 🔥 🤬 😡 🙄 👎 or `against` / `roast` / `ضد` / `هاجم` → **criticise this post**
   - ❤️ 👏 💯 🙏 👍 or `support` / `defend` / `دافع` / `معاه` → **defend this post**
4. **Stay silent** (default once any card exists). A `*` keyword still answers everything. “Reply anyway” is voice with no side.

A Settings card **only applies when one of its keywords is in the post**. A classifier cannot pin an unrelated tweet on a card you wrote for something else.

When a card matches, its **side** (against / support) is locked. A roast emoji cannot flip a support card. Angry on a support card is anger **at its critics**, never at the subject.

## What counts as a summon

| Mention | What Kazma reads |
|---|---|
| Standalone `@handle 😂` | The mention itself |
| Reply under someone else's post | That parent post |
| Quote of someone else (even under Kazma's own tweet) | The **quoted** post |
| `هذا` + `x.com/…/status/…` or `t.co/…` in a reply to Kazma | The linked post |
| Bare reply under Kazma with no quote and no link | **Ignored** (X pre-fills @handle) |

X only lets this account **post** a reply on a tweet that mentions it or that it wrote. The public reply therefore sits **under your mention**, not under the original. The *text* it drafts against is still the original when a quote or link is present.

Paid X plan required for the mentions poller (`GET /2/users/:id/mentions`). Free tier 403s; the poller logs that and backs off.

## Two ways to fire it

**Paste (every X plan).** From chat:

```
/x roast https://x.com/someone/status/1234567890 | Their post text here
```

Always **draft** mode. Approve with `/x approve <id>`. Gateway login is the authorization — you do not need to be in `summoners`.

**Mentions poller (paid plan).** Kazma polls its mentions. Save on Settings starts or stops the poller (no restart). **X Studio → Conversations → Refresh** is the same poll, and it ignores a stuck cursor. `/x poll` too.

## Subjects

Settings → Integrations → X → Auto-reply.

Each card:

| Field | Role |
|---|---|
| **Id** | Short name (`topic`) |
| **Side** | `against` (always criticise) or `support` (always defend) |
| **Keywords** | When this card fires. ASCII = whole word. `*` = catch-all (tried last) |
| **Fallback mood** | Used if the mention has no emoji |
| **Optional extra** | Colour (who / why) — not a second side |
| **Hard lines** | Extra “never say”. Universal lines always apply |
| **Examples** | Replies you actually wrote — strongest voice lever |

Empty allowlist + `allowlist` policy = **nobody** can summon. `anyone` still has caps, follower floor, screen, and stance check.

**Open-thread marker** (optional): a token you put in *your* mention (e.g. 🗣️). Without it, only trusted handles get a reply. With it in the parent post, anyone who mentions Kazma on that thread can get a reply. Trusted handles (you) can keep talking without extra emoji — thread cooldown and per-target caps do not apply to you, so a general question can be a back-and-forth.

List order is priority: first card whose keyword appears wins.

### Modes

| Mode | Behaviour |
|---|---|
| `draft` | Compose, hold, notify. You post with **Approve** (Conversations or `/x approve`) |
| `auto` | Posts after rails + screen + stance check. Text you have not read, under your name |
| `off` | Master off |

`/x roast` is always draft.

## Stance check

The length/threats screen does not know your side. After drafting, a short closed-set call asks **argues / contradicts / fence** (any language). Sympathy for the other side, “don't attack them”, or a summary with no side is **contradicts** or **fence** and is blocked.

Catch-all `*` and unmatched **voice** skip this check (there is no claim to drift from). Content screen and hard lines still run.

`auto` + a check that cannot run → **block**. `draft` + unusable check → **allow** (you read it).

## Knowledge Base (optional)

Settings → “Ground drafts in the Knowledge Base”. A few fenced snippets. If they conflict with the side, **the side wins**. Off by default.

## Conversations

**X Studio → Conversations** is the log of summons: parent text, mention, draft, skip reason.

| Button | Does |
|---|---|
| **Refresh** | Polls X (not a page reload) |
| **Approve** | Posts the **stored** draft, as a reply to the mention |
| **Deny** | Parks a held draft |
| **Retry** | Re-runs skipped / failed / stuck `drafting` against current config (always holds for approval) |
| **Delete** | Posted: delete on X and drop the row. Else: drop the row |

Sort is **newest on X first** (tweet id), not last touch. A Retry of an old card does not jump it to the top.

## Guardrails

On top of ToU policy (length, mentions, duplicates, daily/monthly caps):

- Universal hard lines (no slurs, no threats, criticise institutions not peoples)
- Post-generation screen on what the model wrote
- Daily / per-target / per-thread caps
- Follower floor (default 500) — `0` disables it knowingly
- Reply is posted under the **mention** (X rule)

## Turning it on

1. Settings → X: four OAuth 1.0a values, **Test**, posting-ready.
2. Auto-reply: trusted handles (or `anyone`), mode **`draft`**, Save (poller starts).
3. Add cards only for topics you want a locked side on. One-offs use the mention emoji/word.
4. **Try it** (dry run) until the voice is right.
5. Real mention from another account, then Refresh or `/x poll`. Approve from Conversations.

## Commands

```
/x roast <url> | <text>    draft a reply
/x approve <summon_id>     publish a held draft
/x deny <summon_id>        discard a held draft
/x retry <summon_id>       re-run a skipped/failed summon
/x delete <summon_id>      delete the posted reply on X (or drop the log row)
/x list                    recent summons
/x subjects                declared cards
/x poll                    one mentions poll (paid plan; ignores stuck cursor)
```

## Kill switches

`KAZMA_X_REPLY=0` stops auto-reply; scheduled posts still work.  
`KAZMA_X_POST=0` stops every X write.

## Config keys

`connectors.x.reply.*` in ConfigStore (Settings panel writes these).

| key | default | meaning |
|---|---|---|
| `enabled` | `false` | master switch |
| `mode` | `off` | `off` \| `draft` \| `auto` |
| `summoner_policy` | `allowlist` | `allowlist` \| `anyone` |
| `summoners` | empty | trusted handles; empty allowlist = nobody |
| `allow_emoji_mood` | `true` | mention emoji may set tone (and unmatched side) |
| `stance_check` | `true` | draft must argue the side |
| `unmatched` | `skip` | `skip` \| `voice` when nothing matches and the mention sets no side |
| `classify_llm` | `false` | guess a card when keywords miss (can misfire; leave off) |
| `use_knowledge` | `false` | pull fenced KB snippets |
| `knowledge_library` | empty | library id, or all |
| `trigger` | empty | optional phrase in the mention |
| `max_replies_per_day` | `5` | across targets |
| `max_replies_per_target_per_day` | `1` | per account |
| `cooldown_per_thread_s` | `3600` | one reply per thread per hour |
| `min_target_followers` | `500` | floor; `0` off |
| `poll_interval_s` | `600` | 60s floor |
| `subjects` | `[]` | cards (`id`, `side`, `match`, optional extra) |

## Limitations

- Mentions poller needs a paid X plan. Reads are metered per month.
- Replies fire only while Kazma is running. A mention during downtime is picked up on the next poll if it is still in the mentions window.
- Drafting is outside the supervisor graph: no tools, no chat memory, unless you turn on KB grounding.
- The public reply cannot be attached to a tweet that does not mention this account (X API).
- `drafting` with no buttons meant a stuck model call — Retry is shown on that status; `/x retry <id>` works even before a reload.

## Testing it safely

1. **Try it** — nothing leaves the machine.
2. **`/x roast`** — holds for approve.
3. **Real mention** from another account, then Refresh. Score Conversations, not the model's table.
