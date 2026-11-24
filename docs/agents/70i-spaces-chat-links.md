<!-- AGENTS.md, piece 70i-spaces-chat-links. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **A person links their own chat-app account with a code (S3-01,
  2026-11-23).** Only where Spaces is enforced (`chat_links.linking_on`): an
  install with one account issues no code, and every message, code-shaped or
  not, goes where it always went.
  - `kazma_core/spaces/chat_links.py` is the one home. My account (Settings
    -> My account -> Chat apps, `static/js/chat_links.js`, catalog slice
    `chat_links.*`) asks `POST /api/account/chat-links/code` for a code
    (`chat_links.issue_code`: eight letters of an alphabet with no I, L, O, 0
    or 1, shown once, one use, ten minutes; a new one replaces the person's
    earlier code for that chat app). Only its hash is kept, in the
    CREDENTIAL settings key `chat_links.CODES_KEY`.
  - The person sends it to the bot in a direct chat (`/link ABCD-2345` on
    Telegram, the code alone elsewhere: `chat_links.send_text`). Every
    message accept path asks `SenderGate.link_attempted` BEFORE the sender
    rule (an unlinked sender is not refused first): a message that is a code
    (`chat_links.code_in`, pure, read on the loop) is never handed on,
    recorded as `link_code`, redeemed in a thread (`chat_links.redeem`) and
    answered, naming the account it is linked to. Never a forwarded message,
    nor a Telegram command for another bot.
  - The code is spent by the first message that reaches it, in the same
    atomic step that reads it (`chat_links._take`). A live code typed in a
    group, server channel or Slack channel, or sent on another chat app, is
    burned and links nothing (a group is told only that). A chat-app account
    linked to someone is never moved by another's code. A sender's codes are
    counted as they arrive (`chat_links.take_attempt`: five in ten minutes,
    a flood at once too).
  - Links are the identity store's `links` table (`identity._check_links`:
    a chat app, a user id, an account that is there, one account per
    chat-app account), each an event of the People record (`chat_linked`,
    `people.record_event`); deleting a person takes their links.
  - Gates: `tests/test_chat_link_flow.py` (the real Telegram, Discord,
    Slack and WhatsApp accept paths; a code that can be used twice as the
    negative control), `tests/e2e/test_chat_link_browser.py` (by the
    sidebar, EN/AR: the code shown, sent to the real Telegram adapter, the
    page says linked).
- **The sender rule is a linked, enabled account (S3-02, 2026-11-23).** Only
  where Spaces is enforced; an install that does not enforce it keeps the
  allowlist rule exactly (`sender_rule.refusal`).
  - `SenderGate.sender_refusal` asks `chat_links.link_refusal` once
    enforced: the allowlists, the owner's known ids and the open switch
    retire. Refusals: `not_linked` (told once a day, in a direct chat, how to
    link: Settings -> My account -> Chat apps), `account_disabled` (told so),
    `links_unknown` (before the links were first read: never let in, not
    told). Buttons ask the same rule (`actor_allowed`).
  - The links are read from memory on the adapter's loop. Every write of the
    registry in this process updates them in the writer's thread after its
    commit (a store change listener `chat_links.refresh_links` registers), so
    a link, an unlink, a person disabled or deleted counts at once; older
    than `chat_links.LINKS_TTL_S` they are read again in a thread (another
    process). The app reads them at start.
  - The owner's chat-app accounts Kazma already knows (`chat_links.known_owner_ids`,
    the one copy: `allowlists.known_owner_ids` asks it; the guard's Telegram
    chat, the delivery chat, `platform:id` entries of `KAZMA_GATEWAY_ADMINS`)
    become the owner's links with no step from the owner:
    `chat_links.adopt_owner_ids`, a step of `kazma spaces migrate`
    (`preconditions.plan`/`apply`) and of `preconditions.switch_on`; never a
    known id someone else linked. `--check` names each allowlisted sender who
    is not linked (`preconditions.notes`): they link with a code.
  - Settings -> Adapters & Routes shows no allowlist field once enforced (a
    note says who the bot answers); People lists each person's links
    (`list_people` `links`); the chat apps' Test says how many are linked
    (`connector_test.allowlist_check`).
  - Gates: `tests/test_chat_senders_are_linked.py` (every adapter's accept
    paths and buttons; the allowlist rule letting a listed stranger act as
    the owner as the negative control), `tests/e2e/test_chat_links_listed_browser.py`
    (by the sidebar, EN/AR).
