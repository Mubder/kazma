# X auto-reply

Kazma can reply when someone mentions it — in a tone the **emoji** picks.
It is off by default.

**Subjects are optional.** With none declared, every summon still gets a
reply in voice-only mode: react to the post, don't invent a crusade, emoji
sets roast / angry / dry / supportive. Add a subject when you want a
*declared view* on a topic; those still win over the default voice. A
hallucinated topic still cannot become a reply.

## Two ways to summon it

**Paste (works on every X plan).** You reply `what do you think Kazma? 😂`
under a post, then send Kazma the link:

```
/x roast https://x.com/someone/status/1234567890 | Their post text here
```

It drafts, you approve, it posts. The text after `|` is the post being
replied to — on a paid plan you can omit it and Kazma fetches it.

**Mentions poller (paid plan only).** Kazma polls its own mentions timeline
and handles summons automatically. `GET /2/users/:id/mentions` is **not
available on the Free tier**; it returns 403 there. The poller logs that
clearly and backs off rather than hammering it.

Both triggers feed the same drafting core, so anything you tune with `/x
roast` behaves identically when the poller fires it.

## Configuration

Stored under `connectors.x.reply.*` in the ConfigStore.

**Settings → Integrations → X → Auto-reply.** The panel covers everything in
the table below, with a subject editor and a **dry run** that drafts against a
pasted post without publishing or recording anything — that is the loop to use
when tuning a `view`, since iterating on real summons is slow, capped, and
irreversible.

Saving validates. Subjects are the one config where a malformed entry is
silently *inert* rather than rejected, so a missing `view` or keyword would
otherwise become a subject that simply never fires with nothing to explain why.

Everything is also reachable through `PUT /api/settings` with the raw
`connectors.x.reply.*` keys if you would rather script it, and `/x subjects`
reads the current set back from any chat platform.


| key | default | meaning |
|---|---|---|
| `enabled` | `false` | master switch |
| `mode` | `off` | `off` \| `draft` \| `auto` |
| `summoner_policy` | `allowlist` | `allowlist` \| `anyone` |
| `summoners` | *(empty)* | trusted handles. Under `allowlist`, **empty means nobody** |
| `allow_emoji_mood` | `true` | let a trusted summoner's emoji set the tone |
| `stance_check` | `true` | verify the draft argues your view before posting |
| `trigger` | *(empty)* | optional phrase that must appear in the mention |
| `max_replies_per_day` | `5` | across all targets |
| `max_replies_per_target_per_day` | `1` | per account |
| `cooldown_per_thread_s` | `3600` | one reply per thread per hour |
| `min_target_followers` | `500` | floor for unsolicited replies |
| `poll_interval_s` | `600` | mentions poll cadence (60s floor) |
| `subjects` | `[]` | the list below |

### Modes

`draft` is the default and the one to start with. Kazma composes, holds the
draft, and pushes it to you; you post it with `/x approve <id>`. The round
trip is a few seconds — indistinguishable from typing it yourself, and you
keep the veto for the one in twenty where the model misreads the room.

`auto` posts without asking, once the summoner allowlist, subject match,
three caps and the content screen have all passed. Understand what you are
turning on: text you have not read, published under your name.

`/x roast` always runs in `draft` mode regardless of this setting. You typing
the command is the approval step.

### Who can summon it

`summoner_policy: allowlist` (the default) means only the handles in
`summoners` can trigger a reply, and an empty list means **nobody** — a config
mistake must never open the account to the world.

`summoner_policy: anyone` lets any account that mentions you trigger a reply.
Every other rail still applies: the post must match a declared subject, the
daily / per-account / per-thread caps hold, the follower floor holds, and the
content screen runs. What changes is only *who may ask*.

`anyone` + `auto` is the one combination where a stranger's tweet publishes
under your name without you reading it. The panel warns on save rather than
refusing — it is your account — but start with `draft`.

### Tone by emoji

"what do you think Kazma? 😂" and the same line with 🤬 should not get the same
reply, so the emoji in the summon dials the tone:

| emoji | mood |
|---|---|
| 😂 🤣 😹 💀 🔥 | roast |
| 🤬 😡 😠 👿 | angry |
| 🙄 😐 😑 🫠 🤨 | dry |
| ❤️ 👏 💯 🙏 👍 | supportive |

The first recognised emoji wins, so "😂 but seriously 🤬" reads as the joke it
led with. An unrecognised emoji is ignored rather than defaulting, leaving the
subject's own mood in place.

**Tone only.** The emoji cannot reach the subject's `view` or its `hard_lines`.
The worst a hostile summoner could do is pick which of *your* registers the
reply arrives in — which is why the next rule exists anyway:

