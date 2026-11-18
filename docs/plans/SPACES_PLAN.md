# Spaces: several people on one Kazma

Status: DRAFT of 2026-11-15, revised with the owner on 2026-11-17. All 36
decisions of section 9 are answered (N30 to N36 on 2026-11-18, from the R3
study). The foundation of stage 1 is built (the state of the items, in
section 5); every other item waits for its own step.

Baseline: commit `0cb307cde` (2026-11-17). The plan was first read from
`07ec06b38`; sections 3 and 4 were corrected for the 24 product commits
between the two. Every statement about today's code was read from the
code, not from memory.

## 1. What this plan is for

Today Kazma is for one person: the owner. `docs/THREAT_MODEL.md` says so in
its first section, and five parts of Kazma switch themselves off when a
second login exists (decision D1 of the October audits).

The owner wants friends and family on one Kazma, at home or on a cloud
server, without one person's data reaching another.

**A space** is a private world: chats, memory, documents, knowledge,
scheduled tasks, connected accounts (email, X, calendar) and files.

The plan has two parts (the owner, 2026-11-17):

- **Part 1, Spaces.** Each person has their own private space. Nobody sees
  anyone else's. A family can also have a shared family space.
- **Part 2, the shared workspace.** The owner invites friends into one
  working space: Kazma's development, schedules, the Kazma X account and so
  on. Its chats, memory, documents and files are shared by everyone in it,
  so the work is remembered as one. Each friend keeps their own private
  space beside it for what they do not share. Section 4.11.

Both parts are built from the same pieces: a person can be a member of
several spaces, and privacy is decided by which space a thing is in.

- **Per person**: which tools, a daily spending limit, which model. An
  approval request goes to the person who asked. Each person's Telegram,
  Discord, Slack or WhatsApp account is linked to their own Kazma account.

### The owner's decisions (binding)

| # | Decision |
|---|---|
| O1 | The admin cannot open members' spaces. The admin can create, reset and delete accounts. |
| O2 | Members use the owner's model keys, each with a daily limit. Using their own keys is optional. |
| O3 | Members never get tools that touch the host (the shell, the owner's files, the owner's email). Their code and files stay inside their own sandbox. |
| O4 | A shared family space: yes. |
| O5 | Build the foundations for per-space encryption now: one key per space, and every store able to keep a space's data apart. Encrypting a space even against the machine's owner can come later, possibly as a major version. |

### The stages (the owner's order)

1. Spaces across every part of Kazma.
2. Per-person tools, limits and approvals.
3. Linking chat-app accounts.
4. Hardening for the cloud, together with the independent security review.

## 2. Words used in this plan

| Word | Meaning |
|---|---|
| Account | One person's login. It has a name, a way to sign in, and its own limits. |
| Space | A private world of data. It has an id, a name, members and one key. |
| Member | An account that belongs to a space. |
| Admin | The account that runs the install: accounts, updates, backups, providers. Being admin gives no entry to a space. |
| Host space | The one space that may be given tools that touch the machine. It is the owner's space. |
| Sandboxed space | Every other space. Host tools do not exist there. |
| Personal space | The space made with an account. Only that person is in it. |
| Workspace | The shared working space of Part 2 (4.11). The admin owns it and invites people into it. |
| Asker | The account whose message started a piece of work. |
| Install | One running Kazma: the program, its settings and its machine. |

In the code, a space id is what is called `tenant_id` today. The plan keeps
that column name (it is already in more than twenty stores, in Postgres and
in backups) and uses "space" in pages, messages and docs.

## 3. Where Kazma is today

### 3.1 Who a request is

| Part | Today | File |
|---|---|---|
| Logins | Local users are a list in one settings key (`platform.users`): name, password hash, role, optional tenant. OIDC sign-in exists. | `security/platform_rbac.py`, `security/oidc.py` |
| Roles | `viewer`, `operator`, `admin`, checked per request against one path table, in every mode, on the path the router serves. Writes that change the whole install (GitHub credentials, the active workspace, the chat apps, model profiles, X credentials) are admin only. A role says what a person may do, not whose data they see. | `platform_rbac.role_allows`, `kazma_ui/request_path.py` |
| Sessions | A session carries name, role, user id and tenant. | `security/web_sessions.py` |
| The owner's own entry | The shared secret and local auto-login are an admin with no account behind them. An API token carries the role chosen when it was made (operator unless said), also with no account. | `kazma_ui/auth.py` |
| Other agents (A2A) | Off by default. With it on, an agent holding an API token runs one read-only turn with no memory; its tasks are kept in the process, for the install. | `kazma_ui/a2a_api.py` |
| Tenant of a web request | From the session's tenant, else `default`. A local user with no tenant runs as `default`, the same as the owner. | `auth.create_tenant_middleware` |
| Tenant of a chat-app message | None is bound. Memory picks a tenant from a setting (`shared`, `per_platform`, `per_user`). | `agent_handler/store.py`, `memory/config.py` |
| No tenant bound | Means `default`, which is the owner's data (`require_tenant_id`). | `tenant_isolation.py` |
| Who owns a chat | "A chat of my tenant is bound to this thread." People in one tenant own each other's chats. | `kazma_ui/thread_ownership.py` |
| Chat-app senders | An allowlist per platform. A listed sender acts as the owner. A sender is not an account. | `kazma_gateway/sender_rule.py` |

So there are two half-built ideas: **roles** (what you may do) and
**tenants** (which rows you see). Neither is a space. Nothing ties a person
to a tenant by rule, and code with no tenant falls back to the owner's data.

### 3.2 Store by store

The list is `store_registry.STORES`, its families and the folders of
`DATA_DIR_ENTRIES`. Retired stores are left out.

"Apart" means rows carry a tenant and reads filter by it. "Carries it" means
the tenant is on the row but not every read asks for it. "Whole" means one
copy for the install. "Refuses" means the part is off when a second login
exists (D1). None of this is a defect today: Kazma is one operator per
install, and the threat model says the tenant columns are not a boundary yet.

| Store | Holds | Today |
|---|---|---|
| `chat_sessions.db` (+ Postgres), spool | Chats | Apart by tenant. No person on a chat or a message. |
| `sessions.db` | Chat-app routing | Carries it (often empty). Keyed by the sender's thread. |
| `checkpoints.db`, `checkpoints_<tenant>.db` | Conversation state | A file per tenant on SQLite; one shared table on Postgres. Keyed by thread only. |
| `snapshots.db`, `time_travel_audit.db` | Time Travel steps, its audit | Whole. Routes check thread ownership. |
| `agent_artifacts.db` | Drafts, scratchpad | Apart: key `(tenant, thread, key)`. |
| `task_ledgers.db` | A chat's plan | Carries it. Read by thread. |
| `tool_effects.db` | Tool receipts | Whole. Keyed by thread. |
| `hitl_gates.db` | Approval decisions | Carries it (often empty). Records who decided, not who asked. |
| `run_incidents.db`, the run list | Stuck or failed work | Whole. **Refuses.** |
| `llm_calls.db` | Model calls and cost | Whole. No person, no tenant. The daily limit is for the install. |
| `answer_quality.db` | Quality flags | Whole. Admin page only. |
| `memory_state.db`, `memory_ops.db`, forget journal, Postgres mirror, Neo4j copy | Memory | Apart, and the strongest today: every route and tool is tested against another tenant's ids; entities are keyed `(tenant, id)`. The `default` tenant is "the install's view". |
| `settings.db` (lessons) | The Soul: lessons put in every prompt | Whole, one for the install, until Spaces is enforced; then one per space (S1-10). |
| `memory_state.db` (About me) | The user's own text (`memory_profile`) | Apart by tenant. |
| `knowledge.db`, `vector_memory/`, `knowledge_provenance/` | Knowledge Library | Carries it, on the library only: chunks, the keyword index and the pages reach it through their library. **Refuses** until Spaces is enforced; then each space's libraries are its own (S1-13). One 1 GiB quota for the install, and a share per space. |
| `documents.db` (+ Postgres), `document-store/` | Documents | Apart: tenant plus an access list per document. Content files are stored once per content, whoever uploaded them. Chat and the agent **refuse**; the page acts as each user. |
| `cron.db` | Scheduled jobs | Carries it, and who booked the job (`creator`, the account; S1-14). Every read and change keeps to the caller's space once Spaces is enforced, and a fire runs as the job's space and creator. Reminders go to the address captured at booking: before S3-05 every address is the owner's, so only the owner books (N41). |
| `swarm_tasks.db` (+ Postgres), `pipeline_logs.db`, worker registry | Swarm | The tenant is inside a task's metadata, not a column; the list filters by it in production mode. **Refuses.** |
| `research_sessions.db`, report files | Research | Whole. Reports go into the one workspace. |
| The six X stores | X posts, schedule, replies | Apart by tenant, with an X account and its keys per tenant. Two tables are not: the closed-threads table has no tenant, and the mentions cursor keeps it inside its key. The mentions loop runs for `default` only. |
| `memory_ops.db` (task queue, commitments) | Memory's queued work; the commitment layer's records | Carries it: a queued task's tenant is inside its payload. |
| `sandbox_emails.db` | The practice mailbox | Whole. No tenant. |
| `swarm_registry.json` | Registered swarm workers, each with its learned prompt | Whole. One for the install. |
| X sign-ins (settings and vault) | The OAuth 1.0a keys, the app's Bearer token, the OAuth 2.0 client and the Sign in with X tokens | Apart: each key is the tenant's (`tenant.<id>.` before the key for any tenant but `default`). A sign-in in progress is kept for the install, bound to the browser that started it. |
| Mail and calendar sign-ins | Connected accounts | Whole: stored for the install on purpose (`INSTALL_SCOPED_SECRETS`). |
| `vault.db` | Secrets | Rows carry a tenant or are global. One key for the whole vault. A URL's password under any setting is kept for the install. |
| `kazma.yaml`, `kazma.local.yaml`, the permissions file | Kazma's configuration | Whole. Readable; no tool can write them. |
| `problem_reports/` | Problem reports that could not be sent | Whole. Admin only. |
| `settings.db` | Settings, workspaces | Whole. One active workspace for the install. |
| `rbac.db`, `audit.db`, `disclosure.db`, `certifications.db` | Control records | Whole. |
| `file_checkpoints.db`, `code-index/` | IDE undo, code search | Whole, tied to the host's files. |
| `callbacks.db`, `chat_outbox.db`, `chat_inbox.db` | Chat-app buttons, replies waiting | Whole. Keyed by the chat address. |
| `attachments/`, `images/`, `exports/`, `documents/`, `workspace/` | Files | Flat folders for the install. A file shared into a web chat is checked against its chat. |
| Backups, `kazma migrate` | Everything | Whole install only. Memory has an export per tenant. |

Counted: memory, documents, drafts and chats already keep tenants apart.
About ten stores carry the id without using it everywhere. About twelve are
whole. Five parts refuse.

### 3.3 Surface by surface

| Surface | Today |
|---|---|
| Web pages and `/api` | Signed-in only. Role table per path. Tenant from the session. |
| Chat (web) | By tenant. Stream attach checks the thread. |
| Chat apps | One bot per platform for the install. Allowlisted senders act as the owner. No tenant bound. |
| Memory page and tools | By tenant, tested route by route. |
| Documents | Page by user; chat and agent refuse with a second login. |
| Knowledge, Scheduled, Swarm, Running work | Refuse with a second login. |
| Approvals | A card is shown to whoever owns the thread; on chat apps, in the chat it came from. A web turn's reminders and cards may go to the owner's Telegram. |
| Settings | Admin only, one set for the install. |
| X Studio and the X tools | By tenant. Reading the whole X API is a read tool; acting on X and posting always ask. Connecting and signing out are admin only. |
| Other agents (A2A), problem reports | For the install. A2A runs read-only turns for a token holder; a problem report is the admin's. |
| Workspace, IDE, shell, MCP servers | The host's files and programs. One for the install. |
| Alerts, daily digest, weekly report | To the owner's channels. They name chats and work of the whole install. |
| CLI, terminal app | Act as the owner, on the machine. |
| Tools the model is offered | One list for the install; switches are for the install. |
| Model keys, spend limit | The install's. |

### 3.4 What this means

Three things are missing, and they are the heart of the plan:

1. **A person.** Kazma knows a tenant and a role, not "who is this". Chat
   messages, approvals, jobs and model calls have no person on them.
2. **A rule with no default.** Code that has no tenant gets the owner's data.
   With spaces that is the one mistake that must be impossible.
3. **A whole answer per store.** Each store answers "whose row is this" in
   its own way, or not at all.

## 4. The design

### 4.1 Six rules

1. **The space is the privacy unit.** Inside a space, members share
   everything. Between spaces, nothing is shared. (See decision N2.)
