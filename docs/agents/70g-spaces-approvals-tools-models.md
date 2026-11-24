<!-- AGENTS.md, piece 70g-spaces-approvals-tools-models. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **An approval card goes to the person it asks (S2-05, 2026-11-22;
  decisions N4 and N21).** Only where Spaces is enforced; an install that
  does not enforce it shows, lists, pushes and decides every card as
  before.
  - **One rule** (`kazma_core/spaces/approvals.py`, `_answerers`): a card
    asked for a person other than the admin who holds Development, about
    an always-ask action or a folder grant (`call_preflight.ALWAYS_ASK_TOOLS`),
    in a shared space with a project folder, is answered by the admin
    (N21); else by the asker's approvers, when the admin set some
    (`identity` `policies[asker]["approvers"]`, N4: empty for everyone, no
    page sets it yet; they answer in the asker's place); else by the asker;
    a card that names no asker by the space's owners. `answer_refusal`
    (`not_yours`, `identity_unavailable`) is what every surface asks.
  - **The card names it**: the graph adds `asker`, `answered_by` and
    `answered_by_names` to its interrupt (`graph_tool_worker._whose_card`,
    the security and the clarify card), and every copy of a card keeps them
    (`kazma_ui.hitl_answerers.card_who`: the stream's saved card, the
    checkpoint scan, the registry's pending items); the registry row's
    `asker` is the card's own, never whoever registers it
    (`hitl_gate_bridge`). `kazma_ui/hitl_answerers.py` finds whose card it
    is for a chat (`whose_card`: the row, the card, the chat's opener).
  - **Asked everywhere a card is decided or shown**: the approve route
    (403 `not_yours`, before anything is claimed: the gate stays pending)
    and its bridge-gate branch; the one decision writer
    (`hitl_decision.record_gate_decision` raises `GateNotYours` for a
    person's answer; the watchdog's timeout is nobody's), so the chat apps'
    buttons too; the pending list (`answerable`) and the clear route
    (`clearable_thread`: only chats whose every card is the caller's); the
    swarm (`single_operator.answered_by_caller` asks the one rule, with
    the request's tool for N21); the bell (`bell_items`: the person's
    waiting cards, a tool's name, never its arguments); Web Push
    (`push.notify_push_approval`, from the `approval_required` frame, to the
    devices of the accounts the card names, under the notifications switch).
  - **A resumed turn is its asker's**, whoever answered:
    `hitl_answerers.as_the_asker` binds the card's asker around the resume
    task, on the approve route and the timeout watchdog's journal resume.
    An approver or the admin pressing for someone never runs their turn as
    themself.
  - **The page**: a card for someone else shows "Waiting for {names} to
    answer" (`chat.hitl_waiting_for`, EN/AR) with its buttons hidden
    (`chat.js` `_hitlWaitingFor`, `_markHitlWaiting`; the page's account is
    `<meta name="kazma-account">`).
  - Gates: `tests/test_approvals_reach_the_asker.py` (another member is
    refused and the gate stays pending; the thread-owner rule as the
    control; an approver's answer resumes as the asker, with no binding as
    the control), `tests/test_development_per_person.py` (N21 over every
    always-ask tool), `tests/js/test_hitl_waiting_for.js`,
    `tests/e2e/test_approval_card_reaches_the_asker.py` (by the sidebar,
    EN/AR: the member sees the card waiting with no button, is refused, the
    owner answers).
- **A standing approval belongs to the person and the chat (S2-06,
  2026-11-22).** Only where Spaces is enforced; an install that does not
  enforce it keys every grant by its chat's thread, as before (nothing
  moves).
  - "Allow tool" (`safety.hitl_grants`), "Approve for task"
    (`safety.task_grants`) and a YOLO window (`safety.yolo`) are kept under
    `safety/grant_holder.holder`: the thread id, and once enforced the
    thread and the person (`<thread>~<account>`; an account id holds no `.`
    or `~`, so one person's prefix never matches another's). Every function
    of the three takes `person`; with none it is the person bound now -- the
    turn's asker in the chat graph, the task's in a swarm worker -- so
    `hitl.standing_approval`, the one answer every gate asks (the chat
    graph, the swarm bus, the MCP gate), reads the asker's own. Work with no
    person bound is granted nothing and skips nothing.
  - A grant made from a card is the card's asker's, whoever answered it
    (`routes_direct.misc._apply_scope_grants(person=)`, from S2-05's
    `whose_card`); `/yolo off` ends the person's own window and grants. A
    space's erase finds a chat's grants by the thread before the `~`
    (`space_data._settings_keys`, `grant_holder.thread_of`). Log lines keep
    `thread=` the chat's thread.
  - Gate: `tests/test_grants_per_person.py` (a grant, a task grant and a
    YOLO window of one person never skip another's card; the real approve
    route grants to the card's asker; the per-thread grant as the control;
    with Spaces off the keys are the thread's).
- **The S2-05/06 review's fixes (2026-11-22).** Only where Spaces is
  enforced.
  - **Whose card it is comes from the live interrupt**: the approve route
    and the timeout watchdog ask `whose_card` with the live interrupt's id
    and payload, never an id the request names, and the card's `asker`
    wins over a row's. Two people's identical cards are two gate rows
    (`hitl_gates._askers_differ`: the hash id and the arguments' twin match
    never merge across askers), and claiming one never supersedes the other
    (`_supersede_pending_twins`, the second review: Omar's identical card
    was dropped unanswered when Reem's was claimed). A clarify or confirm
    card's tools are its `items[].tool` (N21 holds when it is answered).
  - **An MCP server's sampling request is a card**: it names its asker
    (`spec_client._sampling_card_who`), the approve route asks the rule
    before `resolve_sampling_hitl`, and the pending list keeps the
    process-wide requests to the space and the person who may answer
    (`hitl_answerers.cards_of_this_space`).
  - **N4: nobody skips their own cards for their approvers**
    (`spaces.approvals.standing_grant_refusal`, asked by `enable_yolo`,
    `grant_tool`, `grant_task` and `/unrestricted`): a person whose cards
    the admin sent to approvers gets `approvers_decide`; an approver's
    grant from the card they answered passes. A chat app's swarm button
    asks the whole rule for the owner (`shared_approvals._a_chat_button_may_decide`).
    Cancel and Retry of one's own swarm task ask whose task it is
    (`single_operator.task_is_callers`), Approve and Reject who answers it
    (`task_actions.caller_may_act_on(answering=)`). With Spaces off the
    Swarm decide route reads no request before deciding. **A standing
    approval reads the approvers when it is used** (the second review: YOLO
    or a grant a person made before the admin set approvers kept skipping
    their cards): every grant records who made it (`granted_by`,
    `approvals.made_by_a_person`), and `hitl.standing_approval` lets one
    hold for a person with approvers only when an approver made it
    (`approvals.approvers_now`; an identity store that cannot be read holds
    none). Gate: `tests/test_standing_approvals_read_approvers.py` (the
    supersede by arguments and the grant read alone as the controls).
  - **The watchdog**: once enforced, whose card it is and its chat-app
    route are read before the timeout is recorded (a read that fails leaves
    the card pending and claimed by nobody; the next pass tries again). The
    third pass that cannot read it records the timeout anyway and closes the
    turn with a line, resumed as nobody (`hitl_timeout._close_turn_unread`,
    `_READS_BEFORE_GIVING_UP`): a card never stays pending for good. With
    Spaces off the watchdog records first and reads the route after, as
    before (the full suite's three watchdog tests). A card whose asker left
    its space ends the turn with a line saying why
    (`turn_budget.write_turn_end`), resumed as nobody.
  - **Every resume runs as the turn's asker**: a turn a restart cut off
    continues as the author of the message that OPENED the turn
    (`hitl_answerers.whose_turn`, `_turn_author`: the newest user row before
    the open turn's reply row, never a steer's row -- marked `steer` -- nor a
    later row; read in the chat's own space), not at all when they left.
    Gate, per call site (the second review: a binding anywhere in a
    function passed every resume in it, and a chat app's continue and the
    watchdog's chat-app resume ran unbound): every call that runs a resume
    -- a resume command, a function that builds one, or a turn continued
    from its checkpoint, given to what runs a turn or handed to a chat app's
    resumer -- sits inside `with as_the_asker(...)`/`bind_scope(...)`, or
    its function is declared (`tests/test_resumes_run_as_the_asker.py`,
    planted unbound resumes, one beside a binding, as the controls).
  - **Another member's message never drops a card**: a new message or
    voice utterance in a chat whose pending card waits on someone else is
    refused with a line naming them (`sse_chat._card_waits_on_someone_else`),
    nothing saved; the asker's own message goes on as before.
  - **Nobody else ends or steers a member's turn** (the second review): one
    rule, `hitl_answerers.turn_control_refusal`, asked by the routes through
    `sse_chat._turn_control` before anything is saved, queued or cancelled.
    A steer (`/api/chat/steer`, soft or hard, running or paused) is its
    asker's only; stop and abort (`/api/chat/stop`, `/api/chat/abort`) its
    asker's or an owner of the space; `/reset` and `/compact` the chat's
    opener's or an owner's, never while another member's turn runs or
    waits; a new message or voice utterance while another member's turn
    runs is refused with a line saying the chat is answering them (it would
    cancel the turn; the same person's message supersedes as before). Each
    refusal is a line in the person's language (`sse_chat._TURN_LINES`) the
    page shows; who it is that cannot be read refuses
    (`identity_unavailable`). Gate: `tests/test_turn_control_per_person.py`
    (through the real routes; the routes without the check, and the
    newest-row rule, as the controls).
  - **The long-task record is each person's** (`long_task._record_key`,
    `grant_holder.holder`): one member's `/long` or `/unrestricted`, and the
    YOLO clock it slides, never reach another's turns.
  - Gate: `tests/test_approvals_review_fixes.py` (each with today's code as
    its control; also the bridge-gate branch, the gateway's `/hitl`, the
    identity-unavailable 503, the push wiring, the bell's route, N21 through
    the route with the resume as the holder).
- **A tool list per person (S2-01, 2026-11-22; plan 4.8: "X Studio on, but
  posting off").** Only where Spaces is enforced; an install that does not
  enforce it refuses nothing, and People refuses the switch (`spaces_off`).
  - Inside the features a person has, the admin switches single tools off
    for them: `identity` `policies[account]["tools_off"]`
    (`features.TOOLS_OFF_POLICY`), read into `Grants.tools_off`.
    `features.tool_refusal` refuses such a tool, so it is neither offered
    nor callable wherever a feature's tools are refused (S2-11: the chat
    offer, the registry, a scheduled job's executor, a swarm worker, the
    MCP branch).
  - **Off at every door, not only at the call** (the second review: X
    Studio's Post now and Delete, a Scheduled booking, a booked post when it
    fired, the intent engine's deep research and the Swarm, Research and
    Documents pages reached the same effects). Every door that is not a
    call of the tool is declared in `spaces.features`: a route door
    (`TOOL_ROUTE_DOORS`, `route_scopes.tools_of_route`; the tenant
    middleware answers 403 `tool_off` with a line, `features.tool_off_text`)
    or a work door (`TOOL_WORK_DOORS`: the X choke points
    `x_api.booking.publish_x_post`, `delete_x_post`, `book_x_post` and
    `x_api.threads.publish_thread`; a booked post when it fires, through
    `x_api.ownership.x_work_scope(tool=)`, which waits as `ToolOff`; the
    intent engine's research handler; the Documents page's generate by
    format and the Knowledge page's ingest by kind; X Studio's queue). Each
    asks `features.tool_work_refusal` (or `tool_door_refusal` for the person
    bound now): the account disabled, the tool's feature off, or the tool
    switched off. Every route that changes something in a space is a door
    or declared not one with why (`NOT_A_TOOL_DOOR`: the Memory page's
    edits stay the person's, the chat itself, answering a card...). A new
    page action reaching a tool's effect is a door or a reason.
  - **Who may use what that cannot be read refuses every tool**
    (`spaces.tools.tool_grants`: `Grants(unreadable=True)`, an always-on
    tool included; the second review: an empty grant let a switched-off
    chat or memory tool run while the identity store was away).
  - **N4 in X Studio**: a person whose cards go to approvers posts and
    deletes on X only through the chat's card; their own click (Post now,
    Delete) is refused with a line naming the approvers
    (`spaces.approvals.own_click_refusal`, 403 `approvers_decide`).
  - Only a tool a person can have is switched (`features._switchable_tools`:
    each feature's tools and the always-on ones, chat and memory;
    `checked_tools` refuses `unknown_tool`, `bad_tools`, and
    `admin_has_every_tool`). People sends the whole list
    (`people.set_tools`, `PUT /api/people/{account_id}/tools`, each change a
    `tools_set` event of the identity record); `GET /api/people` lists each
    person's switchable tools by where they come from
    (`features.listed_tools`: a feature that is off for them has none).
    S1-33's check of the features holds while every stored list is a list.
  - Settings -> People -> Tools: a box per tool for each person but the
    admin, EN/AR. **A Settings mixin member is defined once**: the first
    draft named its handler `toggleTool`, the Tools tab's own switch, and
    the box sent `PUT /api/settings/tools/[object Object]/toggle`
    (`tests/js/test_settings_mixins.js` now fails a name defined twice
    across the mixins, the planted second `toggleTool` as its control).
  - Gates: `tests/test_person_tool_policy.py` (a tool off for a person is
    neither offered nor called in chat, a scheduled job and a real swarm
    worker, another person keeps it; hiding it from the offer only as the
    control; the switch's refusals, the route, the precondition),
    `tests/e2e/test_person_tools_browser.py` (by the sidebar, EN/AR),
    `tests/test_tool_doors.py` (the doors both ways: every changing route of
    the built app is a door or a reason, every work door asks, every asking
    function is declared, each with a planted control; the route door
    through the real tenant middleware, the X choke points, a booked post
    waiting, the intent engine, N4 through X Studio's routes; the middleware
    without its doors and X Studio without the rule as controls).
- **Models per person (S2-03, 2026-11-22).** Only where Spaces is enforced;
  an install that does not enforce it, the admin, and Kazma's own work (no
  person bound) use every model the install offers, and read nothing more.
  - `kazma_core/spaces/person_models.py` is the one rule: a person's
    `policies[account]["models"]` (`MODELS_POLICY`), `{"allowed": [ref],
    "default": ref}` in `provider:model` references; no `allowed` is the
    install's list, no `default` the install's when they may use it, else
    their first.
  - **Refused at the call**: every `chat` and `chat_stream` of every provider
    class asks `LLMProvider._refuse_a_model_not_allowed` first (the identity
    store read off the loop, only with a person bound), which raises
    `LLMError(kind="model_not_allowed")` before any request:
    `friendly_llm_error` words it. `provider_adapters.build_client` names a
    client's provider row (its `provider_name` attribute), so a call is judged by
    `provider:model`; a model already naming its provider is judged as it
    is. Whatever chose the model -- a chat, a scheduled job, a swarm worker,
    research -- the person's call of a model they may not use never leaves.
  - **Their model where none was chosen**: `ModelRegistry.get_client(None)`
    and the supervisor's `resolve_live_client` ask `model_for_work`; a
    model they may not use -- a stored run_model, a router's hint, a
    checkpointed `last_model` (the name the provider returned, which may not
    be the configured id) -- gives way to their default, else the install's
    when they may use it, else the first they may use (the second review:
    with the install's default allowed it was kept, and the call refused the
    turn; `person_models._their_choice`); swarm model selection
    (`select_provider_for_task`) never picks one they may not use. Async
    callers of `get_client` call it in a thread. Gate: the second review's
    cases in `tests/test_person_models.py` (the old choice as the control).
  - **The pickers list only theirs** (`offered`): `/api/models/options` for
    the signed-in person, the chat apps' `/model` (a model not listed is "not
    offered"). The chat stream refuses a model the person names before
    anything runs (an `error` frame, `chat.model_not_allowed`, EN/AR).
  - People: `people.set_models`, `PUT /api/people/{account_id}/models`, a
    `models_set` event; `GET /api/people` gives each person's; Settings ->
    People -> Models (Every model, a box per model, their default), EN/AR.
    A change to one person's list (tools, models) waits for the one before
    it (`settings_ops.js` `_personChange`): two quick clicks built the second
    list from the state before the first.
  - Gates: `tests/test_person_models.py` (the picker, its route, `/model`,
    the chat stream, the call on both paths, a real swarm worker with a model
    the person may not use and one with none, every provider class asking
    with a planted class as control; the install's list as the control),
    `tests/e2e/test_person_models_browser.py` (by the sidebar, EN/AR).
- **Background work uses its creator's policy as it is when it runs; a
  disabled creator pauses their jobs (S2-07, 2026-11-22; R13).** Only where
  Spaces is enforced.
  - Nothing a person's work may use is frozen when it is booked: every door
    reads the person's features, tools, models and daily limit when the work
    runs (a scheduled fire binds its creator, S1-14; the checks are S2-11's
    `work_refusal` and `tool_refusal`, S2-01's tool list, S2-03's call check,
    S2-02's limit). A new kind of later work binds its person and asks them
    when it runs, never a copy made at booking.
  - **People disables and enables a person** (`people.set_enabled`, `PUT
    /api/people/{account_id}/enabled`, events `account_disabled` /
    `account_enabled`; disabling ends their sessions; the owner cannot be
    disabled). Disabled, `features.Grants.disabled` holds nothing for them:
    every door (`work_refusal`, which answers `features._disabled_refusal`) and
    every tool refuses their work, so it waits as work of a feature that is
    off does; their switches stay as stored (People shows them).
  - **A scheduled job of a disabled creator pauses**: the poll does not
    claim it (`identity.disabled_accounts`, read once a pass), a fire
    already started gives its claim back (`_fire_binding` refusal
    `creator_disabled`, `scheduler._PAUSES`), nothing counts as a failure;
    past its catch-up window the occurrence is missed with the reason as its
    last result (`mark_missed(reason=)`); enabled again, it fires. The list
    says why (`paused`, the Scheduled page's `scheduled.paused_creator_disabled`,
    EN/AR).
  - Gates: `tests/test_background_uses_creator_policy.py` (a real
    `CronScheduler` fire; the tools frozen at booking as the control),
    `tests/js/test_scheduled_page.js` (the paused note),
    `tests/e2e/test_person_disable_browser.py` (by the sidebar, EN/AR).
- **Limits per space (S2-08, 2026-11-22).** Only where Spaces is enforced;
  an install that does not enforce it reads nothing here and keeps the
  install's own limits, as before.
  - `kazma_core/spaces/space_limits.py` is the one rule. A space's limits
    (`SpaceLimits`): the bytes of its own folder (`disk_bytes`), its
    documents (`documents_bytes`) and its libraries (`knowledge_bytes`), and
    how many Knowledge crawls, research runs and swarm tasks it runs at once
    (`crawls`, `research`, `swarm`). Whose: the space's own
    (`identity` `spaces[id]["limits"]`), else the limits for spaces
    (`defaults["space_limits"]`), else `SHIPPED` (2 GiB of files, the
    install's 10 GiB of documents, 256 MiB of libraries, half of each of the
    install's slots: 2 crawls, 2 research runs, 5 swarm tasks). The owner's
    space has no folder of its own, so no disk limit.
  - **A share of the install's slots**: asked at admission, before a slot
    is taken, so a space at its share is refused and holds no place another
    space's work could run in -- the swarm in `SwarmEngine._dispatch`
    (after the install's cap, with the task's slot held while the share is
    read and only the space's tasks admitted ahead of it counted,
    `_running_ahead_of`; `admission_denied`, code `space_limit`), research in `bounded_pipeline` and
    `bounded_session` (`research_runtime._take_space_share`, counted per
    process, a session's own pipeline counted once), a crawl in
    `crawl_jobs.start` (the journal row names its `space`; a research
    pipeline's row is research's, never a crawl's). The install's own caps
    still bound every space together.
  - **Bytes**: the documents store's quota (`_space_quota` in both
    repositories, `ingestion._space_documents_bytes` for an intake) and the
    Knowledge share (`KnowledgeStore._space_over_its_share`) read the limit
    set for the space or for every space, else the install's own values
    (`documents.quotas.tenant_bytes`, the 256 MiB share) as before; the disk
    limit (`disk_refusal`, a folder measured at most once a minute):
    `check_path_access` refuses a write inside the bound space's folder at
    its limit, an upload is 507 `space_limit`, a shared copy, a generated
    image and a document made into the folder are refused. All admission
    checks: a write admitted under the limit may carry the space past it,
    and the next is refused. A new writer into a space's folder asks
    `refuse_write_for_disk` first.
  - People sets them (`people.set_space_limits`,
    `set_default_space_limits`; `PUT /api/people/spaces/{space_id}/limits`,
    `PUT /api/people/space-limits`; events `space_limits_set` /
    `default_space_limits_set`) and shows each space's usage beside its
    limits (`people.spaces_usage`, `GET /api/people/spaces/usage`: counts
    and sizes only, never a word of what a space holds); a space's editor
    holds its own limits only, an empty field taking the limits for spaces.
  - Gates: `tests/test_space_quotas.py` (one space at its share refused
    while another runs, for swarm tasks, research and crawls; the install's
    quota alone, the shares taken out, as the control: one space fills the
    install and the other is refused; each byte limit taken out as its
    control; the routes and refusals; nothing read without Spaces),
    `tests/e2e/test_space_limits_browser.py` (by the sidebar, EN/AR).