**Only trusted summoners can dial it.** Under `anyone`, a stranger may summon
but gets the subject's declared mood. Letting someone else choose whether you
answer angry is a small manipulation lever with no upside. Set
`allow_emoji_mood: false` to pin every reply to its subject's mood.

### Subjects

```yaml
subjects:
  - id: var
    match: ["var", "offside", "تحكيم"]
    mood: roast          # roast | angry | dry | deadpan | supportive
    register: gulf       # dialect / tone hint
    view: |
      VAR has made football worse, not fairer. Three minutes of freeze-frame
      to overturn a call nobody in the ground disputed is not precision, it
      is theatre. I have no patience for "but it got the decision right".
    hard_lines:
      - never name or mock an individual referee
    examples:
      - "Four minutes to draw a line through a knee. Riveting stuff."
```

The subject here is deliberately mundane. Pick whatever you actually have
opinions about — the mechanics are the same whether it is refereeing or
something with real stakes, and the guardrails below matter more the higher
those stakes are.

`match` is checked as **whole words** for ASCII keywords, so `var` does not
fire on `variable`. Non-Latin keywords fall back to containment.

`view` is what Kazma argues from, and the prompt tells the model it is "the
ONLY view you may argue" and that it is explicitly not a neutral assistant.

Write a position, not a topic. Three things make one hold:

1. **State the premise as settled**, not as a question — "that is my starting
   premise, not a conclusion I am open to relitigating in a reply".
2. **Name the counter-framings you expect to meet**, so the reply has
   something to push against rather than inventing an angle.
3. **Fix the target.** Name the institution, the decision or the argument —
   never a group of people. "The rule and the people who wrote it" rather than
   a nationality or a faith. That is not softening the view; it is what makes
   it land, and on any charged subject it is the difference between a sharp
   account and a suspended one.

`examples` are worth more than `view` for voice. Two or three replies you
actually wrote will pin the register harder than a paragraph describing it.
This is the lever to reach for first when output feels off.

### Answering everything (the emoji decides)

If you want Kazma to reply to **whatever** you summon it under, with the emoji
picking the tone, give a subject the single keyword `*`:

```yaml
subjects:
  - id: general
    match: ["*"]
    mood: dry
    view: |
      React to what the post actually says, in my voice: short, dry, and
      unimpressed by padding. Don't hedge and don't summarise back at them.
```

Then 😂 gets a roast, 🤬 gets an angry one, 🙄 a dry one — off the same subject,
on any topic.

Three things to know:

- **Specific subjects are still tried first.** A catch-all is the floor, never
  the ceiling, so adding one does not blunt the subjects you already wrote.
- **It costs no model call to match.** A catch-all short-circuits before the
  classifier, so "answer everything" is also the cheapest rule.
- **The stance check is skipped for it.** That check asks "does this draft
  argue the declared position?" — a catch-all's view is a *voice*, not a
  claim, so every draft would come back `fence` and block itself. The content
  screen and the hard lines still apply, and so does `draft` mode.

This is still a declared subject: you wrote the view, it carries your hard
lines, and nothing is improvised. What it drops is having to predict the topic
in advance.

### Classification

Keyword match runs first: deterministic, free, auditable, and it never calls
a model. Only when nothing matches does an LLM pick — and it chooses from
your declared subject **ids**, using the *view* not just the keywords. A
hallucinated id is discarded. If nothing fits, the reply still goes out in
**voice-only** mode (emoji sets the tone) rather than staying silent.

### Stance check

The content screen checks rules that are the same for every subject — threats,
length, emptiness. **It has no idea what your position is.** A draft that
quietly argues the other side, or sits on the fence, passes it cleanly.

For a feature whose premise is "argue the view I wrote", that is the failure
that matters: not a rude reply, but Kazma agreeing with the person you summoned
it to answer.

So after drafting, one short model call classifies the draft against your
`view`: **argues**, **contradicts**, or **fence**. The last two block the
draft the same way a banned construction does. It is closed-set — any other
answer is treated as a non-answer, so the check cannot invent a verdict or be
talked into approving.

**When the check itself cannot run** (no provider, a timeout, a garbled
answer) the behaviour is deliberately asymmetric:

| mode | unusable check |
|---|---|
| `auto` | **blocks** — publishing an unverified reply under your name is the thing this exists to prevent, and a model outage is not a reason to relax it |
| `draft` | **allows** — you read the draft before it posts, so you are the check |

Costs one extra short call per reply. Turn it off with `stance_check: false`
if you would rather not pay it — the panel warns if you do that while in
`auto`, because then nothing verifies a reply before it publishes.

## Guardrails

Beyond the caps above, every draft passes:

- **Universal hard lines**, added to whatever you wrote: no attacks on
  protected characteristics, no slurs, no threats, no claims about private
  individuals, criticise institutions and arguments rather than peoples.
  You cannot switch these off per subject.