2. **No space, no data.** Code with no space bound is refused. It never
   falls back to the owner's space. Work that belongs to the install (an
   update, a backup, a health check) is declared as such and reads no space
   content.
3. **Every piece of work has an asker.** A turn, a job, a swarm task, an
   approval and a model call each name the account that started them.
4. **One answer, one place.** One function says which space a request is in.
   One table says how each store keeps spaces apart. One table says which
   routes are the install's, a space's or a person's.
5. **The admin runs the install, not the spaces.** No page, route, tool or
   command lets the admin read a space they are not a member of.
6. **Host tools exist only in a host space.** In a sandboxed space they are
   absent in code, not switched off by a setting. Only the admin's personal
   space and the workspace can be host spaces (4.11).

### 4.2 What a space is in the data

**An id on every row, and a folder for every file.**

- Each row of a space's data carries the space id (the `tenant_id` column).
  Stores that lack the column get it. Every read and write names it.
- Each space has one folder, `<data dir>/spaces/<space id>/`, for things that
  are files: its sandbox files, attachments, images, exports, generated
  documents and fetched Knowledge pages.
- The owner's space keeps the id `default`. Today's rows are already
  `default`, so the owner's data does not move (section 7).

Why rows and not a database per space: more than twenty stores and every
Postgres table already carry the id; the background engines (scheduler,
swarm, memory worker, document jobs, retention) each run once for the
install; and Postgres needs the row model anyway. A database per space was
weighed and is decision N6.

What makes rows safe enough to rely on:

- **Store handles are bound to a space.** A store is asked as
  `store.for_space(id)`. There is no call that works without one.
