<!-- AGENTS.md, piece 70h-spaces-fairness-people-keys. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Fairness between spaces and between people (S4-04, 2026-11-23).** Only
  where Spaces is enforced: an install that does not enforce it reads
  nothing more, counts nothing, and every queue keeps its order.
  `kazma_core/spaces/fairness.py` is the one rule.
  - **A person's share** (`person_share`): in a shared space with more than
    one member, each person holds at most half of any of the space's
    shares, rounded up; in a personal space, or one with a single member,
    the space's. `shares(kind, space)` answers both;
    `space_limits.running_refusal(..., person=, person_running=)` refuses
    for either, and crawls (`crawl_jobs.start`, the journal row's `asker`),
    research runs (`research_runtime._take_space_share`) and swarm tasks
    (`engine._running_ahead_of`, the task's `asker`) ask it.
  - **Turns and scheduled jobs have shares** (`SpaceLimits.turns`, 8;
    `scheduled`, 2; both on the People page), counted in this process
    (`fairness.turn_slots`, `fairness.scheduled_slots`). A new turn takes its
    slot in `turn_segment_budget`, waiting up to `TURN_WAIT_S`; past it the
    turn is refused before it starts (`TurnSegment.refused`, `expired` too,
    the honest end written in the person's language, never recorded as
    failed: `turn_failed` False, as the daily-limit refusal) and the caller
    runs no graph: every caller of the budget checks `segment.refused` (a
    gate).
    An approval's resume is let in and counted; a turn inside one that
    holds a slot -- a sub-agent's, a scheduled task's -- takes none
    (`fairness.held`). A due job takes its slot in
    `CronScheduler._execute_bounded` before the scheduler's own semaphore,
    waiting up to one poll and holding nothing while it waits; one still
    beyond its share is left pending, unclaimed. A reminder (no model call)
    takes no slot (`CronScheduler._takes_a_slot`): behind a running task
    fire it would ring minutes late, or past its catch-up window. With
    Spaces off a slot is not even asked (no thread hop).
  - **Queues take turns**: the scheduler's due list by space, then by
    person (`SQLiteCronStore.due_jobs(fair=True)`); document claims, within
    a tenant, by the person with the fewest running (`fair_claim_order`'s
    `running_actor`, both backends; with Spaces off the order is the one
    before S4-04); the memory lane by space (N37:
    `memory_task_queue.claim_space`, filled at enqueue -- `enqueue_task(
    space=)`, else the payload's tenant, else the bound scope -- read by
    `_claim_batch`; never whose data a task is, which the space walk reads
    from the payload).
  - **Uploads and paid endpoints**: a space's documents are bounded by its
    queue and rate budgets (`documents.admission`, per tenant); every rate
    window (`kazma_ui.rate_limit`) is keyed by the person the tenant
    middleware bound, wherever they sign in from, and each request also
    counts in its space's window of `SPACE_WINDOW_FACTOR` (2) people's
    worth: both admit it or neither counts it.
  - Gates: `tests/test_space_fairness.py` (turns through the real
    `run_agent_turn` and graph, the scheduler's real poll on a real store,
    real document claims, the real memory claim, the real rate dependency;
    one queue for all as each control: one space's turns, jobs, claims or
    tasks first and another space's waiting; a caller of the budget that
    runs the graph anyway as the gate's control; the People page's limits
    in both languages).
- **A member's code runs only in a container, with no network (S4-05,
  2026-11-23, and its review).** Only where Spaces is enforced: an install
  that does not enforce it reads nothing more, and the admin's code keeps
  the rules it had. `kazma_core/spaces/member_code.py` `code_rule()` is the
  one answer for the work running now: anyone but the admin, in any space,
  runs `python_exec` in the Docker jail or not at all -- never the host
  fallback, whatever `KAZMA_CODE_EXEC_ALLOW_LOCAL` says
  (`code_exec.local_exec_forbidden` asks it first), and never E2B, which
  always has the internet (`sandbox.e2b.e2b_enabled`). Work no person asked
  for likewise. A chat-app turn runs as the person whose linked account
  sent it (stage 3, `chat_apps.chat_place`), so their code is held there
  too.
  - **No network for members' code, until S4-05b.** The first build let the
    admin allow it per person, and the jail then ran `--network bridge`: the
    review reached a host service through the LAN and WSL addresses, and the
    router's admin page, from the jail; cloud metadata (169.254.169.254) was
    open, and on Docker Desktop `host.docker.internal` reaches the host's
    loopback. Now every jail run is `--network none` with the network
    modules blocked, `allowed_network` answers False whatever is stored (a
    first-build allowance reads as not allowed), and People refuses turning
    it on (`people.set_code_network`: 409 `code_network_not_yet`, worded in
    both languages; off is taken and clears a stored value). The People page
    shows the switch off, disabled, "Not yet". S4-05b brings filtered egress
    on Kazma's own engine. Never give a member's container the engine's
    default network.
  - S2-12's member rules (`spaces.tools.MEMBER_DEVELOPMENT`) keep a member
    off every host process that runs a project's code; this holds the one
    code runner they keep.
  - Gates: `tests/test_member_code_in_containers.py` (the rule; per-space
    runs with no Docker refused, the local fallback reading a host file as
    the control; the refusal, a first-build allowance read as not allowed,
    today's allowance as the control; `--network none` for every container;
    E2B never; a chat-app turn of a linked person through the real gateway
    handler, the owner's own link as the control; a real Docker run where
    the sandbox image is present, `real_docker`: no network and no host file
    in the container, the local fallback reading it as the control),
    `tests/e2e/test_person_code_network_browser.py` (by the sidebar, EN/AR:
    the switch off, disabled, "Not yet", a stored allowance shown off).
- **Every action under the person's name (S2-09, 2026-11-22).** Only where
  Spaces is enforced; an install that does not enforce it records nothing
  more (the memory operator trail and the logs as before).
  - `kazma_ui/activity.py` `ActivityRecorder`, an ASGI middleware outside
    the tenant middleware: every request that changes something (`POST`,
    `PUT`, `PATCH`, `DELETE`) and reached a route is one row of the space's
    activity (`kazma_core/spaces/activity.py`, `activity.db`): when, the space
    it acted in, the account the tenant middleware bound (`kazma_asker` and
    `kazma_space` in the request: never a session label, a body or a header
    the client chose), the route's method and template, the ids in its path,
    the answer's status. Never a body or a query value. A request no person
    made writes nothing. One middleware: a route added later is recorded the
    day it is added.
  - The memory operator trail names the account too (`account:<id>`), the
    session's label only where no account was bound.
  - **The Activity page** (`/activity`, `GET /api/activity`; sidebar and
    palette only where Spaces is enforced): the space's actions to its
    members, newest first, with each person's name; the membership check
    every request passes is its only door, the admin's included (S1-25).
    Kept 90 days, at most `activity.MAX_ROWS_PER_SPACE` a space, pruned as
    they are written; a space's rows go with its erase and are in its export
    (the `space` column).
  - Gates: `tests/test_actions_name_the_person.py` (every mutating route of
    the built app, enumerated, called through the real tenant middleware and
    the recorder, names the person; a recorder writing the session label as
    the control), `tests/e2e/test_activity_page_browser.py` (by the sidebar,
    EN/AR).
- **The People page shows each person whole (S2-10, 2026-11-22).** Only
  where Spaces is enforced (People is the admin's, S1-25). Beside each
  person's features, tools, daily limit and models (S2-11, S2-01, S2-02,
  S2-03): the spaces they are a member of with their role
  (`people._spaces_of`), how many sessions they have open and when they last
  signed in (`people._sign_ins`: the newer of the sign-in recorded at the
  login route, `people.note_sign_in`, runtime key `people.LAST_SIGN_IN_KEY`,
  and their newest session's start, `web_sessions.session_starts`; never a
  session id). The admin ends a person's sessions (`people.end_sessions`,
  `POST /api/people/{account_id}/sessions/end`, event `sessions_ended`, told
  at their next sign-in); the owner's own are ended in My account
  (`owner_in_my_account`). A sign-in that cannot be recorded is never
  refused for it. Gates: `tests/test_people_sign_ins_and_sessions.py` (the
  real login route), `tests/e2e/test_people_page_each_person_browser.py` (by
  the sidebar, EN/AR: a person signed in from a second browser, every part
  of their row, their sessions ended and the second browser signed out).