- **A post-generation screen.** Checked against what the model actually
  wrote, not what it was asked for, because a model in `roast` mode drifts
  and the drift is always toward the thing the hard line forbade.
- **The existing X post policy** — length, @mention count, hashtags,
  duplicate window, daily and monthly caps — unchanged, applied on publish.

The follower floor is the one people ask about. Mocking a large account is
banter; the same text aimed at someone with forty followers is pointing a
bot at a stranger, and that is what gets reported as targeted harassment.
Set it to `0` to disable, knowingly.

## Turning it on

1. Save the four OAuth 1.0a credentials in Settings → Integrations → X and hit
   **Test** — auto-reply refuses to draft until the connector is posting-ready,
   and the panel says so rather than failing silently.
2. Subjects are optional. Skip them to reply to everything, emoji picking
   the tone. Add one with a `view` only when you want a declared position.
3. List your own handle under **Trusted handles** (or set who-can-summon to
   anyone).
4. Set the mode to `draft` and save — the poller starts on Save.
5. Use **Try it** to draft against a pasted post until the voice is right.

**Saving auto-reply starts or stops the mentions poller.** You do not need a
restart to turn it on. Modes, caps, subjects and views are also read live on
every summon, so a view that is landing badly can be fixed and retried from
X Studio → Conversations.

`/x roast` and `/x poll` work without the poller running.

## Testing it safely

Test in three layers. Do **not** start on X — the first two never touch your
account.

**1. Dry run (nothing leaves the machine).**
Settings → Integrations → X → Auto-reply → **Try it**. Paste the text of a post,
pick a tone, hit *Draft a reply*. Nothing is published, nothing is recorded, no
summon is consumed, and no cap moves. Iterate on the `view` and `examples`
here until the voice is right — this is what the panel exists for.

**2. Manual summon from chat (posts only when you approve).**

```
/x roast https://x.com/someone/status/123 | the text of their post
```

Kazma classifies, drafts, screens, stance-checks, and **holds**. You get the
draft back with `/x approve <id>`. Nothing posts until you run that. `/x roast`
forces draft mode whatever your configured mode is, so this step cannot
surprise you.

Your gateway login is the authorization here — you do **not** need to be in
`summoners` for this, and adding your X handle would not help: the command
passes a gateway identity (`telegram:12345`), not a handle on X.

**3. A real mention (paid plan only).** From a *different* X account, mention
the bot (a reply under someone else's post, or a standalone `@handle 😂`).
Then `/x poll` to force a cycle rather than waiting for the interval. In
`draft` mode you still approve before anything posts — from Conversations
or `/x approve`. Skipped rows have a **Retry** button that re-runs them
against current config.

`/x list` shows every summon with its outcome, including the skipped ones, so
"nothing happened" is always explainable. Set `KAZMA_X_REPLY=0` to stop the
whole thing instantly.

## Operating it

```
/x roast <url> | <text>    draft a reply
/x approve <summon_id>     publish a held draft
/x deny <summon_id>        discard a held draft
/x retry <summon_id>       re-run a skipped/failed summon
/x list                    recent summons and their state
/x subjects                what Kazma has views on
/x poll                    force one mentions poll (paid plan)
```

### The Conversations tab

**X Studio → Conversations** shows whole exchanges rather than a list of
outgoing posts: what the other account said, who summoned Kazma and how (emoji
included), and what Kazma replied — or why it didn't.

The posted list on the Studio tab cannot answer this. A reply read without its
parent is a non-sequitur, and the most common question is the one about the
replies that never happened. Skipped and failed summons are listed for exactly
that reason. Approve, Deny and Retry sit on the row — Retry re-runs a skip
against current config, which is how a config fix reaches a mention the
cursor has already passed.

Both sides are captured at claim time, not fetched later — the poller has them
in hand, re-fetching costs read quota, and a deleted tweet is gone for good.
The parent post is stored up to 2,000 characters and the summon up to 500.

Every summon is also in `kazma-data/x_replies.db` with its subject, draft,
outcome and reason. Every X API call is in `x_audit.db` as before.

## Kill switches

`KAZMA_X_REPLY=0` stops auto-reply and leaves scheduled posts working.
`KAZMA_X_POST=0` stops every X write, replies included. Both beat any stored
config.

## Limitations

- The mentions poller needs a paid X plan. Check **Products → Subscription**
  in the developer portal for your monthly read cap before lowering
  `poll_interval_s` — reads are metered per month, not just rate-limited.
- Replies fire only while Kazma is running. A summon that arrives during
  downtime is picked up on the next poll if it is still inside the mentions
  window, and lost otherwise.
- Drafting happens outside the supervisor graph (like swarm aggregation), so
  replies have no tool access, no memory, and no conversation context. They
  see the post and your declared view, nothing else. That is deliberate.