- **A class gate reads every statement** on a space's table and fails one
  that does not name the space (the pattern memory's entity gate uses today).
- **A matrix test** drives every route and every tool as one space with
  another space's ids. Memory has this today; it becomes the rule for all.
- **On Postgres, row-level security** is a second lock in stage 4: a query
  that forgets the filter returns nothing.

**The space rule of each store** is declared once, in `store_registry`,
beside what the store holds and how it migrates:

| Rule | Meaning | Examples |
|---|---|---|
| `row` | A space id on every row | chats, memory, jobs, swarm tasks, approvals, model calls |
| `folder` | Files under the space's folder | attachments, sandbox files, exports |
| `install` | Belongs to the install; holds no space content. Declared with why. | provider keys, updates, the guard's state |
| `derived` | Rebuilt from space data, split the same way | vectors, search indexes |
| `host` | Tied to the machine's files; host space only | IDE undo, code index |

Each `row` store also names its **content columns** (a person's words and
files) apart from its **index columns** (ids, times, states). Background
sweeps read index columns only. This is what the encryption work needs later
(4.9).

### 4.3 The identity store

One new store, on the settings backend (SQLite or Postgres), replaces the
`platform.users` list:

| Table | Holds |
|---|---|
| accounts | id, name, sign-in, enabled, install role (`admin` or `member`), language |
| spaces | id, name, kind (`host` or `sandboxed`), created by |
| memberships | account, space, role in the space (`owner` or `member`) |
| links | platform, platform user id, account (stage 3) |
| space_keys | space, key id, the wrapped key, how it is wrapped |
| policies | per account: tools, daily limit, models (stage 2) |

Roles are two short lists. In the install: `admin`, `member`. In a space:
`owner` (may add and remove members), `member`. Today's `viewer` and
`operator` map to `member`; a read-only member is decision N12.

### 4.4 How a request finds its space

One function binds two values for the life of a request or a job: the
**space** and the **asker**.

**Web.** A page is rendered for one space and names that space on every
call it makes. The server checks the caller is a member, every time. A link
that cannot carry the name (a download, an image) is resolved from the thing
itself: the file or chat names its space, and the caller must be a member.
A space switcher in the header changes space by loading the page again.
The space is not kept in the session, so two tabs in two spaces cannot mix.

**Chat apps (stage 3).** The sender is looked up in `links`. No link: the
private notice, with how to link. A linked sender's direct chat runs in
their own space, or the one they chose with `/space`. A group chat runs in
the one space a member bound it to.

**Background work.** A scheduled job, a swarm task, a memory task, a
document job and a resumed turn each carry their space and asker on their
own row, and bind them before they run. A job with no space does not run.

**Threads.** Every chat thread is recorded once with its space and the
account that opened it. Every place that runs a turn (web, chat apps,
scheduler, a resume after a restart, an approval's timeout) binds the scope
from that record. A turn that paused in one space can only be resumed there.

**CLI and terminal app.** They run on the machine, as the admin, in the
admin's own spaces. A command that names another space is refused.

### 4.5 Sign-in and accounts

- Every person signs in as an account. Once a second account exists, the
  shared secret, local auto-login and API tokens are tied to the owner's
  account or switched off (item S1-04).
- The admin creates an account, resets its password and deletes it (O1).
  A reset is never silent: the member is told at their next sign-in and in
  their linked chat app, and it is in a record the admin cannot edit.
- Creating an account creates its personal space and that space's key.
- A member can be in several spaces: their own, and shared ones.
- A personal space has one member forever. Nobody can be added to it, the
  admin included.
- Adding anyone to a shared space is never silent: every member of it is
  told who was added and by whom, and it is in a record the admin cannot
  edit.

### 4.6 What follows the space, part by part

| Part | With spaces |
|---|---|
| Chats | A chat belongs to one space. Each message names who wrote it. Members of the space see the space's chats. |
| Checkpoints, Time Travel, drafts, plans, receipts | Follow their chat's space. Postgres checkpoints get the space on the thread record. |
| Memory | The space is the memory tenant, always. The `shared / per_platform / per_user` setting is retired. `default` becomes a space like any other: no "install's view". Facts name the member who said them (4.7). |
| Lessons (the Soul) | One per space. A lesson learned in one space never enters another's prompt. |
| Documents | The document's tenant is the space; its owner is the account. The "one operator" stand-in goes. Chat and the agent act as the asker. Content files are kept per space, so one space can be exported or erased alone. |
| Knowledge | Libraries, their vectors and fetched pages belong to a space. The quota is per space. |
| Scheduled | A job carries its space and its creator. It fires in that space, with the creator's tools as they are at that moment. Its result goes to the creator. |
| Swarm | A task carries its space and asker. Workers run inside that scope. Templates stay the install's. |
| Research | Sessions and reports belong to a space; reports are written in its folder. |
| Connected accounts | Mail, calendar, X and GitHub sign-ins belong to the space that connected them. X already works this way, with all of its sign-ins: the OAuth 1.0a keys, the app's Bearer token and Sign in with X. Mail, calendar and GitHub move from the install to the space. A sign-in in progress names the space it was started in. The owner's mailbox is the owner's space's. Background refresh and the X mentions loop go space by space. |
| Secrets | The vault gains a space scope. A space's secrets are sealed with the space's key (4.9). The install's secrets stay the install's. |
| Files | File tools, uploads and generated files live in the space's folder. `python_exec` mounts that folder and nothing else. |
| Workspace, IDE, shell, git, MCP servers | Host tools. Host spaces only (4.8). Each host space has its own root folder, IDE root, code index and IDE undo; a switch or clone in one never moves the other (4.11, S1-34). |
| Approvals | A gate row carries its space and its asker. Only the asker is shown the card and may answer it (stage 2). |
| Running work | Listed per space. A stuck or failed run is told to its asker. |
| Model calls | Each row names the space and the asker. Spend is counted per person. |
| Settings | Every key is classed `install`, `space` or `account`. The Settings page splits into three: My account, This space, Install (admin). |
| Notifications | Web Push and the bell are per account. A turn's "done" goes to its asker. |
| Alerts, digest, weekly report | To the admin: counts and states, never a chat's title or text from a space the admin is not in. |
| Logs | Name ids, never a member's words. |
| Backups | The whole install, as today, plus the identity store. Stated plainly: a backup holds every space (N10). |
| Export and erase | Any member can export their space. Deleting a space erases it in every store, with the same honest limit memory states today: backups already taken. |

### 4.7 Who is "I" in a shared space

Memory assumes one user per tenant: "I live in Kuwait" becomes a fact about
`user`. With three people in a family space that is wrong.

The plan: in a space with more than one member, each member has their own
"self" entry, under their name. A fact a person states about themself is
filed under them. Recall for a turn treats the speaker as "I" and the other
members as named people. A space with one member works exactly as today.

This was the least settled part of the design, so it got a study before its
item is built (risk R3): `docs/plans/SPACES_R3_MEMORY_STUDY.md`, 2026-11-18.
What the study chose, and the owner's answers N30 to N36:

- **An entry per member, filed when the fact is written**, keyed by the
  account. A personal space keeps the one `user` entry and changes in
  nothing. A shared space files by member from its first turn (N30).
- The asker's own facts are shown as today; another member's are shown
  under that member's name, and so are their messages.
- A statement about another member is kept as "said by", and never replaces
  what that member said about themself (N34).
- Memory in a shared space is shared, and the space says so where people
  type (N35).
- When a member leaves or is removed, the facts filed under their own entry
  are forgotten; their messages stay as the conversation's history (N36).

### 4.8 Tools: three groups

| Group | What | Who can have it |
|---|---|---|
| Host tools | The shell, files outside the space's folder, the IDE, git, workspace switch and clone, MCP servers, package and skill installs, settings and vault tools, computer use, system logs, updates and backups | The host space only. Absent everywhere else, in code. |
| Space tools | Memory, knowledge, documents, scheduling, swarm, research, files in the space's folder, code in the container, the space's own connected accounts | Members, by their per-person list |
| Outside tools | Web search and fetch, image generation, voice | Members, by their per-person list |

Code for members runs only in a container (or the cloud microVM). If the
machine has no container engine, members have no code tool. The local
fallback is never used for a sandboxed space.

Whether friends in the owner's space get host tools is decision N1.

**Features: what the admin grants to each person.** The owner asked for
this on 2026-11-17: "give User Y the X Studio, but not User G". A tool list
alone does not do that, because a feature is more than its tools. So Kazma
gets one table of features. Each feature names everything that belongs to
it: its pages, its routes, its tools, its chat commands, its background
work and its connected accounts. The admin turns a feature on or off per
person, and one switch covers all of those parts.

| Feature | What it covers (examples) | Group |
|---|---|---|
| X Studio | The X pages and routes (the `/api/x/oauth2/*` sign-in among them), the X tools (`x_api_read`, `x_api_act`, `x_upload_media` too), `/x`, the mentions loop, the X account and its sign-ins | Space |
| Email and calendar | The mail and calendar tools, their sign-ins and cards | Space |
| Documents | The Documents page, routes and tools | Space |
| Knowledge | Libraries, crawls, `/kb` | Space |
| Research | The Research page and deep research | Space |
| Swarm | The Swarm page and `dispatch_swarm` | Space |
| Scheduled | Reminders, tasks, automations | Space |
| Code | `python_exec` in the container | Space |
| Web | Search, fetch, crawl | Outside |
| Browser | Browser automation | Outside |
| Images and voice | Image generation, speech | Outside |
| Chat apps | Linking Telegram, Discord, Slack or WhatsApp (stage 3) | Space |
| Development | The shell, the IDE and Workspace pages, git, the GitHub tools, the project's files, and using the MCP servers the admin set up | Host: a shared space with a project folder, by grant; the admin's own personal space, on at first and the admin can turn it off (4.11, N28) |

Rules:

- A feature that is off for a person is gone for them: no menu entry, the
  page and its routes refuse, the tools are neither offered nor callable,
  the chat command refuses, and their background work of that feature
  stops. Hiding the menu entry alone is never enough.
- A grant never passes the group rule above. No grant gives a member a host
  tool (O3), except where N1 allows it for a friend in the owner's space.
- Every page, route, tool, command and background loop belongs to a feature
  or is declared "always on" with why (chat, memory, My account). A new one
  with neither fails the build, so nothing is left ungrantable by accident.
- Inside a feature the admin can still switch single tools (S2-01). For
  example: X Studio on, but posting off, so the person can draft and read.
- A feature that is off keeps the person's data. Turning it on again brings
  it back.
- The switch is per person. In a shared space each member has their own
  set (decision N18).

### 4.9 The encryption foundation (O5)

What is built now:

1. **A key per space.** Made when the space is made, kept in `space_keys`,
   wrapped by the install's vault key. Each sealed item names its key id, so
   a key can be changed later.
2. **A space's secrets are sealed with its own key.** Mail, calendar and X
   tokens and a member's own model keys. This is the one place content is
   encrypted per space in this plan, because the vault already encrypts.
3. **Every store declares how it keeps a space apart** (4.2), with its
   content columns named.
4. **Three operations work on every store, from that table:** count a
   space's data, export one space, erase one space. If every store can hand
   over exactly one space and remove exactly one space, every store can keep
   a space apart. A test proves it with a word planted through every writer.
5. **One sealing format** for a space export, using the space key.

What is not built now, and how the foundation leads to it:

- Wrapping a space's key with something only its members hold (a password
  and a recovery code), so the machine's owner cannot open it.
- Sealing the content columns, and keeping each space's search indexes in
  files that open only while the space is unlocked.
- Deciding how scheduled jobs and chat apps work while nobody is signed in.

Until that version, this plan promises a boundary **between people who use
Kazma**. It does not promise a boundary against the person who runs the
machine: they can read the disk and the backups. The pages and docs will say
so in plain words.

### 4.10 What the admin can and cannot do

| Can | Cannot |
|---|---|
| Create, disable, reset and delete accounts | Open a space they are not a member of, by any page, route, tool or command |
| Create spaces and set each person's features, tools, limit and models | Read a member's chats, memory, documents, jobs or files |
| See each person's spend and counts of work | See titles or text of that work |
| Run updates, backups and restores of the whole install | Restore or export one member's space into their own view |
| Set the install's model keys and chat-app bots | Use a member's connected accounts |

### 4.11 Part 2: the shared workspace

What the owner asked for: "one admin shared space, many users, with shared
data across every user except data the user won't share ... they also have
their own private space."

**Two kinds of shared space.** The screen for making a shared space offers
both, and the People page says which kind each space is (N29):

| Kind | What it is | Who makes it |
|---|---|---|
| A plain shared space (the default) | Sandboxed. Its own space folder, no project folder, no Development, no GitHub. For family and friends who do not code: shared chats, memory, documents, schedules, X Studio and the rest. | The admin |
| A shared space with a project folder | The host kind, for development. Only this kind ever asks about Development or GitHub; N26 and N27 apply only to it. | The admin only |

An install can have plain shared spaces and no development space at all.
In the rest of this section, "the workspace" is a shared space of either
kind. The rules about Development, the project folder, roots, MCP and
GitHub apply only to the second kind.

**How it is built.** No new kind of privacy is needed. It is two spaces per
person:

| Space | Who is in it | What is in it |
|---|---|---|
| The workspace | The admin and everyone the admin invites | The shared work: chats, memory, documents, knowledge, schedules, files, the work's connected accounts (for example the Kazma X account) |
| A personal space each | That one person | Whatever they do not share: private chats, their own memory, documents, files and accounts |

Rules:

- **One memory for the work.** Everything said and learned in the workspace
  is remembered there, for every member. Each fact still names who said it
  (4.7), so "I prefer mornings" is filed under the person who said it.
- **Private means "in my personal space".** There is no "private" mark on
  single items inside the workspace. A mark per item would have to be
  honoured by every store, search, summary and background job, and one miss
  would show a private thing to everyone. A whole space is one boundary that
  the gates of Part 1 already hold.
- **Choosing is one click.** The header shows which space the page is in.
  "New private chat" opens a chat in the personal space. A person can move
  their own chat, document or file from their personal space into the
  workspace ("Share with the workspace"). Moving the other way takes a
  thing out of the workspace, so it is allowed only for the person who made
  it, and the workspace keeps a line saying it was taken out.
- **A private chat does not read the workspace's memory, and the workspace
  never reads a personal space.** (Decided, N20; it can be added
  later as a per-person option.)
- **What each person may do is the admin's grant** (4.8, S2-11), per
  person: one friend gets X Studio, another gets Scheduled, another gets
  Development.
- **Host features in the workspace.** Helping with development needs the
  shell, the IDE, git and the workspace's files. These are the feature
  "Development", off for everyone until the admin turns it on for a person.
  O3 still holds for every member: no member's personal space and no
  family space ever has host tools.
- **The admin's personal space is the one personal space with host tools.**
  It is today's install with today's data (N19), and the admin already holds
  full trust. So the owner keeps the shell, the IDE and git in private
  chats. The workspace is a new, empty space.
- **Development is a switch for the admin too** (N28). It starts
  on, as today. An admin who never codes can turn it off for themself: the
  shell, the IDE, git, the GitHub tools and the machine's project files are
  then absent from the admin's personal space too. The space stays a host
  space; only the Development tools follow the switch.
- **Admin tools are not Development.** Settings and vault tools, package
  and skill installs, updates and backups, system logs, and adding,
  changing or removing MCP servers stay with the admin whatever the
  switch says.
- **The workspace has its own project folder,** chosen by the admin when it
  is made. It is never Kazma's install folder, so a friend's file write
  cannot change Kazma's own code.
- **The admin approves what a Development holder's always-ask actions and
  folder grants ask for** (N21). It uses the "approver" setting of
  N4, set to the admin for anyone holding Development.
- **What Development does not include.** Settings and vault tools, package
  and skill installs, updates and backups, system logs, computer use, and
  adding, changing or removing MCP servers. An MCP server is a command
  Kazma runs as itself at every start, for every space. All of these stay
  in the admin's personal space only, never in the workspace.
- **Each host space has its own root.** Today one active folder serves the
  whole install: the workspace switch and clone, the IDE, the code index
  and IDE undo all follow it. With two host spaces each gets its own
  binding, carried per task, never through the one install-wide row. A
  friend switching or cloning in the workspace never moves the admin's
  folder (S1-34).
- **The filesystem MCP server serves one host space.** It is bound to one
  folder for the whole process. Until each space can have its own instance,
  it serves the admin's personal space and refuses in the workspace, where
  Kazma's own file tools do the work (N27).
- **GitHub is a connected account of a space.** Today the git and GitHub
  tools use the install's one GitHub sign-in, which is the owner's. In the
  workspace they use the workspace's own GitHub connection, or none. A
  friend's push never acts as the owner (S1-17, N26).
- **Said plainly on the grant:** a person with Development can run commands
  on the machine. Until the encrypted version (4.9), that is the same trust
  as the admin. The People page says so when the switch is turned on.
- **Everything is under a name.** Each action in the workspace is recorded
  under the person who did it (S2-09). An approval card goes to the person
  who asked (S2-05).
- **Chat apps (stage 3).** A linked person's direct chat with the bot runs
  in the space they chose with `/space` (their personal one at first). A
  group chat can be bound to the workspace, so the team works with Kazma in
  one group.
- **Leaving.** When the admin removes a person from the workspace, what
  they put there stays (it is the work's). Their personal space is theirs
  until their account is deleted.

Where today's data goes is decision N19: nothing the owner has today
becomes shared without the owner choosing it.

## 5. Stages

Each item names what changes, the gate that proves it, and what the owner
sees. Every gate has a negative control: it is shown failing on the old code
or on a planted mistake, as `AGENTS.md` asks. Test names are proposals.
All items are **Planned**, except where the table below says otherwise.

### State of the items

Updated as work lands (section 11). "Done" means the item's gate passes with
its negative control; the evidence names the test files.

| ID | State | Evidence and notes |
|---|---|---|
| S1-01 | Done, 2026-11-17 | `kazma_core/spaces/identity.py`: accounts, spaces and memberships in one document on the settings backend, changed only by `update_registry` (one atomic step). The older mode's users are accounts; `platform_rbac` reads and writes them through it, and its three read-change-save sites are atomic now. The old list moves in once, at start. `tests/test_identity_store.py` (15 tests; the old whole-list save loses a user as the control). Not done here: a People page, and a real Postgres run of this test (the store uses the settings store's own `atomic_update`, which has its Postgres tests). |
| S1-02 | In progress | The mechanism is done: `kazma_core/spaces/scope.py` (a scope is space and asker, bound together; `install_work` has no space; with Spaces enforced, unbound work raises `NoSpaceBound`). `require_tenant_id` asks it and no longer swallows errors. Enforcement is one stored switch kept in memory; nothing in the product can turn it on yet, so an install behaves as before. `tests/test_no_space_no_data.py` (25 tests; the old fallback as the control). Left: the tenant fallbacks still written out in product code. A ratchet in the same test counts them (226 after S1-12; 229 after S1-10; 230 after S1-09; 245 after S1-08; 299 at the start) and may only go down; each store's item (S1-08 onward) removes its own and adds its `for_space` handle. |
| S1-03 | Done, 2026-11-17 | `store_registry.SPACE_RULES`: every store, family and data-folder entry (94) declares `row`, `folder`, `install`, `derived` or `host`, what it does today, and its content columns. `tests/test_store_space_rules.py` (16 tests; declared columns are checked against each store's own `CREATE TABLE`; planted entries as controls). |
| S1-04 | Done for the foundation, 2026-11-17 | `kazma_ui/space_binding.account_of`: the shared secret, an API token, the loopback auto-login and a secret sign-in's session are the owner's account; a local user's session is that user's; a sign-in with no account (OIDC, today) is none, and is refused once Spaces is enforced. The tenant middleware binds the asker. `tests/test_sign_in_is_an_account.py` (27 tests). Left for S1-33: retiring the shared secret for the web (N15), and accounts for OIDC sign-ins (N24). |
| S1-05 | Done, 2026-11-17 | `kazma_ui/route_scopes.py`: all 632 routes of the built app classed (space 329, install 253, account 21, open 29). `tests/test_route_scopes.py` (21 tests; a planted route, a stale rule and a wrong `open` as controls). |
| S1-06 | Done for the foundation, 2026-11-17 | The page names its space on every call (`static/js/space_scope.js`: the `X-Kazma-Space` header on fetch, a `space` value on event streams, sockets and links; the space is the tab's, kept in `sessionStorage`). The tenant middleware checks membership every time, before the handler. `GET /api/account/spaces` and a header switcher, shown only when Spaces is enforced and the person has more than one space. The page does all of this only on an install that enforces Spaces; elsewhere it names nothing, and a request that names the tenant it would run in anyway runs as before (the older multi-user mode's users and OIDC sign-ins are members of no space; the first version refused their pages' own calls, found in review). `tests/test_web_request_space.py` (44 tests: every `space` rule of the route table driven as a member of another space; the page's own flow for a local user, a local user bound to a tenant and an OIDC session, with the first rule as its control; a session-kept space mixes two tabs as the control), `tests/js/test_space_scope.js` (48 checks). The browser test with two real tabs, in English and Arabic, is written with S1-08 (`tests/e2e/test_two_tabs_two_spaces.py`; the owner in two spaces) and WebSocket handshakes are bound and checked there. Left: with S1-33, a tab that remembers a space its new sign-in is not in (sign out, sign in as another account in the same tab) must open in that account's own space instead of a refusal. Fixes, 2026-11-18, from the browser test's first run: a page (a GET outside `/api/` and the sockets) that names no space is drawn for the person's own first space and the script loads it for the tab's (a reload after the sidebar dropped `?space=` was a 400); with no sign-in at all, a space route naming another space is refused instead of answered from the owner's space. Both in `tests/test_web_request_space.py`, through the middleware and through `create_app()`'s `/chat`, with the old rules as controls. |
| S1-07 | Done, 2026-11-17 | `kazma_core/spaces/threads.py` (`thread_scopes.db`): a thread is recorded once with its space and opener. `turn_segment_budget`, which every graph run sits in, binds the turn's scope from the record. Enforced, a turn asked for in another space is refused. `tests/test_turn_scope_bound.py` (16 tests; a gate finds every graph run outside the block; a resume with no record ran as `default` as the control). A thread's record is removed when its chat is deleted (S1-08). |
| S1-29 | Done (empty harness), 2026-11-17 | `tests/_space_matrix.py`, `tests/test_space_matrix.py` (20 tests): every `space` route (329) and every agent tool (175) has a case, or names the item that will add it, or says why it holds nothing of a space. 500 were pending at the start, 459 after S1-08, 395 after S1-09, 392 after S1-10, 369 after S1-13, 320 after S1-12 (merged after them); the number may only go down. |
| S1-08 | Built, 2026-11-18; its browser test and its real-Postgres run are still to be run | Guards refuse only where Spaces is enforced, so an install with one account behaves as before. The thread's record is the authority for everything kept by thread (`spaces/threads.py`: `thread_home`, `thread_refusal`, `require_thread`; a thread from before Spaces is the owner's, N19). Chats and the spool: the store asks the scope for its space (no fallback). Checkpoints: once enforced, the checkpoint manager follows the thread: another space's thread reads as having none and a write is refused; on Postgres the one shared table is read and cleared thread by thread. Time Travel: restore and fork refuse another space's chat, and a fork's thread is recorded in its space before anything is written; a chat delete of an id the space does not hold deletes no steps. A deleted chat's thread record goes last, once nothing of the chat is left. Drafts, the scratchpad and receipts take the turn's space (`scope.space_of_state`); a plan is read and made only through a thread of its own space. Sockets (`/ws/chat/{session_id}`, `/ws/voice`) name their space and membership is checked at the handshake. A live-voice room and an A2A task are their space's. Messages name their author: a user's row carries `author`; history and the live frame add `author_name` for another member, only where Spaces is enforced, and the chat page shows it. Evidence: `tests/_chat_space_cases.py` (41 matrix cases: 34 routes and 7 tools, through the product's routers behind the real tenant middleware), `tests/test_chat_state_spaces.py` (31 tests: each guard taken out fails its case; the checkpoint store on one shared table, with "the shared table reads across" as the control; authors; an install without Spaces is unchanged), `tests/js/test_message_author.js`. The tenant fallbacks went from 299 to 245 and the matrix's pending targets from 500 to 459. Found and fixed on the way, each with its test: a chat delete that named an id its own store did not hold deleted the Time Travel steps saved under that id; a live-voice room was named by the chat id alone. Left: (1) `tests/e2e/test_two_tabs_two_spaces.py` (two tabs in two spaces, by the sidebar, EN and AR) is written and not yet run; (2) the five real-Postgres tests of the checkpoint store are written and not yet run; (3) on Postgres, checkpoints the older multi-user mode wrote into `checkpoints_<tenant>.db` files are not read once Spaces is enforced (every space then uses the shared table): they move at switch-on (section 7); (4) `snapshots.db` and `tool_effects.db` keep no space column: they are reached through a thread, and S1-27 lists a space's rows through its thread records; (5) chat-app turns, a chat's uploads and files, approvals and the memory a rewind takes back keep their own items (stage 3, S1-19, S1-21, S1-09). **Review fixes, 2026-11-18** (each with its test and a control that runs the code as it was): a fork or a restore whose thread record store fails is a refusal (`thread_record_unavailable`, 503, a WARNING), never a raw `sqlite3.Error`; a fork bound to no space once Spaces is enforced (a chat app's `/fork`) is refused (`no_space`), never `NoSpaceBound`; both are worded on the chat apps and the Time Travel page. A deleted chat's record stays when its checkpoints were not deleted (no checkpoint store wired, or one that failed, now a WARNING) and when the Dashboard's delete could not delete the chat (a WARNING; it was DEBUG). On one shared table the listing is held to the space's threads by its own statement, before the limit (filtered after reading the newest `limit * 5` threads, a space with older chats got a short or empty list); a listing and a clear ask the record store once (`threads.space_threads`), not thread by thread with no memory of a miss; every read of a record remembers its space, so the plan's guards a turn asks on the loop read no store. Once enforced a scratchpad note is filed in the bound scope's space (it took the tenant value). New controls: drafts, notes and receipts filed by the tenant value an older checkpoint left; Time Travel through the product's own `CheckpointManager` (its thread guard stops a fork and a restore the rewind guard let through); a control passes only on its case's own assertion. `tests/test_chat_state_spaces.py` now holds 47 tests. |
| S1-09 | Built, 2026-11-18; the memory benchmarks and the Postgres mirror tests are still to be run | As S1-08: guards act only where Spaces is enforced, stamps are always written, and an install with one account or the older multi-user mode behaves as before. **One rule for who sees every tenant** (`kazma_core/memory/tenant_view.py`): Kazma's own work always; the owner's tenant `default` only while Spaces is not enforced; any other tenant never. About forty comparisons with the literal `default` ask it now. Once enforced, `default` is the owner's personal space: the owner reads and changes its own rows only, and a member reads nothing of it. **The mode setting is retired**: only `shared` can be saved; Settings and the terminal app lose the control; an install that stored `per_platform` or `per_user` keeps it until it enforces Spaces or presses "Use one memory" (`config.legacy_tenant_mode`). Enforced, a turn's memory tenant is the bound space. **A turn's memory is its step's space** (`scope.space_of_state` in the supervisor, the tool worker, respond, Time Travel and the rewinds). **Ids carry the space** (the owner's decision 17: `id` stays the key, no table is rebuilt): an episode id of any tenant but the owner's is salted with it (`dual_write.episode_ids`, `swarm_bridge.bridge_episode_ids`); the writer picks the id inside its transaction, recognises a row an older build wrote under the plain id, and never writes under an id another tenant holds; the same turn or the same imported history in two spaces is two sets of memories. **Writers that named no tenant**: the tool-outcome recorder, the swarm bridge, the history import (page and command) write for the space they run in; bringing a history in is allowed per space once Spaces is enforced. **The worker**: `memory_tenants()` lists every tenant that holds memory and every space, and raises instead of answering "the owner alone"; a queued task runs bound to the space it names; the manual reconsolidate queues one task per tenant. **The store refuses a row under the "no tenant" sentinel** (a trigger on every table with a tenant column) and the operator trail names the space acted in. **Recall** holds its episode candidates to the asker's tenant before any read by id. Routes that took their tenant from elsewhere (graph clear, procedure search, the Neo4j topology) take the request's. **Memory outside every space is counted**, table by table, in memory health (`tenants_outside_spaces`: the three local files; the copies it does not read are named). Memory's copies outside the data folder declare a space rule (`memory_stores.EXTERNAL_SPACE_RULES`). Evidence: `tests/_memory_space_cases.py` (64 matrix cases: every memory route through the real tenant middleware, the history import, 11 tools), `tests/test_memory_default_is_a_space.py` (95 tests: every memory route as the owner in `default`, and as a member with the owner as the victim; without Spaces the owner is shown every tenant as the control; a class gate on comparisons with the literal), `tests/test_memory_spaces.py` (39 tests, each rule with the old behaviour as its control; every memory sweep run with Spaces enforced and nothing bound), `tests/test_memory_tenant_matrix.py`. The tenant fallbacks went from 245 to 230 and the matrix's pending targets from 459 to 395. Left: (1) the retrieval benchmarks and the Postgres mirror and primary-recall tests are not yet run on this change (the benchmark's tenant now writes spaced ids); (2) memory under a tenant that is no space is counted, never merged: merging is the migrate command's, with the owner's word (R6); (3) the Postgres mirror, a remote vector index and Neo4j keep `id` as their key across installs that share them; (4) a swarm task hands its space to memory with S1-15; (5) the lessons recall channel and the Soul are S1-10; (6) a gate over every memory statement that picks a row by id alone is not built: recall's are held by one check, the rest are reached through rows already read by tenant. **Review fixes, 2026-11-18** (each with its test and a control that runs the code as it was; the owner's existing ids do not change): the three cadences that visit every tenant (macro sleep, reconsolidation, the nightly export) list them through one helper (`worker_bootstrap._queue_per_tenant`): a failed listing is a WARNING, queues nothing, writes no stamp and is tried again 15 minutes later (reconsolidation said it at DEBUG); a recall whose tenant check of its candidates fails says it failed (`recall_failed:`), never "nothing found"; the deep pass is queued for the id the writer chose (a turn of another space may be stored under either form of its id), and for no turn that was not written; an empty tenant is the owner's on both sides of the writer's id lookup (a re-write added a copy under the other id); with Spaces enforced every queued memory task runs whole: a space and the account it acts for (the space's own person or maker, the admin for a tenant no space holds), or as the install's work (`task_queue._task_binding`; each task type says how it finds its space: `TENANT_TASKS`, `ROW_NAMED_TASKS`, `INSTALL_TASKS`, held to the registered handlers); one lock for "is a history import running anywhere" and the claim. New controls: the sweep check catches a sweep that asks for the space, raised or said; the owner's enforced pass run with the rule memory code held before S1-09 shows the owner every tenant. The id-only statements are their own item (S1-35), counted by a ratchet meanwhile (81). |
| S1-10 | Built, 2026-11-18 | One Soul for an install that does not enforce Spaces, whatever tenant a turn runs for, exactly as before. Enforced, each space has its own (`self_improvement.soul_space`): the owner's space keeps the settings key it always had; every other space's Soul is inside one document keyed by space (`self_improvement.space_evolution`, changed in one atomic step). **Learning**: `close_turn` hands the finished turn over with its space, and the lesson (and the refiner's own memory search) is that space's. **Reading**: once enforced, the supervisor is the one place a turn's lessons are chosen, on every surface: at the turn's first step it drops any copy the call holds (a transport's, read for the request) and adds the lessons of the step's space; it cuts a Soul the agent baked into its base prompt at build time, and the agent bakes none once Spaces is enforced; a turn with no space reads none. **Forgetting** a turn, a chat or a text edits the lessons of that space only; Kazma's own replay, which names no space, reads every Soul. **A lesson held for confirmation** is its space's commitment: listed, confirmed, rejected and applied there; another space's answers not found. Found and fixed on the way, with its test: the reject route aborted whatever commitment the id named (a reminder being confirmed), not only a held lesson. Evidence: `tests/test_lessons_per_space.py` (18 tests at first, 39 after the review: learning through the real hand-off, reading through the real supervisor graph with a recording model, each rule with the single Soul as its control), `tests/_lesson_space_cases.py` (3 matrix cases behind the real tenant middleware). The tenant fallbacks went from 230 to 229 and the matrix's pending targets from 395 to 392. Left: (1) who confirms a held lesson of a space (open decision N39): the routes are admin-only today (`/api/commitment`), and the admin sees only the lessons of the space they are in, so while confirmation is required (it is, by default, with several accounts) a member's space learns nothing until this is decided; (2) a swarm worker's own lessons live on the worker registry, which is the install's (S1-15); (3) removing a space's Soul when the space is deleted is S1-27's; (4) chat-app turns read lessons once their turns are bound to a space (stage 3). **Review follow-up, 2026-11-18:** the background lesson task binds a whole scope once Spaces is enforced -- the turn's space and the person the turn ran for, else the space's own account (`identity.acting_account`, the one answer the queued memory tasks use too) -- not the tenant alone, which ``require_scope`` refused. Tested with the old binding as its control. **Review fixes, 2026-11-18:** (1) the refiner's memory search reads the turn's space through a real recall, with the task as it ran before S1-10 (nothing bound: the search refused at DEBUG, no memory) as its control; off enforcement the task gets no tenant of its own (exactly as before). (2) Every change of a Soul is one `atomic_update` with its read inside (`_update_soul`), and `atomic_update` on Postgres locks the key itself first: `FOR UPDATE` locks nothing on a missing row, so two first lessons of two spaces could each start from nothing (`tests/test_atomic_update_first_write.py`, real Postgres, the raw order as the control; a lesson another process wrote meanwhile is kept, the old read-then-write order as the control). (3) Confirm and reject act only on a lesson still held (`needs_confirm`, a compare-and-set): an applied lesson was "rejected" while it stayed in the Soul, and a second confirm applied it twice. (4) The tests that showed kept behaviour are no longer named controls; the agent's base prompt and memory's forget are tested by running them. (5) On every install the model gets one current, fenced copy of the lessons, added by the supervisor: the agent baked one into its base prompt at start (stale after the first new lesson) beside the transport's own, twice a call on live; nothing bakes one now and the transports add none. (6) S1-20's row says `config_read` reads every space's lessons under enforcement. |
| S1-12 | Done for its own parts, 2026-11-18 | A document's tenant is its space and its owner the account that ingested it. The page, chat `/documents` and the agent's tools ask one function (`documents.principal.surface_scope`) and act as the asker in the asker's space; the stand-in principal `operator` is gone. Members of a space share its documents through `grant_member_access` (audited, made by each document's owner); the same grant gives the owner's account the documents an install made before, with no step to run. The agent's tools build the service before they resolve the scope, so the grant is made on the first call of a process too. The refusal follows enforcement: an install of the older multi-user mode keeps it (chat and the agent refuse, the page acts as each user), an install that enforces Spaces has none. Chat `/documents` reads the tenant its own turns run in (memory's `resolve_tenant_id`): with a retired memory mode stored (`per_platform`, `per_user`), `telegram` or the sender, as before, until Spaces is enforced or the owner presses "Use one memory" (S1-09's rule). **Files**: once Spaces is enforced each space writes to its own folder (`storage.space_folder`: the root for the owner's space, so nothing moves; `spaces/<id>` for others); before, every tenant writes to the root as before Spaces (`storage.writes_folder`), so nothing on an install that does not enforce Spaces changes place and a backup keeps its old shape. Reads look in the space's folder, then the root. Every content call names its space (`for_space`). Once enforced, the files kept in the root are copied into their space's folder at every start, journaled and resumable (`documents/space_files.py`); a bad id or an unreadable file is a WARNING and the copy goes on. **Rollback safety**: the root keeps every tenant's file in this release (GC's root keeps every live reference of any tenant), so an older build, which reads the root only, reads every document after a revert; a document a space adds after enforcement is only in its folder, and an older build does not read it until the newer one is back (nothing is deleted). Removing the root copies of a copied space is a later step, after S1-33. **Backups and the migration export** record each file where the live store keeps it, and a verify accepts either place: a backup taken before enforcement has the old shape; **an older build cannot verify a backup taken after enforcement**, which holds `spaces/...` files. `tests/test_document_spaces.py` (28 tests, replaces the operator-principal test; the old `agent` principal resolved through the tool's own scope path, older documents with no grant, a grant through the installed service during boot, the first chat rule on a retired memory mode, a member with no grant and a crossing surface as controls), `tests/test_document_space_files.py` (22 tests; a store writing the space folders before enforcement, one folder for every tenant once enforced, and the first collector that let the root forget a copied tenant, as controls), the matrix's 36 Documents cases (`tests/_document_space_cases.py`: every Documents route, the page and all 8 document tools; a document's index, unindex and the library search run on a private Knowledge store with a library in each space, as Knowledge is per space since S1-13; pending 500 -> 469 on its own, 356 -> 320 merged after S1-08 to S1-14). The S1-02 ratchet: 299 -> 296 on its own, 229 -> 226 merged. Also run on this change: every Postgres-marked document test on a real Postgres 18 (88 passed, one SQLite-only check skipped), the crash and intake-lease drills on SQLite and Postgres (64 passed), the container worker profile on Kazma's engine with an image of this code (`tests/test_document_worker_container_docker.py`, 24 passed), certification (`tests/test_document_certification_phase10.py` and its honesty test; the real-scan test is opt-in and skipped); all of them again on the tree merged with S1-08 to S1-14 (3b5f33569), with the same results. Left: (1) the run supervisor's document buttons act as the owner's account until S1-21; (2) S2-12 (leaving or removing a member) takes the leaver's `grant_member` ACL rows back; (3) with Spaces enforced, the agent's convert and redact copy their result into the install-wide `<data dir>/documents`, where the generators write too: S1-19's space folder; (4) with Spaces enforced, `/api/documents/ops/metrics` and `/ops/capacity` show the install's queue and storage totals beside the space's own quota (numbers only): S1-25 decides what a member sees; (5) the owner's folder is the root, which holds `spaces/`: S1-27's export and erase of the owner's space leave `spaces/` out; (6) the copy runs at start, so a switch to enforcement takes effect at the next start (S1-33's switch-on restarts); (7) worker scratch (`parser-runs`, `operation-runs`, `.stage`, `.upload-staging`) stays the install's (transient, swept). |
| S1-13 | Built, 2026-11-18 | Guards act only where Spaces is enforced; an install with one account, and the older multi-user mode (which keeps its refusal), behave as before. Enforced, the refusal is lifted for work bound to a space and kept for work bound to none (a chat app's `/kb` before stage 3: `knowledge_lifecycle.NO_SPACE_REFUSAL`). **A library's id is its space's**: the owner's ids never change; every other space's carry a mark no typed name can produce (`slug.<8 hex>`, `knowledge._library_id_in_space`), so two spaces each have their "docs", and a name or another space's id never reaches the other's library; the Chroma collection name keeps the mark apart. **Every boundary resolves the id once** (`KnowledgeStore.own_library_id` / `library_id_for`, the routes, the tools, `/kb`) and uses the answer from then on. **Reads keep to the space** (the store's tenant filter, on whenever Spaces is enforced): libraries, their chunks, search across libraries, the turn's injection and federated search. **A page is written only into a library of the bound space** (`publish_page`'s last check). **Crawls**: the journal is the install's; a space sees and resumes the crawls of its own libraries (`/api/kb/jobs`, a job, its resume, the outcome summary); a crawl a restart cut off resumes in its library's space (`kb_api._in_the_crawls_space`, the acting account); the daily canary runs as the owner's space on its private store. **A quota per space**: the text of a space's active chunks, 256 MiB (`KnowledgeSpaceFull`, a kind of the install's `KnowledgeStorageFull`); the install's 1 GiB stays the file's guard. Found and fixed on the way, each with its test: a route or tool that checked `get_library(x)` then read `x`'s chunks (list, search, delete) would have read another space's library once ids are resolved per space; a crawl into "docs" from a second space would have written into the first space's "docs". Evidence: `tests/_knowledge_space_cases.py` (23 matrix cases: 18 routes and 5 tools, through the product's router behind the real tenant middleware), `tests/test_knowledge_spaces.py` (21 tests: the id rule; the space filter, the write's last check and the crawl journal's filter each taken out as a control; the turn's injection; the quota; work bound to no space; the auto-resume; one account unchanged). The matrix's pending targets went from 392 to 369. Left: (1) real Chroma (meaning search) is not driven by the matrix, which runs keyword search; the vectors follow their library's id, which is the space's; (2) the X reply engine's library lookups run with no space bound (S1-17); (3) the Documents platform's indexing into a library is S1-12's; (4) the bell's crawl alerts are S1-32's; (5) per-space limits become settings with S2-08. **Review fixes, 2026-11-18:** (1) every name the install builds from a library id comes from one rule that keeps the space mark (`knowledge.library_name_part`): the fetched-pages folder turned "." into "_", so a space's "docs.<mark>" and an owner's library named "docs.<mark>" shared a folder, and the owner's delete wiped the other space's pages (live ids hold no ".", so no folder moves; a gate keeps every builder on the rule, the old folder rule as the control). (2) The crawl journal is filtered only where Spaces is enforced, never by the store's tenant filter, which production mode (live) turns on too: a deleted library's crawls stay listed, a research job is named as one, and the Dashboard's crawl card agrees with /metrics. (3) A space's bytes are measured on a read connection of their own, outside the store lock (a scan under it stalled every Knowledge call), and answer for a minute; the share is an admission check, as the install's quota is: an admitted crawl may carry its space past it, and the next one is refused. (4) A crawl whose space cannot be read at start waits for Resume with a WARNING; the others are still resumed. (5) The quota test's route half is a real check (507 `kb_storage_full`, the share lifted as its control), and the resume test has its control (the route called with nothing bound is refused). A search by meaning, 2026-11-18: with the real Chroma and the install's own embedder (`real_model`), each person's question answered only by meaning finds their own space's page and never the other's (`tests/test_knowledge_spaces.py`; the space filter taken out as the control). |
| S1-14 | Built, 2026-11-18 | Guards act only where Spaces is enforced; an install with one account, and the older multi-user mode (which keeps its refusal), behave as before. Enforced, the refusal is lifted for work bound to a space and kept for work bound to none (`cron.single_operator.NO_SPACE_REFUSAL`). **A job carries its space and the account that booked it** (`ScheduledJob.creator`, a new column, always stamped). **Every read and change keeps to the caller's space** (`scheduler._tenant_scope`): the list, one job, edit, cancel and Run again, on the page and through the four tools; another space's job id answers exactly as an unknown one. **A fire runs as a whole scope** (`scheduler._fire_binding`): the job's space and its creator, else the account Kazma's work for that space acts for (`identity.acting_account`); the fired turn's thread is recorded in that space. A job whose tenant is no space of the install does not fire (`no_space`). **Only the owner's own jobs use the owner's address** (N41): before people link their own chat apps (S3-05) every address a job can deliver to is the owner's -- a web turn's delivery target is the owner's Telegram -- so a booking by anyone else is refused (`NO_OWN_ADDRESS`, code `no_own_address`, worded on the page in both languages) and a job that would act for anyone else does not fire. Found and fixed on the way, each with its test: without that rule a member's reminder went to the owner's chat, their words with it; a job with an empty address adopted any job's address on the same thread id, of any tenant (`sibling_delivery_target` now takes the tenant). Evidence: `tests/_scheduled_space_cases.py` (13 matrix cases: 9 routes and 4 tools, through the product's router behind the real tenant middleware, on the real scheduler and store), `tests/test_cron_spaces.py` (17 tests: a real fire runs in its space, the fire with no scope as its control; another space's job id reads as unknown, the tenant rule taken out as the control; a job with no space does not fire; the booking rule and the fire's rule each taken out, the member's words then reaching the owner's chat; the sibling address; work bound to no space; one account unchanged). The matrix's pending targets went from 369 to 356. Left: (1) a member books when S3-05 gives them an address of their own (N41); (2) an X post booked from the page is the X account's, S1-17; (3) the cron engine's own passes (due jobs, recovery, the outbox, the alert outbox) stay the install's work. Review fixes, 2026-11-18: a new prompt and Run again need the caller to be the account the job fires as (`not_yours`; a member of a shared space could put their words into the owner's job); the fire's binding moved inside its `try` (an unreadable identity store gives the claim back, the database-away wait; while the database is away a reminder waits too once enforced); the missed path asks the fire rule (no notice for a job that would not fire); the sibling control runs the old lookup through the scheduler. Each in `tests/test_cron_spaces.py` with its control, and a member-of-a-shared-space check in three matrix cases. |

### Stage 1: spaces across every part of Kazma

Done when two accounts can use every part of Kazma on one install and
neither can reach the other's data, and the five refusals are gone.

| ID | What changes | Gate, with its negative control | The owner sees |
|---|---|---|---|
| S1-01 | The identity store (4.3). `platform.users` moves into it once. | `test_identity_store.py`: accounts, spaces and memberships through the real store on SQLite and Postgres; the move keeps every user. Control: the old list read-modify-write loses a user under two writers. | Settings → People lists accounts and spaces. |
| S1-02 | One scope: space and asker, bound together, with **no default**. `require_tenant_id`'s fallback to `default` is removed for content. | `test_no_space_no_data.py`: every store's reader called with no scope refuses; a source gate finds no `or "default"` on a content path. Control: the old fallback returns the owner's rows. | Nothing. |
| S1-03 | Every store, family and data folder declares its space rule and content columns. | `test_store_space_rules.py`: each entry of the registry has a rule; a planted undeclared store fails. | Nothing. |
| S1-04 | Sign-in is always an account once a second one exists. The shared secret, auto-login and API tokens are tied to the owner's account or off. | `test_sign_in_is_an_account.py`: each old entry, with two accounts, is the owner or refused. Control: the old principal has no account. | The owner signs in by name. A notice explains the change once. |
| S1-05 | Every route is classed `install`, `space`, `account` or `open`, in one table. The routes added since the first draft are in it like the rest: `/api/x/oauth2/*`, `/api/x/api-access`, `/api/a2a`, `/api/support/*`. | `test_route_scopes.py`: every route of the built app is in the table; a planted route fails. | Nothing. |
| S1-06 | The web names its space on every call; the server checks membership every time. A space switcher. | `test_web_request_space.py`: a member of A asking as B is refused on every route (enumerated). Browser test, EN and AR, two tabs in two spaces. Control: space kept in the session mixes two tabs. | A space switcher in the header. |
| S1-07 | A thread record: space and opener. Every place that runs a turn binds the scope from it. | `test_turn_scope_bound.py`: every function that runs a graph binds it (found from the source); a resume in another space is refused. Control: a resume with no record runs as `default`. | Nothing. |
| S1-08 | Chats, the spool, checkpoints (SQLite and Postgres), Time Travel, drafts, plans and receipts follow the space. Messages name their author. | `test_chat_state_spaces.py`: the matrix over every chat route; real Postgres for checkpoints. Control: the shared Postgres table reads across. | Each message shows who wrote it. |
| S1-09 | Memory: the space is the tenant, always. The mode setting is retired. `default` loses "the install's view". | The existing tenant matrix with the mode removed; `test_memory_default_is_a_space.py`. Control: the old owner rule answers another space's id. | Settings → Memory loses one option. |
| S1-10 | Lessons (the Soul) are per space. | `test_lessons_per_space.py`: a lesson learned in A is never in B's prompt, through the real graph. Control: the single Soul. | Nothing. |
| S1-11 | Shared-space memory: a "self" entry per member (4.7), as the R3 study specifies (`docs/plans/SPACES_R3_MEMORY_STUDY.md`, section 5) and N30 to N36 decide. An entry keyed by the account, filed when the fact is written, its vectors made from `user <predicate> <object>`; the source label `member_stated` (N34); who may forget what, with the honest wording (N32, N33); About me per member per space, with "Copy from my personal space" (N31); the shared-memory notice (N35); the step "forget every fact under an entry", which the leave and remove flow calls (N36, S2-12). | `test_memory_speakers.py`: two members state different facts about themselves; each is recalled for the right one. Control: one `user` entry overwrites. The gates of the study's section 5.2. The held-out questions (Gulf Arabic above all, where "I" is a prefix or a suffix) are written by a different session than the one that builds S1-11, before the builder sees them. | In a shared space Kazma knows who said what, and says that memory there is shared. |
| S1-12 | Documents: the owner is the account; the operator stand-in goes; chat and the agent act as the asker; content files per space. Refusal removed. | The operator-principal test replaced by `test_document_spaces.py`: page, chat and agent see the same documents within a space, none across. Control: the `agent` name. | Documents work for everyone. |
| S1-13 | Knowledge: libraries, vectors, fetched pages and crawls per space; quota per space. Refusal removed. | `test_knowledge_spaces.py`: search, inject and crawl never cross; real Chroma where installed. Control: a search with no space filter. | Knowledge works for everyone. |
| S1-14 | Scheduled: a job carries space and creator and fires in that scope. Refusal removed. | `test_cron_spaces.py`: a real fire runs in its space; another space's job id reads as unknown; a job with no space does not fire. Control: the fire with no scope. | Scheduled works for everyone. |
| S1-15 | Swarm: a task carries space and asker; workers run in that scope. Refusal removed. | `test_swarm_spaces.py`: every swarm route and tool by matrix; a worker's tool call lands in the asker's space. Control: the unscoped dispatch. | Swarm works for everyone. |
| S1-16 | Research sessions and reports per space. | `test_research_spaces.py`: list, read, download and delete by matrix. Control: the unfiltered list. | Research is per space. |
| S1-17 | Connected accounts (mail, calendar, GitHub, and X with every one of its sign-ins: the OAuth 1.0a keys, the Bearer token, Sign in with X) belong to a space; background refresh goes space by space. The git and GitHub tools use the space's own GitHub connection, or none. | `test_connected_accounts_spaces.py`: two spaces, two mailboxes; a refresh writes only its own; every reader names a space (source gate). A git push and every GitHub tool call in the workspace never use the owner's token. Control: the install-scoped names, and today's install-wide GitHub client. | Each space connects its own accounts. |
| S1-18 | The vault gains a space scope; the install list stays. | `test_vault_space_scope.py`: a space's secret is not returned to another space or to code with no space. Control: the `default` rung. | Nothing. |
| S1-19 | The space folder: file tools, uploads, images, exports and generated documents. `python_exec` mounts only it. | `test_space_files.py`: every tool that takes a path, in a sandboxed space, cannot reach outside its folder; real Docker run where present. Control: the install's workspace mount. | A Files area per space. |
| S1-20 | Host tools exist only in a host space (4.8). In the workspace only Development's tools exist; the admin-only host tools (4.11) are absent there too. With the admin's own Development off, none of its tools is offered or callable in the admin's personal space, and the admin tools still are (control: today's "always"). The settings tools are among them: once Spaces is enforced `config_read` can read every space's settings, the whole `self_improvement.space_evolution` document (every space's lessons) included (S1-10 review), so it is an admin's host tool, or answers for the caller's space only. | `test_host_tools_absent.py`: every registered tool is classed host, space or outside (a new tool with no class fails); in a sandboxed space a host tool is neither offered nor callable. Control: a policy that only hides it from the list. | Members have no shell, IDE or Workspace page. |
| S1-21 | Approvals and running work carry space and asker. Refusal of Running work removed. | `test_gates_and_runs_spaces.py`: pending approvals and runs by matrix. Control: the list with no space. | Each person sees their own work. |
| S1-22 | Model calls name space and asker. | `test_ledger_names_the_asker.py`: every call of a real turn, a job and a swarm task. Control: a call site with no asker fails the gate. | Spend per person on the admin page. |
| S1-23 | Settings keys classed `install`, `space`, `account`; the Settings page splits. | The settings class gate extended: a key with no scope fails. Browser test, EN and AR. | Three Settings areas. |
| S1-24 | Alerts, the digest, the weekly report and logs carry no member content. | `test_admin_signals_hold_no_content.py`: a word planted in a member's turn is in no alert, digest or log line at INFO or above. Control: today's digest names the chat. | The digest shows counts per person. |
| S1-25 | The admin boundary (4.10). Create, reset, delete. A reset tells the member. | `test_admin_cannot_open_a_space.py`: the admin against every space route by matrix; a reset leaves a record the admin cannot change. Control: a role check alone lets the admin in. | People page: create, reset, delete. |
| S1-26 | The key per space; a space's secrets sealed with it (4.9). | `test_space_keys.py`: a secret of A does not open with B's key; a new space has a key. Control: the single vault key opens both. | Nothing. |
| S1-27 | Count, export and erase one space, from the registry. | `test_space_export_erase.py`: one word planted through every writer in A and another in B; A's export holds A's and not B's; after erasing A no column of any store holds A's word and B is whole. Control: yesterday's erase misses a store. | "Export this space" and "Delete this space". |
| S1-28 | Backups and migration carry the identity store; a restore keeps spaces apart. | `test_backup_restore_spaces.py`: a real backup and restore, then the matrix. Control: a restore without the identity store. | Nothing. |
| S1-29 | The master matrix: every route and every agent tool, enumerated, as one space against another. | `test_space_matrix.py`: a new route or tool without a case fails. | Nothing. |
| S1-30 | The docs: the threat model's first section, the safety design, the guide. They say what Spaces promises and what it does not (4.9). | The docs gates; `test_threat_model_accuracy.py` updated. | A "Spaces" guide page, EN and AR. |
| S1-31 | The turn's environment note is the space's own: its folder and its connected accounts. A sandboxed space's note never names the install's folder, repository or GitHub login. | `test_environment_note_per_space.py`: a real turn in a sandboxed space; the note holds none of the install's paths or names. Control: today's note. | Nothing. |
| S1-32 | The bell, the Dashboard's open problems and the Running work card are per person. A research topic or chat title is shown only to members of its space. | `test_bell_and_problems_per_person.py`: a word planted in one person's research topic and chat title is in no other account's bell, problems list or runs card. Control: today's install-wide list. | Each person sees their own notices. |
| S1-33 | Before a second account can be switched on: per-person daily limits (S2-02) and feature grants (S2-11) pass their tests, the copy that leaves the machine is the encrypted kind (N10), and the deploy check and live tests sign in with an owner API token instead of the shared secret (N15). `kazma spaces migrate --check` names any of these that is missing. | `test_second_account_preconditions.py`: with any one missing, creating a second account is refused and says which. Control: the switch with no check. | A clear list of what is left before inviting anyone. |
| S1-35 | Memory statements that pick a row by its id alone (turns, facts, archived facts, weekly summaries, tool procedures: `id` is their whole key, decision 17) each name their tenant in the same WHERE, or are declared with why (an id that carries its space, a tenant check just before, a row chosen by tenant in the same statement). The memory audit's item 7: the entity key's gate (M21) widened. Found too many to declare one by one in S1-09 (81). | Until it is done, `tests/test_memory_id_only_statements.py` counts them and the count may only go down (planted statements as its control). Done when the ratchet is a gate: none unscoped and undeclared. | Nothing a person sees. |
| S1-34 | Each host space has its own root: the workspace switch and clone, the IDE service, the code index, IDE undo and the MCP binding follow the space of the task, not one install-wide row. | `test_host_space_roots.py`: a switch and a clone in the workspace leave the admin's personal space's root, IDE root, code index and MCP binding unchanged, and the other way round; a filesystem MCP call in the space it does not serve is refused. Control: today's one active row moves both. | Nothing. |