- **A chat-app chat works in its space (S3-03, 2026-11-23).** Only where
  Spaces is enforced (`chat_apps.chat_place`, asked by the gateway's handler
  before it finds the chat's thread); an install that does not enforce it
  runs every message as before. It replaces "a chat app works in the owner's
  space" for linked senders.
  - **A direct chat** works in the space its person chose with `/space`
    (`chat_apps.space_command`: a list numbered from their spaces, then a
    number, a name, or "personal"/"شخصي"), their personal space at first, as
    them. **A group** (a Telegram group, a Discord server channel, a Slack
    channel: `chat_apps._is_direct`) works in the one shared space a member
    bound it to (`/space <name>`; never a personal space; a bound group moved
    or unbound -- "none"/"لا شيء" -- only by a member of its space); a sender
    who is not a member of it is ignored there, told nothing; an unbound group
    answers `/space` only and says how at most every ten minutes. A binding
    to a space the person left, or that is gone or being erased, counts as
    none.
  - **Each space keeps its own chats**: the chat's sender key
    (`ChatPlace.sender_key`, `chat_apps._key_for`) is the platform sender in
    the owner's space -- the owner's chats stay on the threads they had --
    and carries `--in-<space>` in any other; a group's is
    `<platform>:grp<chat>--in-<space>`, one chat for the group. The thread
    record (`chat_app_scope(thread_id, place)`) refuses a chat of another
    space than the place's.
  - The bindings are the settings key `chat_apps.BINDINGS_KEY`
    ("identity.chat_spaces", a CREDENTIAL: a restore never moves a chat into
    another space). `/space` is in the command catalog, `/help` and the
    slash-command reference, and is an always-on chat command.
  - Gates: `tests/test_chat_space_binding.py` (the real handler: a direct
    chat in its personal space, the owner's thread unchanged, `/space 2`
    moving it, a group's member run in its space and a non-member ignored;
    the sender's thread with no space as the control),
    `tests/test_chat_apps_once_enforced.py` (re-pointed: the owner's linked
    accounts).
- **A chat app's approval card is its asker's (S3-04, 2026-11-23).** Only
  where Spaces is enforced; before it, a `/hitl` decision still needs a
  gateway admin who owns the thread.
  - `agent_handler.hitl._handle_hitl_resume` asks the card's own rule
    (`hitl_answerers.refusal_for_card`, S2-05) on the LIVE card, before
    anything is recorded or resumed: the sender is the linked account the
    handler bound (S3-03); another person's press -- another member of the
    group's space, the admin, a sender outside it (answered in
    `_chat_place_or_answer`) -- is told whom it waits for and changes
    nothing. The resume runs inside `as_the_asker(whose)`; "Approve for
    task" grants the card's asker (`grant_task(person=)`), never an
    approver. A card someone else answers (N21's admin, N4's approvers)
    reaches the asker's chat without buttons, saying whom it waits for
    (`_build_approval_prompt`).
  - **Results go to the chat that asked**: a card answered from another
    chat (an approver's) sends the turn's answer and next card to the
    turn's own chat (its `_gateway` route, `_resume_destination`), never the
    presser's, which gets a receipt. A swarm task's result goes to its
    asker's own chat (`chat_apps.chat_is_the_askers`: a group bound to the
    task's space they are a member of, or their own chat in it), never
    another person's; a bus approval request of someone else still stays in
    its space (O1).
  - In a group, another member's message while a card waits on someone
    else is answered with whom it waits for, and the card is kept
    (`_waits_on_someone_else`): a new turn would drop it.
  - Gates: `tests/test_chat_approvals_asker.py` (the real handler; the
    gateway-admin rule, and O1 keeping results off every chat, as the
    controls).
- **A web turn's notices go to its asker's own chat (S3-05, 2026-11-23).**
  Only where Spaces is enforced; before it every web turn stamps the
  operator's Telegram, as always.
  - A link keeps the direct chat it was made from (`target`, recorded by
    `chat_links.redeem(target=)`; an adopted owner link its Telegram chat).
    `chat_links.address_of(account)` is a person's own address (Telegram,
    WhatsApp, Slack, Discord, in that order; a Discord or Slack link only
    once its direct chat is recorded), and `chat_links.is_their_address`
    says whether an address is theirs (one of their links, or a group bound
    to the job's space they are a member of).
  - `send_message.own_address` is the one fallback of
    `resolve_delivery_target`: the bound person's own address; the owner's
    falls back to the operator's Telegram; anyone else with none, and work
    that names no person, get "" -- never the owner's address for someone
    else. `web_gateway_block` (the web chat and live voice), the Scheduled
    page's booking and `schedule_task` take it.
  - **N41's refusal is lifted for a person's own address only**: a booking
    by anyone but the owner, and its fire, pass when the job's address is
    theirs (`scheduler._their_address`); addressed to anyone else's chat --
    or with no address of theirs (`needs_own_address`: the page and the tool
    let the scheduler say so) -- they are refused `no_own_address`, which now
    says how to link a chat app (EN/AR; the ready-made automations word the
    refusal's code too).
  - Gates: `tests/test_web_turn_delivery_target.py` (two people, two
    targets; a member's reminder booked and fired into their own chat; the
    owner's Telegram stamped on every web turn as the control),
    `tests/e2e/test_member_reminder_browser.py` (by the sidebar, EN/AR: a
    linked member books, an unlinked one is told to link).
- **Every chat-app command acts as the linked account in the chat's space
  (S3-06, 2026-11-23).** Only where Spaces is enforced: the handler binds the
  sender's linked account in the chat's place (S3-03) before any command, so
  every command's replies and turns run there; ``/space`` alone is answered
  before the place exists (it chooses it).
  - **Seasons** (`agent_handler/session_commands.py`): the pointer is the
    chat's own key in its space (`_mouth_key`: `ChatPlace.sender_key`, passed
    to `_handler_body` as `chat_key`); `/sessions` lists the bound space's
    chats (its chat store); a take-over is of a season of that space only
    (`_in_this_space`, the thread's home); the gateway-admin and owner rules
    of before stay for an install that does not enforce Spaces.
  - `/runs act` and the `/memory` change words are decided in the space
    (`slash_commands.decided_in_the_space`: `runs.decide`'s own rule, the
    bound tenant's facts), not by the gateway-admin rule; the rest of
    `changes_global_config` stays the admin's (S3-08).
  - **Buttons**: the adapters' admin gate for an approval or run card's
    press gives way to the card's own rule (`allowlists.press_needs_admin`,
    asked by every adapter's press gate) -- the sender rule already let a
    linked account in, and the handler asks whose card it is (S3-04); a swarm
    bus card stays the admin's (O1). In a group the card keeps its buttons on
    a press (`allowlists.press_keeps_card`: Telegram's keyboard and alert,
    Discord's components): another member's press changes nothing.
  - Gates: `tests/test_chat_commands_act_as_the_account.py` (a matrix over
    every catalog command through the real handler, each reply and turn
    bound to the space as the account; a command added needs a line in
    `SAMPLES`; every adapter's press gate asks `press_needs_admin`, a planted
    gate as its control; the sender-owner pointer as the control),
    `tests/test_press_gate_before_spaces.py` (before Spaces a non-admin's
    approval, swarm or run press changes nothing on Telegram, Discord and
    WhatsApp; the enforced rule applied without Spaces as the control).
  - **A member never steers, stops or clears another member's turn** (the
    S3-06 follow-up): `/steer` and `/steer!` (only the turn's asker),
    `/abort` and `/stop` (its asker or an owner of the space), `/reset` and
    `/compact` (the chat's opener or an owner, never while another member's
    turn runs or waits) ask the web chat's rule,
    `hitl_answerers.turn_control_refusal`, through `_turn_control_refused`
    in the handler; a refusal is said with the web's words
    (`hitl_answerers.turn_lines`) in the sender's language. Whose turn it is
    reads the chat store's rows, so every user row the gateway saves names
    its sender (`graph._carry_authors`: the k-th newest row of the same text
    keeps its author, the message a turn opens with takes the bound asker;
    a web row's author survives a gateway save). `/undo`, `/edit` and
    `/replay <step>` are not asked yet. Gate:
    `tests/test_chat_turn_control_per_person.py` (a Telegram group, a
    Discord server channel and a Slack channel through the real handler,
    WhatsApp's direct chat hers alone, EN/AR; the unrefused press and rows
    without their authors -- whose turn falls back to the chat's opener --
    as the controls).
- **Unlinking, and a disabled or deleted account, stops everything at once
  (S3-07, 2026-11-23).** Only where Spaces is enforced.
  - My account's Unlink (`DELETE /api/account/chat-links`,
    `chat_links.unlink`, recorded `chat_unlinked`) and deleting a person
    (`people.delete_person`) move the link to the identity store's
    `unlinked` table (`identity.retire_links`, with the chat it answered);
    linking it again takes it out. An owner's known id the owner unlinked is
    not linked again on its own (`owner_links_to_adopt`).
  - The sender is refused at once: the links are read again in the writer's
    thread (S3-02), so the next message and button press are refused at the
    adapter, and one already queued is not run (`chat_place`).
  - **What waits for them is dropped**: `chat_links.address_revoked(target)`
    (memory only, any thread) is true for the chats of an unlinked or deleted
    chat-app account and of a disabled account's links, never a chat still
    linked to an enabled account. `GatewayManager.send` drops a reply to such
    a chat (`gateway._dropped_for_its_person`), and the outbox's retry pass
    settles a waiting one `dropped` (`ChatOutbox.finish`, no notice owed):
    an expired card's notice from the watchdog, a swarm result, a reply that
    was not taken -- never sent. A disabled person's chats are answered
    again once enabled.
  - Gates: `tests/test_unlink_stops_everything.py` (the outbox, a queued
    message, a press, a pending card's expiry; disabled, deleted, linked
    again; a waiting reply still sent as the control),
    `tests/e2e/test_chat_unlink_browser.py` (by the sidebar, EN/AR).
- **A chat app's admin is the admin's linked account (S3-08, 2026-11-23).**
  Once Spaces is enforced `allowlists.is_gateway_admin` -- the one admin rule
  of the chat apps: admin-only commands (`changes_global_config`), install
  buttons, a swarm bus card's press -- asks
  `chat_links.linked_admin(platform, user)` (memory: a link to an enabled
  account whose install role is admin; a Discord sender's user, never its
  channel). `KAZMA_GATEWAY_ADMINS` and the allowlists grant nothing then
  (its `platform:id` entries became the owner's links when Spaces was
  switched on, S3-02); before Spaces they decide, as always
  (`_admin_by_lists`). Every admin-only refusal says how one becomes able
  to through `allowlists.admin_hint` (the handler's in the sender's
  language, `graph._admin_only_text`), never the retired list once
  enforced. Gate: `tests/test_chat_admin_is_an_account.py` (the rule, a
  listed member refused through the real handler in English and Arabic,
  the owner's linked chat passing; the environment list as the control).
- **The stage 3 review's fixes (2026-11-23).** Only where Spaces is
  enforced, unless said.
  - **A link is made from a person's own sign-in** (H1,
    `chat_links_api._own_sign_in`): the chat-link routes answer a browser
    session, or a credential with the admin role (the shared secret, an
    admin token); a token of any other role is refused, 403
    `own_sign_in_only`. Every API token is the owner's account
    (`space_binding.account_of`), and an operator token's code made its
    holder the chat apps' admin. Gate: `tests/test_chat_links_own_sign_in.py`.
  - **No API token acts as the owner on the person's own routes** (the
    account-route class, `auth.token_acts_for_the_owner`, asked by
    `auth.principal_allows`, the middleware's one role rule): once Spaces is
    enforced every route `route_scopes` classes ACCOUNT refuses an API token
    below admin (403 `own_sign_in_only`) -- My account's notices, chat links
    and model keys, Web Push's subscribe, the bell, the problems list.
    `auth.TOKEN_MAY_ASK` declares the few that describe the credential or
    the install, with why; the chat-link and notices routes ask the rule
    again (`people_api._own_caller`). An operator token read and dismissed
    the owner's notices and could subscribe its endpoint to the owner's
    answers. Gate: `tests/test_account_routes_own_sign_in.py` (every
    ACCOUNT route of the built app; the rule taken out as the control).
  - **The switch adopts only what was the owner** (M1,
    `chat_links.owner_link_plan`, `_was_the_owner`): a known id passes the
    sender rule of before (on the allowlist; with none set, the known ids
    or the open switch -- a set allowlist decides alone) and the admin rule
    of before (`KAZMA_GATEWAY_ADMINS` when
    set, else the allowlist, else the known ids); a Telegram group (a
    negative id) is never adopted; `--check` names every id adopted and
    every one left, with why. The core restates the gateway's rules (it does
    not import the gateway). Gate: `tests/test_switch_on_adopts_the_owner_only.py`.
  - **Codes** (L1, L2, `sender_rule.link_attempted`, `chat_links.prefixed_code`,
    `bare_code`, `live_codes_in`, `is_live_code`): a code alone from a
    linked sender counts only when it is live (a "next-week" is their
    message, never a guess counted against them); a live code anywhere in
    a message, its own word, is burned in a group and redeemed in a direct
    chat, never handed to the agent; the live codes are kept in memory (this
    process's issues and uses at once, another's through the store's change
    notice). A redemption is a `chat_linked` event by the chat-app account
    that sent the code, told to the account at its next page load
    (`people._TOLD`, `account_notices.js`), with Unlink and "It was me".
  - **The links in memory fail closed** (L3, L4): a read that fails forgets
    them (`chat_links._forget_snapshot`: no stale admin); `linked_admin`
    reads again past `LINKS_TTL_S`; before the links were read a reply waits
    in the outbox (`gateway._links_unknown`). A native command's unlinked
    sender is told how to link (L5); `--check` reads a list-typed allowlist
    as ids (`chat_links.split_ids`, L6). Gate:
    `tests/test_chat_link_codes_review.py` (each with the rule of before as
    its control), `tests/e2e/test_chat_unlink_browser.py` (EN/AR).
  - **An answer from another chat never sends the asker's work to the
    presser** (M2): `hitl_answerers.as_the_asker` clears the delivery target
    and platform when it binds someone else (the tool worker then takes the
    turn's own `_gateway` route); a card answered for someone else
    (`hitl._answered_for_someone_else`) resumes into the asker's chat only
    when `chat_is_the_askers` says the chat is theirs -- a group of their
    space, or their own chat (`chat_links.is_own_chat`) -- else nowhere, and
    the presser's chat gets only a receipt. Gate:
    `tests/test_hitl_resume_never_reaches_the_presser.py`.
  - **A press keeps the chat it was pressed in** (M5): a Discord press
    carries its `guild_id`, so a press in a server channel is not a direct
    chat; each adapter's press message says which kind of chat it is in
    (gate in `tests/test_press_keeps_its_chat.py`).
  - **No fallback to the owner's chat for someone else's work** (M6):
    `send_file`'s and the document handler's fallbacks answer
    `send_message.own_address`; every product function reading a
    configured chat id asks it or is declared (`tests/test_chat_fallbacks_ask_the_person.py`).
  - **A member sends only to their own chat or this conversation** (M7,
    `call_preflight._outbound_refusal`): every tool of
    `side_effects.OUTBOUND_TARGET_FIELDS` but those `OUTBOUND_NOT_CHECKED`
    names (the space's own mailbox; `send_file`, no recipient) is checked
    before the card and at the call (`PREFLIGHT_TOOLS`); the admin keeps the
    allowlist rule of before. Gate: `tests/test_member_outbound_targets.py`.
  - **A dropped reminder ends quietly** (L8): the scheduler's delivery pass
    asks `chat_links.address_revoked` first, and `finish_delivery(dropped=True)`
    ends the delivery `dropped`, the job done, no retry, no alert
    (`scheduled.delivery_dropped`; the digest counts it, not as a problem).
    Gate: `tests/test_cron_dropped_reminder_ends_quietly.py`.
  - **A waiting card is checked again under the thread lock, in every
    chat** (M3): another member's message that waited on the lock behind
    the turn that paused never drops its card (`graph._waits_on_someone_else`,
    asked before the lock and again before `cancel_pending_hitl`). Gate:
    `tests/test_card_wait_checked_under_the_lock.py`.
  - **`/undo`, `/edit` and `/replay <step>` are the chat's opener's or an
    owner's** (M4): action `rewind` of `hitl_answerers.turn_control_refusal`,
    the rule of `reset`, in groups and direct chats; the S3-06 command
    matrix runs in a group too. Gates: `tests/test_chat_rewind_per_person.py`,
    `tests/test_chat_commands_act_as_the_account.py`.
  - **A card is decided in the space it lives in** (L7,
    `hitl._card_space_for_presser`): after a `/space` switch, a member of
    the card's space presses it there, as themself; whose card it is is
    still asked on the live card. Gate: `tests/test_card_press_after_a_space_switch.py`.
  - **A group's binding holds while its binder is a member**
    (L9, `chat_apps._group_space`); **a forum topic is the group's**, named
    by the group, never the member who wrote (L11,
    `graph._resolve_thread_in_place`); **`/reset` clears the chat's own
    pointer in its space** (L10). Gate:
    `tests/test_chat_group_bindings_and_pointers.py`.
  - **A change to a member's fact is decided by them or an owner** (L13,
    `changes._not_theirs_to_decide`, code `not_theirs`): in a shared space
    a change filed under `member_<account>` is confirmed, undone or
    dismissed only by that member or an owner of the space, on the Memory
    page and with `/memory` alike. Gate:
    `tests/test_memory_changes_decided_by_their_person.py`. Asked before
    the memory mutation lock (`changes._decided_before_the_lock`: the
    identity store is no call to wait on under it, §15 D2), and an identity
    store that cannot answer is the refusal `identity_unavailable`, never a
    500: 503 with `Retry-After`, as the other outages are. Gate:
    `tests/test_memory_change_decided_before_the_lock.py`.
  - **Time Travel restores, forks and clears a chat of its opener or an
    owner** (`thread_rewind._theirs_to_change`, once Spaces is enforced):
    `rewind_thread`, `fork_thread` and `clear_thread_snapshots` -- the one
    service the page, the terminal app and the chat apps call -- and the
    web chat's and the chat apps' `/replay clear` ask the rule a chat app's
    `/replay` asks (`turn_control_refusal`, action `rewind`): 403
    `not_your_chat`, worded on the page (EN/AR) and in the chats. The space
    check alone let any member of a shared space take back, copy or delete
    another member's turns. Gate: `tests/test_time_travel_restore_per_person.py`
    (the real routes behind the real tenant middleware; the space-only
    check as the control).
  - **A whole chat is changed by its opener or an owner** (the review of
    3da533662): once Spaces is enforced the web routes that change a whole
    chat ask the rule `/reset` asks (`turn_control_refusal`, action
    `reset`): delete (`DELETE /api/chat/sessions/{id}`, through
    `sse_chat._turn_control`: never while another member's turn runs or
    waits), rename, archive, unarchive, pin and unpin
    (`sse_chat._whole_chat_refusal_sync`), and the Dashboard's delete
    (`DELETE /api/sessions/{thread_id}`; whether a turn waits read from the
    gate registry, `dashboard._turn_runs_or_waits`). 403 `not_your_chat`;
    503 with `Retry-After` when who it is cannot be read. The Dashboard's
    "Clear saved steps" clears every member's chats of the space: an owner
    of the space only (`dashboard._clear_all_refusal`, 403 `not_an_owner`).
    The space check alone let a member delete another member's whole chat,
    its steps and its record.
    The memory route that keeps a chat out and forgets its past asked
    S1-11's rule already (`speakers.may_keep_chat_out`). A new web route
    that changes a whole chat asks the same rule. Gate:
    `tests/test_whole_chat_routes_per_person.py` (the space-only check as
    the control).