- **A member's own model keys (S2-04, 2026-11-23; plan O2, 4.9).** Only
  where Spaces is enforced, and never the admin's (the install's keys are
  theirs, under Providers); an install that does not enforce it reads
  nothing more.
  - **Kept in their personal space, sealed with its key**
    (`kazma_core/spaces/own_keys.py`): the vault name
    `space:<personal space>:model_key.<provider>` (`own_keys._secret_name`),
    a space's secret, so S1-26 seals it and S1-27's count, export and erase
    of that space reach it. Read through `SecretVault.for_space`, only for
    the person bound now (`own_keys.key_for_call`): their work in a shared
    space uses it too, nobody else's. Only a provider signed in with a plain
    key (`own_keys._takes_own_key`: a Bearer, `x-api-key` or `api-key`
    header; not a server on this network, AWS or Google's sign-in).
  - **Used at the call**: every `chat` and `chat_stream` of every provider
    class asks `LLMProvider._person_call` first -- S2-03's model check
    (`_refuse_a_model_not_allowed`), then the person's own key -- and hands
    the whole call to the client it returns
    (`LLMProvider._own_key_client`: the row's model and prices, their key,
    no switch to the install's other keys, none of the row's secret headers,
    never through the install's egress proxy: pinned to the provider's own
    address, taken as a string before the twin was built, since a
    constructor routes its config through the proxy in place -- compared
    with that same object, her key went to the install's LiteLLM proxy, the
    review of S2-04). Their key refused, the call
    fails as theirs (`_switch_key_after`), never the install's key in its
    place; a store that cannot say whether they have one refuses the call
    (`own_key_unreadable`). A client built with a typed key (the providers'
    check) is marked `_key_is_given` and never swapped. A new provider class
    asks `_person_call` and hands the call on, or the gate fails.
  - **Their spend on it is theirs, read from the answer**: the delegation
    marks the answer it was given (`LLMResponse.own_key`, `own_key_answer`,
    `own_key_delta`), every ledger writer of an answered call passes
    `own_key=llm_ledger.answered_with_own_key(response)` and names the
    client that answered (`provider_of`: its `provider_name`; the
    supervisor's routed or failover client, never the build-time one), and
    neither `llm_ledger.usage_of` (their daily limit), `usage_since` (the
    install's) nor `spend_by_person` counts it. The ledger never works it
    out from a provider's name: it named the build-time client's provider
    and asked the vault whether she had a key for it, so a call on the
    install's key was recorded as hers and the reverse (the review of
    S2-04). Gate: every ledger write passing `usage=` passes `own_key=`.
  - **The routes** (`kazma_ui/own_keys_api.py`, `/api/account/model-keys`):
    GET lists the providers they may bring a key for, each with `own`, never
    a key; PUT keeps a key only once one short message through it answered
    (`provider_check.check_model(own_key_of=(space, account))`: to the
    provider's own address with the typed key alone, never through the
    egress proxy -- behind it the proxy's key answered, a key that does not
    work was kept and the install paid -- and ledgered as theirs, `own_key`
    only when their key answered; rate limit bucket `own_keys`, 6 a
    minute); DELETE forgets it. Settings -> My account -> "Use my own key"
    (`#own-model-keys`, a member's page only), EN/AR.
  - Gates: `tests/test_member_own_keys.py` (her key in the header and never
    the install's, on the OpenAI-shaped and Anthropic classes, chat and
    stream, from a shared space too; refused as hers with nothing else
    tried; nobody else's calls changed; her own-key spend outside her limit;
    the admin's work cannot read it and no GET route answers it to the
    admin; the install-scoped row as the control; the review's cases --
    a real supervisor turn on the install's key and on hers, a provider
    moved to this network, `resilient_chat`'s rows, her key and her check
    behind the egress proxy, the route's limit -- fail on beb126dec),
    `tests/test_person_models.py` (the S2-03 gate requires `_person_call`; a
    class asking the refusal alone is the control),
    `tests/e2e/test_member_own_keys_browser.py` (by the sidebar, EN/AR,
    through the real check behind the egress proxy: a refused key keeps
    nothing, a good one is kept sealed and never shown, "Use the install's
    key" forgets it; the admin's page, and the check sent through the
    proxy keeping a refused key, as the controls).
    Past their daily limit a turn on their own key still runs, on their own
    keys only (S2-02's entry above).
- **A member's page asks nothing the server refuses (2026-11-23).** The
  page shell read the install's settings on every page -- the appearance
  (and a `PUT` on a theme switch), `/api/provider/active`, `/api/providers`
  and `/api/settings/voice` -- the admin's, so a member's page was refused
  four times a load. The tenant middleware records who views the page from
  the principal it already read (`auth._PAGE_VIEWER`), and
  `auth.install_settings_here` asks the role rule for the install's settings
  (no I/O); the page carries it (`<meta name="kazma-install-settings">`,
  `window.kazmaInstallSettings` in `auth-guard.js`), and every shell read of
  them asks first (`components.js`, `stores.js`, `voice.js`). A member's
  theme stays in their browser; the sidebar's default-model picker (it sets
  the install's default) is drawn only where the page may. This space's
  Settings tabs of a feature the person lacks (email and calendar, X Studio)
  are neither drawn nor opened (`#settings-root`'s `data-tabs-off`,
  `_tabShown`): the X panel's `x-init` asked X's routes whatever the tab.
  A shell read of an install route goes behind `kazmaInstallSettings`. Gate:
  `tests/e2e/test_member_settings_asks_nothing_refused.py` (a member with a
  new person's features, by the sidebar, EN/AR, every tab shown and a theme
  switch: no request refused; today's shell and the tabs drawn whatever the
  features as controls).
- **The cloud profile (S4-08, 2026-11-23).** An install the operator
  declares public (`KAZMA_CLOUD_PROFILE`, one of `boot_guard.SECURITY_SWITCHES`,
  read through `env_flag`) runs the checks of
  `kazma_core/security/cloud_profile.py` at boot; with it off nothing there
  runs or is read. `CHECKS` is the one table, each check declaring what a
  missing piece does: **refuse** (Kazma does not start, and prints what is
  missing and the fix) for an `https` `KAZMA_PUBLIC_URL` on a public host
  (`ssrf.public_host_problem`, ssrf's own rules with no DNS: never a private
  address, `.local`, `.internal` or a name with no dot), a proxy named in
  `KAZMA_TRUSTED_PROXIES` (`*` is no proxy; a range of prefix length 0, or
  wider than the widest private block, /8 or /7, is refused: every peer in
  it could name its own client address), nobody in without signing in (no
  open switch: `boot_guard.OPEN_SWITCHES`, the ONE list -- sign-in off,
  demo, the socket bypass, loopback and LAN auto-login, the chat apps open
  to everyone, an open season takeover -- which S4-03 reads too; no hand-set
  `KAZMA_SECRET` under `WEAK_SECRET_CHARS`) and code only in containers
  (`security.execution.container_required`, which no Settings write turns
  off while the profile is on: `cloud_profile.container_requirement_refusal`,
  asked by `settings_validation`); **warn** (said at WARNING) for
  `KAZMA_PRODUCTION`, a loopback bind, a container engine installed and a
  backup copy off the machine. A check that cannot be made is a missing
  piece. Two stages: the environment's checks in `create_app` before the app
  is built (before it waits for its database), the settings' in the builder
  once the store answers (`boot_refusal(settings=...)`). A new piece is a
  row of `CHECKS` and a way to break it in the test's `BREAKS`. `kazma
  doctor` shows every check: on, a missing refuse piece fails it; off, each
  is shown (`[info]`) and counts for nothing. Gate:
  `tests/test_cloud_profile.py` (each piece missing on its own, through the
  real `create_app` for both stages; the profile off and every piece in
  place as controls; a check with no way to break it fails the gate; the
  shipped proxy, sign-in and TLS rules as controls for the review's cases,
  and the Settings save refusing the container switch).