### Stage 2: per-person tools, limits and approvals

Done when the admin can set, per person, the features, the tools, the daily
limit and the models, and every approval reaches only the person who asked.

Order: the workspace (S2-12) is built first, because friends working in
the owner's workspace is what the owner wants most. S2-02 and S2-11 are
built in stage 1 (section 6, step 8); they are listed here because they
belong to this subject.

| ID | What changes | Gate, with its negative control | The owner sees |
|---|---|---|---|
| S2-01 | A tool list per person, within what their space may have. Applied when tools are offered and again when one is called. | `test_person_tool_policy.py`: a tool switched off for a person is neither offered nor callable, in chat, a job and a swarm worker. Control: hiding it from the offer only. | Tick boxes per person. |
| S2-02 | (Built in stage 1.) A daily spending limit per person, counted from the ledger. A limit in tokens covers models with no known price. | `test_person_daily_limit.py`: the turn that would pass the limit is refused before any model call; another person is not affected. Control: the install-wide sum. | A limit per person; today's spend beside it. |
| S2-03 | Models per person: which they may use, and their default. | `test_person_models.py`: a model not allowed is refused in the picker, `/model` and a job. Control: the install's list. | A model list per person. |
| S2-04 | A member's own model keys (optional). Stored in their space, sealed with its key. Their spend on their own key does not count against the limit. | `test_member_own_keys.py`: a call with the member's key never uses the owner's; the key is not shown to the admin. Control: the install-scoped provider row. | "Use my own key" in My account. |
| S2-05 | Approvals go to the asker: the web card, the bell, Web Push and the chat app. Nobody else can answer. The one rule that decides who may answer also reads an "approver" set on the asker's account (N4): empty for everyone, so it changes nothing yet. | `test_approvals_reach_the_asker.py`: another member of the same space is refused; the gate stays pending. Control: the thread-owner rule lets them. | Each person gets their own cards. |
| S2-06 | Standing approvals ("Allow tool", YOLO windows) belong to the person and the chat. | `test_grants_per_person.py`: a grant by one person does not skip another's card. Control: the per-thread grant. | Nothing. |
| S2-07 | Background work uses its creator's tools and limit as they are when it runs. A disabled creator pauses their jobs. | `test_background_uses_creator_policy.py`: a job booked before a tool was taken away does not use it. Control: the tools frozen at booking. | A paused job says why. |
| S2-08 | Limits per space: disk, documents, knowledge, work running at once. | `test_space_quotas.py`: one space at its limit does not slow or stop another. Control: the install-wide quota. | Usage per space on the admin page. |
| S2-09 | Every action is recorded under the person's name. An activity page per space, for its members. | `test_actions_name_the_person.py`: every route that changes something writes the account (enumerated). Control: a route recording the session label only. | An Activity page in each space. |
| S2-10 | The People page: features, tools, limit, models, spaces, last sign-in, sessions. | Browser test, EN and AR, reached by the sidebar. | The page itself. |
| S2-11 | (Built in stage 1.) Features per person (4.8). One table names each feature's pages, routes, tools, chat commands, background work and connected accounts. A feature off for a person is refused at every one of them, and the check is made again when the work runs. | `test_feature_grants.py`: every page, route, tool, command and background loop belongs to a feature or is declared always on (a new one with neither fails). With X Studio on for Y and off for G: G has no menu entry, every X route refuses, no X tool is offered or callable in chat, a job or a swarm worker, `/x` refuses, and the mentions loop skips G; Y works. Control: a grant that only hides the menu entry. Browser test, EN and AR. | A switch per feature for each person. A person sees only what they were given. |
| S2-12 | Shared spaces (4.11), in two kinds: a plain one (sandboxed, no project folder, the default) and one with a project folder (host, admin only). The create-space screen and the People page say which kind. A plain shared space never offers Development or GitHub, to anyone. For the host kind: a space the admin invites people into; the Development feature, granted per person; the space shown in the header; "New private chat"; moving one's own chat, document or file between one's personal space and the workspace; leaving a shared space, and a member's removal by its owner or the admin: the facts under the member's own memory entry are forgotten through S1-11's step, the leave screen says so first, their messages stay (N36). | `test_workspace_and_personal.py`: two members and the admin. A member who leaves, and one who is removed: no fact under their entry is recalled afterwards, by anyone, and their messages are still in the chat (control: the entry left in place). A workspace chat, fact and document are seen and recalled by all three; a thing in one member's personal space is found by nobody else, by any route, tool, search or recall, from the workspace or from another personal space. A member without Development has no shell, IDE or git, offered or callable; with it, they do. A moved chat is in exactly one space after the move, in every store. Control: a "private" mark on a workspace row, which recall and search still return. Browser test, EN and AR. | The space name in the header; "New private chat"; "Share with the workspace". |

### Stage 3: linking chat-app accounts

Done when each person's Telegram, Discord, Slack or WhatsApp account is
their own Kazma account, and an unlinked sender gets nothing.

| ID | What changes | Gate, with its negative control | The owner sees |
|---|---|---|---|
| S3-01 | Linking: a signed-in person gets a short code in My account and sends it to the bot in a direct chat. One use, ten minutes. | `test_chat_link_flow.py`: a real adapter; a used, old or wrong code links nothing; a code typed in a group is refused. Control: a code that can be used twice. | "Link Telegram" in My account. |
| S3-02 | The sender rule becomes "a linked, enabled account". The allowlists retire. The owner's known ids become the owner's links. | The private-by-default test re-pointed: every accept path asks the link. Control: the allowlist lets a listed stranger act as the owner. | The allowlist fields go; links are listed. |
| S3-03 | A chat's space: the person's own by default, `/space` to change it, and a group bound to one shared space by one of its members. | `test_chat_space_binding.py`: a message runs in the bound space; a sender who is not a member of it is ignored in the group. Control: the sender's thread with no space. | `/space` in every chat app. |
| S3-04 | Approvals and results in chat apps go to the asker. In a group only the asker's press counts. | `test_chat_approvals_asker.py`: another person's press answers "not yours" and changes nothing. Control: the gateway-admin rule. | Cards arrive in the asker's own chat. |
| S3-05 | A web turn's notices go to the asker's linked chat, never the owner's. | `test_web_turn_delivery_target.py`: two people, two targets. Control: the owner's Telegram stamped on every web turn. | Reminders reach the person who set them. |
| S3-06 | `/sessions`, `/switch`, `/memory`, `/runs` and the rest act as the linked account in the bound space. | The slash-command gates re-pointed; a matrix over every command. Control: the sender-owner pointer. | Nothing new. |
| S3-07 | Unlinking, and a disabled or deleted account: the sender is refused at once; replies waiting for them are dropped. | `test_unlink_stops_everything.py`: outbox, inbox, callbacks and a pending card. Control: a waiting reply still sent. | "Unlink" in My account. |
| S3-08 | Chat-app admin (`KAZMA_GATEWAY_ADMINS`) retires: admin commands need a linked admin account. | `test_chat_admin_is_an_account.py`. Control: the environment list. | Nothing. |

### Stage 4: hardening for the cloud, with the independent review

Done when an install reachable from the internet, with people who do not
all trust each other, has been attacked by someone other than its authors,
and what they found is fixed.

| ID | What changes | Gate, with its negative control | The owner sees |
|---|---|---|---|
| S4-01 | Postgres row-level security on every space table, bound per request. | `test_pg_row_security.py` on real Postgres: a statement with no space filter returns nothing. Control: the same statement without the policy returns both spaces. | Nothing. |
| S4-02 | Stronger sign-in: a second factor, sign-in limits per account, invitation links that expire, a list of each person's sessions. | `test_sign_in_hardening.py`. Control: a password alone signs in when a second factor is required. | Second factor in My account. |
| S4-03 | The switches that open an install are refused once a second account exists (sign-in off, the open chat-app switch, demo mode). | `test_open_switches_refused_with_members.py`: each switch, at boot. Control: the boot guard without the rule starts. | A clear refusal at start. |
| S4-04 | Fairness: limits on turns, jobs, uploads and crawls per person and per space; one space cannot starve another. | `test_space_fairness.py` on the real queues. Control: one queue for all. | Nothing. |
| S4-05 | Members' code runs only in a container or microVM, with no network unless the admin allows it. | The sandbox tests, per space; a real Docker run. Control: the local fallback. | Nothing. |
| S4-06 | A space can move: export a sealed space bundle from one install and import it into another. | `test_space_bundle_round_trip.py`: export, import into a fresh install, then the matrix. Control: a bundle carrying another space's row. | "Move this space" for its owner. |
| S4-07 | Key care: change a space's key; a deleted space's key is destroyed. | `test_space_key_rotation.py`. Control: an old key still opens a new secret. | Nothing. |
| S4-08 | A cloud profile: the checks a public install must pass at boot (TLS, a declared proxy, sign-in on, a container engine for code, backups set). | `test_cloud_profile.py`: each missing piece refuses or warns as declared. | `kazma doctor` shows the cloud profile. |
| S4-09 | A soak with many spaces: real turns, jobs, uploads and restarts; every cycle checks nothing crossed. | `scripts/spaces_soak.py` and its harness test with planted crossings. | A report. |
| S4-10 | The review pack: scope, claims with their tests, setup, what is out of scope. Then the review, by someone outside. Then the fixes, each with a test. | The reviewers' report and the fix list. Never replaced by a self-review. | The report, and what was fixed. |
| S4-11 | The published threat model and safety design say what was reviewed, by whom, and what still is not a boundary. | The docs gates. | The pages. |

## 6. Order inside stage 1

Stage 1 is large. It is built in this order, and nothing is deployed with a
second account allowed until the last step:

1. S1-01 to S1-07: identity, the scope with no default, the tables.
2. S1-29's matrix harness, empty, so each later item adds its cases.
3. The stores that already keep tenants apart (S1-08, S1-09, S1-12), and
   memory's id-only statements (S1-35).
4. The five parts that refuse (S1-13 to S1-15, S1-21), one at a time. Each
   keeps its refusal until its own matrix passes.
5. Files and host tools (S1-19, S1-20), the environment note per space
   (S1-31) and a root per host space (S1-34).
6. Accounts, secrets and keys (S1-17, S1-18, S1-26).
7. Export, erase, backups, admin boundary, signals (with the bell,
   problems and runs per person, S1-32), docs.
8. Per-person daily limits and feature grants (S2-02, S2-11), brought
   forward from stage 2: no friend ever uses the owner's model keys without
   a limit and a grant. Then the preconditions check (S1-33).

Until step 8 is done and S1-33 passes, an install with one account behaves
exactly as today, and an install with two keeps today's refusals. The
owner's rule "deploy only complete phases" holds: single-account behaviour
is deployable at each step; multi-account is switched on once.

## 7. Moving today's install

The owner's data becomes the owner's space. Nothing is copied or rewritten
that does not have to be.

1. **The space.** A space with the id `default` is recorded, kind `host`,
   named by the owner. Rows that already say `default` are already in it.
2. **The account.** An owner account is made, role `admin`, the only member
   of `default`. An existing local user with role `admin` becomes it. The
   shared secret keeps working as that account's sign-in until the owner
   sets a password.
3. **Rows with no space.** Stores that gain the column are filled with
   `default` in one pass per store, through the existing column helper.
   Empty tenants on approval rows and chat-app rows become `default`. A
   swarm task's space moves from its metadata into a column.
4. **Memory tenants.** An install that used `per_platform` or `per_user`
   memory has tenants such as `telegram`. They are counted first and
   reported. Nothing is merged without the owner's word (R6). The live
   install uses `shared`, so it has none.
5. **Secrets.** Vault rows under tenant `default` stay (the X keys are
   among them). The mail and calendar sign-ins move from the install's scope
   to the `default` space. The space key is made, and the space's secrets
   are sealed again with it.
6. **Files.** `attachments/`, `images/`, `exports/`, `documents/` and the
   default workspace are the `default` space's folder by a mapping, not a
   move. The host space keeps the real workspace, as today.
7. **Chat apps.** The owner's known ids become links to the owner's account
   (stage 3). Until then the sender rule is unchanged.
8. **Jobs.** Existing scheduled jobs and swarm tasks get the owner as their
   creator.
9. **The workspace.** It is not made by the move. The admin makes it when
   inviting the first friend: a new, empty space with its own project
   folder. Sharing the X account with it moves that account out of the
   owner's personal space, because a connected account belongs to one space.
10. **What the older multi-user mode filed under another key** (found in
    the S1-08 review). Once Spaces is enforced, everything kept by thread
    follows the thread's record; before, it followed whatever tenant was
    bound. Two cases need the move, both listed by `--check` thread by
    thread:
    - **Checkpoints in the wrong file.** Under the older multi-user mode a
      thread recorded in space X but resumed with no tenant bound (a
      watchdog, a resume at start, an approval's timeout) wrote its
      checkpoints into the owner's `checkpoints.db`. Once enforced, X's
      thread is read from `checkpoints_X.db` (or the shared table's rows
      held to X), so those steps vanish from X, and the owner's clear-all
      would delete them. The move copies each such thread's checkpoints to
      the file of the space its record names, compares them, and only then
      removes them from the owner's file. Until the move has run, Spaces is
      not switched on.
    - **Drafts and notes under the state's tenant.** A draft or a
      scratchpad note was filed under the tenant the turn's state carried
      (`graph_tool_worker.py`, the draft card's lookup, and the scratchpad
      write). Once enforced they are filed and looked up under the bound
      scope's space, so a draft filed under another value is not found by
      its own chat. The move re-files every draft and note whose tenant is
      not its thread's recorded space under that space.

Rules for the move:

- One command, `kazma spaces migrate`, with `--check` first. It reads only,
  and says what it would change, store by store.
- A backup is taken first. Every step is recorded before it is made, and a
  stopped run continues where it stopped.
- It can be run twice with no second effect.
- It is tested on a copy of a store seeded by the product's own writers, and
  on a copy of the live install's shape, before it runs on live.
- An install that never adds a second account still gets the move, and sees
  no difference.

## 8. Risks and open questions

| # | Risk | What the plan does about it |
|---|---|---|
| R1 | One missed filter leaks a space. This is the main risk of the row model. | Bound store handles, the statement gate, the matrix over every route and tool, Postgres row security, the soak, the outside review. |
| R2 | Code with no space quietly acts as the owner. | No default (S1-02). It is the first item because every later one leans on it. |
| R3 | "Who is I" in a shared space changes how memory files facts. Memory is the most hardened part of Kazma. | A short study first, on the memory benchmark, before S1-11 is built. A space with one member stays exactly as today. **The study is done (2026-11-18): `docs/plans/SPACES_R3_MEMORY_STUDY.md`.** It chooses an entry per member, filed when the fact is written, and measures it against today's single entry; The owner answered its seven questions on 2026-11-18 (N30 to N36). |
| R4 | The owner's own secrets move scope (mail, calendar, X). A mistake here signs the owner out. | The move is checked by reading each secret back after it; the old copy is kept until the first good refresh. |
| R5 | Size. Stage 1 touches every store and most routes. | The order in section 6; each part keeps its refusal until its own tests pass; nothing multi-account ships early. |
| R6 | Old memory tenants from the retired modes have no clear owner. | Counted and reported; the owner decides per tenant. |
| R7 | The machine's owner can still read every space on disk and in backups. | Said plainly in the product and the docs (4.9). The encrypted version is the answer, later. |
| R8 | A reset by the admin lets the admin sign in as the member. | The member is always told, and it is recorded (N3). The encrypted version closes it properly. |
| R9 | One bot serves everyone. Messages of all members pass through the admin's bot. | Said plainly; a bot per space is possible later (N7). |
| R10 | Shared model keys: one person's use can hit the provider's rate limit for all. | Per-person limits (S2-02) and fairness (S4-04). |
| R11 | Group chats: people who are not members can read what Kazma answers there. | A group is bound to one shared space on purpose, by a member; the bind message says so. |
| R12 | The prompt cache and the tool list differ per person, so cost per turn may rise. | Measured in stage 2 before and after. |
| R13 | A scheduled job outlives its creator's rights. | S2-07: rights are read when it runs. |
| R14 | The review finds design faults late. | The review pack is started in stage 1, and the reviewers may be asked to read this plan before stage 2. |

Open questions that need study, not a decision:

- How Postgres checkpoints carry the space without changing LangGraph's
  tables (a thread record beside them is the plan).
- Whether the Neo4j copy of the memory graph is kept per space by a label or
  by a database.
- How a shared space's "About me" works: answered by the R3 study and N31
  (one text per member per space).

## 9. Decisions needed

N1 to N29 were answered by the owner on 2026-11-17. N21 to N29 came from
three reviews the same day. N30 to N36 are the seven questions of the R3
study (`docs/plans/SPACES_R3_MEMORY_STUDY.md`, section 6, where they were
D1 to D7), answered on 2026-11-18. Each row gives the question and what was
decided.

| # | Question | Proposal |
|---|---|---|
| N1 | Do friends in the owner's space get host tools (the shell, the owner's files, the owner's email)? O3 says members never do; the owner also said friends use everything as the owner does. | **Decided by the owner, 2026-11-17 (4.11).** Friends work in the shared workspace. Host tools are the feature "Development": off for a new friend, turned on per person by the admin. Never in a personal or family space. |
| N2 | Inside a space, is everything shared, chats included? If friends join the owner's space, they see the owner's past chats, memory and documents. | **Decided by the owner, 2026-11-17 (4.11).** The workspace is one shared world with one memory. Everyone, the owner included, also has a personal space for what they do not share. |
| N3 | What does "reset an account" mean? | **Decided by the owner, 2026-11-17.** The admin sets a new one-time password. The member is told, and it is recorded where the admin cannot change it. It never erases data. |
| N4 | May a space's owner be the one who approves for another member (for example, a child's posts or spending)? | **Decided by the owner, 2026-11-17.** Only the asker answers, for now. The code is built for more: each account has an "approver" setting, empty by default, and the gate reads it (S2-05). No page sets it until the owner turns the option on. |
| N5 | Who can make a shared space, and who adds people to it? | **Decided by the owner, 2026-11-17: two roles.** The admin makes spaces and accounts and grants what each person may use. A space's owner adds and removes members from existing accounts. Two guards: a personal space has one member forever, and adding anyone to a shared space is never silent. |
| N6 | Rows with a space id, or a database per space? | **Decided by the owner, 2026-11-17.** Rows and a folder per space now (4.2). A database per space is kept open for the encrypted version, for memory and knowledge. |
| N7 | One bot per platform for everyone, or a bot per space? | **Decided by the owner, 2026-11-17.** One bot. A direct chat with it is private and runs in the person's own space. In a group, Kazma answers only when it is mentioned, and only from the one shared space the group is bound to, never from a personal space. A member's own bot later if asked. |
| N8 | The daily limit: in money, in tokens, or both? What happens at the limit? | **Decided by the owner, 2026-11-17.** Both: money where the price is known, tokens always. At the limit the person's turns stop until the next day, and they are told. The admin can raise it. |
| N9 | Deleting an account: at once, or after a wait? What happens to a shared space when its last member leaves? | **Decided by the owner, 2026-11-17.** A seven-day wait, during which the admin can undo it. A shared space with no members is erased after the same wait. |
| N10 | Whole-install backups hold every space, and the owner holds the backup passphrase. Is that acceptable until the encrypted version? | **Decided by the owner, 2026-11-17.** Yes, with encryption in two steps. **Now (this plan):** every copy that leaves the machine is encrypted (restic); the plain zip copy is refused once a second account exists. On an install with more than one account, the security report checks that the machine's disk is encrypted (BitLocker, FileVault, LUKS) and says so if not. A member's own export of their space is sealed with a password they choose (S1-27). Members are told on first sign-in what this does and does not cover. This protects against a stolen disk, drive or cloud account. **Later (the encrypted version, 4.9):** a space's key is held by its members, so the backup and the live data cannot be read by the machine's owner either. Encrypting only the backup against the owner, while the live data on the same machine is readable, would promise more than it gives. |
| N11 | What may the admin see about members? | **Decided by the owner, 2026-11-17.** Spend, counts and states per person. Never titles or text of a space the admin is not in: other people's personal spaces and a shared space the admin did not join. |
| N12 | Is a read-only member needed (today's `viewer`)? | **Decided by the owner, 2026-11-17.** Not now. Two roles in a space: owner and member. |
| N13 | Which outside tools do members get by default: web search and fetch, the browser, image generation? | **Decided by the owner, 2026-11-17.** Web (search, fetch, crawl), research, image generation and voice on. The browser and computer use off until the admin grants them. |
| N14 | Is a second factor required for every account on a cloud install? | **Decided by the owner, 2026-11-17.** Required on the cloud profile, optional at home. Required everywhere for anyone who holds Development. What follows: the admin's personal space always has host tools, and being the admin of an install with other people is the larger reason: the admin needs a second factor from the day a second account exists (N25). |
| N15 | After Spaces, does the owner still sign in with the shared secret? | **Decided by the owner, 2026-11-17.** It works until the owner sets a password, then it is retired for the web. The CLI on the machine keeps acting as the owner. |
| N16 | Who does the independent review, and when do they start? | **Decided by the owner, 2026-11-17.** Chosen at the end of stage 1; they read this plan and the stage 1 code before stage 2 ends, and test the whole in stage 4. |
| N17 | The name in Arabic. | **Decided by the owner, 2026-11-17.** "مساحة" for a space, "حساب" for an account. |
| N18 | Feature grants (4.8, S2-11). What does a new member start with? Are grants per person or per space? May a space's owner grant features to its members, or only the admin? | **Decided by the owner, 2026-11-17.** A new member starts with chat (with its memory), documents, knowledge, web (search, fetch, crawl), research, image generation and voice; everything else is off until the admin turns it on. Grants are per person, also inside a shared space. Only the admin grants, never a space's owner. |
| N19 | When the workspace is made, where does today's data go? Today everything is the owner's alone. | **Decided by the owner, 2026-11-17.** It stays in the owner's personal space, private. The workspace starts empty. The owner then picks what to share: chats, documents, knowledge libraries, the X account, and memory by topic (for example everything about Kazma's development). Nothing becomes shared by default. |
| N20 | May a private chat read the workspace's memory (one way), so a friend can ask about the work from their private chat? | **Decided by the owner, 2026-11-17.** No at first: each chat reads only its own space. It can be added later as a per-person option. |
| N21 | (From the review of 2026-11-17.) Who approves for a person holding Development? Only the asker answers their own cards (N4), so a Development holder would approve their own file writes and folder grants. | **Decided by the owner, 2026-11-17.** The admin approves a Development holder's always-ask actions and folder grants, from day one. Other cards stay with the asker. |
| N22 | (Review.) The key per space protects nothing until members hold it, and sealing the owner's live secrets again is risk R4. Build it in stage 1 or stage 4? | **Decided by the owner, 2026-11-17.** Stage 1 makes the key and seals new spaces' secrets with it (O5 stands). The owner's existing secrets are not sealed again until stage 4, with key rotation. R4 then covers only moving mail and calendar to the space scope. |
| N23 | (Review.) Mention-only replies in groups (N7) would change how the owner's groups behave today. | **Decided by the owner, 2026-11-17.** Mention-only applies to a group bound to a shared space. The owner's existing groups keep today's behaviour until the owner changes each one. |
| N24 | (Review.) With the read-only role gone (N12), what do OIDC sign-ins with role "viewer" or "operator" become? | **Decided by the owner, 2026-11-17.** Both become members with the starting features. An OIDC "admin" claim never makes an admin: there is one admin, the owner's account. |
| N25 | (Second and third review.) Does the admin need a second sign-in factor? An admin can make accounts, reset a member's password and sign in as them (N3), and run the admin tools, with or without Development. | **Decided by the owner, 2026-11-17.** Yes, from the day a second account exists, because they are the admin. S1-33 refuses a second account until the admin has one. N14's rule for Development stays for members who are granted it. |
| N26 | (Second review.) Which GitHub account does the workspace use? Today's sign-in is the owner's, and the owner can push to `main` of the public repository. | **Decided by the owner, 2026-11-17.** The workspace connects its own GitHub account or token, with no right to bypass the rules on `main`. Until one is connected, git push and the GitHub tools refuse there. The owner's sign-in stays in the owner's personal space. |
| N27 | (Second review.) The filesystem MCP server is bound to one folder. Which host space does it serve? | **Decided by the owner, 2026-11-17.** The admin's personal space, as today. It refuses in the workspace until each space can have its own instance; Kazma's own file tools work there. |
| N28 | (Third review.) Must the admin always carry the Development tools? An owner who never codes has no use for the shell, IDE, git and GitHub tools. | **Decided by the owner, 2026-11-17.** Development is a per-person switch for the admin too. It starts on, and the admin can turn it off for themself. The admin tools stay either way. |
| N29 | (Third review.) Is every shared space a development workspace? | **Decided by the owner, 2026-11-17.** No. Two kinds: a plain shared space (sandboxed, the default) and a shared space with a project folder (host, admin only). An install can have only plain ones. |
| N30 | (R3 study, D1.) Which spaces file memory's facts by member? | **Decided by the owner, 2026-11-18.** Every shared space, from its first turn, also while it has one member. A personal space never does: it keeps the one `user` entry. Nothing is re-filed when a second member joins. |
| N31 | (R3 study, D2.) "About me" in a shared space. | **Decided by the owner, 2026-11-18.** One text per member per space, shown only in that member's own turns; each member edits their own. Plus an action "Copy from my personal space", so nobody retypes it. It copies once; the two texts are independent after that. |
| N32 | (R3 study, D3.) Who may forget another member's turn in a shared space, or keep a shared chat out of memory? | **Decided by the owner, 2026-11-18.** The turn's author and the space's owner. Everyone can always forget their own turns. |
| N33 | (R3 study, D4.) A member forgets a fact about themself that another member stated. Does the other member's message go too? | **Decided by the owner, 2026-11-18.** The fact goes and is not learned again from that message. The message stays. Forget says so plainly: the other member's message still holds the words and can still be recalled, and its author or the space's owner can forget it. |
| N34 | (R3 study, D5.) May a member state facts about another member? | **Decided by the owner, 2026-11-18.** Yes. It is kept as "said by" (the source label `member_stated`) and never replaces what the member said about themself. A member's own words always win. |
| N35 | (R3 study, D6.) What does recall show a member about the others in a shared space? | **Decided by the owner, 2026-11-18.** Everything in the shared space, under the member's name. And the space says so before anyone types something private: a visible notice, "Memory here is shared with Sara and Omar" (the members' names, English and Arabic), near the composer and on the Memory page. |
| N36 | (R3 study, D7.) A member leaves a shared space, or is removed. | **Decided by the owner, 2026-11-18** (changed from the study's proposal, which kept them). Every fact filed under the member's own entry is forgotten: what they said about themself, and what others said about them. Their messages in shared chats stay, as the conversation's history. Facts about shared things (a trip, a project) are not under their entry, so the space keeps them. The leave screen says this before they leave. An admin's or owner's removal applies the same rule, so a removed member's facts do not linger without their say. Built by S2-12 (the leave and remove flow of a shared space); deleting an account (S1-25, N9) runs the same step for every shared space the account was in. S1-11 provides the step: forget every fact under an entry, through the existing forget path, so the ledger, the mirror and the graph copy follow. |
| N37 | (S1-09 review.) Fairness between spaces in the one memory queue. The memory worker has one queue with two lanes (memory writes, maintenance), each claimed oldest first. One space that queues hundreds of deep passes -- a history import of a few thousand turns -- holds up every other space's memory of its own turns until it drains. | **Open.** Proposal: the memory lane claims in turn by space (the oldest task of each space, round robin), from a space column the queue fills when a task is queued (the payload's tenant, or the row's for a row-named task); a history import's tasks go in a third, slower lane. An install with one space claims exactly as today. |
| N38 | (S1-09 review.) The Postgres memory mirror's key when two installs share one Postgres. The mirror's tables (`kazma_episodes`, `kazma_beliefs`) are keyed by `id` alone. S1-09 salts a turn's id with its space, but every install's owner's space is `default`, so two installs that hold the same turn (the same chat id, turn and text: a history both imported, a test chat) write one mirror row, and the second install's write changes the first's. | **Open.** Proposal: the mirror's key becomes (install, id). Every mirror row already carries its writer's install (`metadata.install`); it becomes a column of the key, filled from the metadata at switch-on, and recall's top-up keeps reading only other installs' rows. Until then, Spaces is not switched on for two installs that share one mirror (`kazma spaces migrate --check` names it). |
| N39 | (S1-10.) Who confirms a lesson Kazma holds for confirmation in a space? Once there are several accounts, a lesson is held until someone confirms it. The confirm queue's routes are admin-only, and with Spaces enforced the admin sees only the held lessons of the space they are in: a member's space learns nothing until this is decided. | **Open.** Proposal (the main session's): the space's own owners confirm -- in a personal space, its person; in a shared space, its co-owners. The admin has no review view of other spaces' held lessons, following the owner's decision O1 (the admin cannot open members' spaces). Nothing more is built until the owner decides. |
| N40 | (Memory audit, decision 17.) How a memory row carries its space, now and in the sealed version. | **Decided by the owner, 2026-11-18** (the memory audit's four decisions, agreed). Rows now: every memory row carries its space in `tenant_id`, and the space is salted into the ids of new rows (S1-09: the owner's ids never change). No table's key is rebuilt: `id` stays the whole key of turns, facts, archived facts, weekly summaries and tool procedures, so every statement that picks a row by its id alone names its tenant (S1-35). One memory database per space is kept for the sealed version (stage 4). The owner also said losing the live install's memory would be acceptable: nothing changes in how it is built, and the stakes of the R6 migration step are lower. |
| N41 | (S1-14.) Who books a scheduled job before people link their own chat apps? A job is delivered to the address captured when it is booked, and until S3-05 every address Kazma has is the owner's: a web turn's delivery target is the owner's Telegram. A member's reminder would reach the owner's chat, their words with it. | **Built as a refusal, 2026-11-18**: once Spaces is enforced only the owner books; anyone else is told sending to their own chat app comes later (`no_own_address`), and a job that would act for anyone else does not fire. S3-05 lifts it by giving each person an address of their own. Open for the owner: whether stage 1 should give members another place a result can reach (the bell, S1-32) before S3-05. |

## 10. Not in this plan

- Encrypting a space against the machine's owner (4.9 builds toward it).
- Billing, sign-up by strangers, or running Kazma as a public service.
- A bot per space, and a read-only role.
- More than one install sharing the same spaces.
- Changing how a single-account install works.

## 11. Execution rules (when work starts)

- Each fix or feature lands with its behaviour test, its class gate and its
  negative control.
- Each part keeps its refusal until its own matrix passes.
- No deploy to the live install part-way through a stage.
- The plan's tables are updated with the state and the evidence of each
  item, as the Documents plan does.
- The repository is public. A weakness found on the way is fixed with its
  test before it is written down anywhere in it.
