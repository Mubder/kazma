# Spaces R3: who is "I" in a shared space

A study for risk R3 of `docs/plans/SPACES_PLAN.md` (section 4.7), written
before item S1-11 is built. It changes no product code. Read from the code at
commit `42c3c294b` on 2026-11-18.

The question: memory assumes one user per tenant. "I live in Kuwait" becomes
a fact about `user`. In a space with several members each member needs their
own "self", the speaker is "I" when memory is read, and the other members are
named people. A space with one member must stay as it is today.

Contents: 1. how "I" is filed today; 2. the designs and the one chosen;
3. the rules per member; 4. the measurement; 5. what S1-11 should be;
6. the owner's decisions.

## 1. How "I" is filed today

### 1.1 One entry, written as the word `user`

The person is one entity per tenant, with the id `user`
(`self_hub.HUB_ID`). Nothing in the write path names a person: the id is a
literal in every writer.

| Where | What it does | The speaker |
|---|---|---|
| `agent/graph_respond.py`, `respond_node` | Marks the finished turn for memory: `_post_turn_memory = {session_id, turn, tenant_id}`. | **Lost here.** Since the foundation the turn's scope (space and asker) is bound in this node, and the record does not take the asker. |
| `consolidator.remember_turn`, `_schedule_post_turn_memory`, `_run_turn_memory`, `_defer_turn_memory` | Carry session, turn, tenant and time to the episode writer, the quick pass and the durable queue. | Not carried, in the thread or in the queued payload. |
| `dual_write.episode_row` | The episode row: question, answer, tier, metadata `{source}`. | No author column; the metadata has no author. |
| Quick pass: `belief_extractor.extract_beliefs_heuristic`, `arabic_facts.extract_arabic_facts`, `current_facts.parse_current_facts` | Read the user's message for "my name is", "I live in", "اسمي", subscription resets. | Every fact is `{"subject": "user", ...}`. |
| Deep pass: `belief_extractor._llm_extract` and `_EXTRACT_SYSTEM` | The model reads `User: ... / Assistant: ...` of one episode. | The prompt says `Use "user" for the user themselves`. The episode it reads has no author. |
| `belief_extractor._apply_beliefs_to_v2` | Resolves entities, then `mutate_belief`. | `clean["subject"] == "user"` is special: never resolved, always a node, never anchored. |
| `ego_anchor.anchor_leaf_subject` | Links every other subject to the person: `user related_to <subject>`. | The literal `user`, in the write and in three SQL checks. |
| The agent's tools (`tool_builtins/memory.py`) | `memory_store` writes `user noted <text>`; `memory_link_entities` treats `user` as the hub. | The literal `user`. |
| `turn_reconcile` | Rebuilds episodes and facts from the chat store. | The chat store's messages have no author (S1-08 adds one). |
| `history_import` | Imports a ChatGPT or Claude export as episodes. | One person by construction. |
| `backfill_v2` | The older migration's prompt: "subject is always 'user' unless clearly about something else". | The literal `user`. |

`belief_mutation.mutate_belief` itself knows no person. Its key is the
subject and the predicate: a single-valued fact has one current value per
`(tenant, subject, predicate)`. With one `user` entry, a second person's "I
live in Doha" is a newer statement of `user lives_in` and replaces the first
person's "I live in Kuwait". Both come from the user's own channel
(`user_stated`), so the trust gate lets it through. This is the control S1-11
names, and section 4 measures it.

### 1.2 Where a speaker would have to be known

Writing:

1. `respond_node`, when it marks the turn (the asker is bound there).
2. The queued copy of a turn (`post_turn_memory` payload) and the episode row,
   because the deep pass and turn reconcile run later, from the row.
3. The one place a fact's subject is decided, `_apply_beliefs_to_v2`.
4. The agent's memory tools, which run inside the turn and can read the scope.
5. The chat store's messages, for turn reconcile (S1-08: "messages name their
   author").

Reading:

1. `recall()` takes a question, a tenant and a session. It shows a fact as
   `user lives in Kuwait` (`recall._format_belief_text`) and a turn as
   `User: ... / Assistant: ...` (`episode_text.display_text`). In a shared
   space both lines would be read by another member as being about them.
2. `profile.about_for_turn(tenant)`: one "About me" row per tenant
   (`memory_profile`, key `tenant_id`), shown as "About the user". Changing
   it is admin-only (`platform_rbac`, Memory audit M11), because it is
   guidance for every user of the tenant.
3. `topic_summaries`: the model is shown `[when] User: ...` lines and told it
   writes "one week of a user's conversations".
4. The graph walk (`recall._belief_graph_ppr`) and keyword search read the
   stored subject strings. The Memory page draws `user` as the centre
   (`self_hub`, `graph_view`), and `entity_protection` protects `user` and
   the entities its aliases name.

Trust and forgetting:

1. `provenance.may_overrule` compares two source labels. `user_explicit` and
   `user_stated` mean "the user's own words"; with several members that is
   "some member's words".
2. The forget ledger (`memory_forgotten`: tenant, chat key, turn, question
   hash) has no actor, and needs none to be correct: a forgotten turn is
   forgotten for the whole tenant. Who asked is in the operator-action trail
   (`memory_actions`), from the session. No rule says who may ask.
3. Rewind (`forget.rewind_memory`) follows the chat: the turns taken back are
   forgotten whoever said them.

### 1.3 What already works per space

Every memory store is keyed by tenant, and a space's id is the tenant id.
Entities are keyed `(tenant, id)`, so each space has its own `user`. The
foundation binds space and asker for every turn. Nothing in this study
changes how spaces are kept apart; it is only about people inside one space.

## 2. The designs, and the one chosen

### 2.1 Candidates

| | Design | What it changes | Verdict |
|---|---|---|---|
| A | Keep the one `user` entry; add a speaker to each fact; work out whose fact it is when memory is read. | The key of a single-valued fact becomes subject, predicate **and speaker**: the supersede rule, the trust gate, the earlier-statement path, the same-words predicate rule, the "extra current rows" close, the graph copy and the mirror all read that key. | Rejected. It changes the key inside `belief_mutation`, the most hardened code in memory, and every reader that forgets the new part of the key shows one member another's fact. |
| B | **An entry per member, filed when the fact is written.** "I" becomes the speaker's own entry before `mutate_belief` is called. | One mapping in the writers. `belief_mutation`, the supersede rule, the trust gate, forget, the mirror and the graph copy are unchanged: to them a member's entry is one more subject. | **Chosen.** |
| C | A memory of its own for each member inside the space (a partition per member), read together with the space's. | Every memory query reads two or more tenants. | Rejected. Every statement in memory names one tenant; the plan's rule is one memory for the work (4.8), and a private memory is the personal space (N20). |
| D | Do not file first-person facts in a shared space at all; keep the turns, with their author, and let recall show them. | Nothing in the fact path. | Rejected. Facts are what give "I moved" its effect (the old address stops being current) and what the control run loses: 13 of 32 facts (section 4). |

Design B has two more choices.

**When "I" is resolved: at write time.** The deep pass, turn reconcile and
the queue run minutes or days after the turn, some in another process. A
fact filed under the speaker at write time is right forever; a fact that
waits for recall to decide needs the speaker at every later read.

**The deep pass is not told who speaks.** Its message stays exactly today's,
in every kind of space. The prompt keeps `Use "user" for the user
themselves`, and the writer maps `user` to the speaker's entry. A member
named in a message ("Omar", "عمر") is written by the model under that name,
and the alias match files it under the member's entry.

The study first proposed two more lines for a shared space, naming the
speaker and the other members, and then measured them (4.4). They gained
nothing on the speaker turns, where today's message already found every
fact; and on the extraction set they changed nothing that can be told apart
from how the model's answer varies from one asking to the next. They are
not adopted. S1-11 needs no second prompt and no second recording, and the
recorded replies stay byte for byte what they are today.

### 2.2 What the entry's id is

Two forms were measured (section 4): the slug of the member's name
(`sara`), and an id made from the account (`member_<account id>`) with
recall reading the member's name for it.

The chosen form is the account id. Reasons:

- An account's name can change, and two accounts could one day share a
  display name. A memory key must not move when a name does.
- A reserved shape cannot be the id of a concept, and the extractor can
  never write it: the sanitizer refuses a subject of that shape from a
  model.
- Code can tell a member's entry from its id alone, with no lookup: the
  writers treat it as they treat `user` today (never resolved against other
  entities, never anchored, always a node).
- Measured quality is the same either way, once recall reads the name for
  the id and the entry's vectors are made as below: the two forms give the
  same number in every row of section 4.

The member's name and the names they are called by (Latin and Arabic) are
the entry's display name and aliases. Recall, keyword search and the graph
walk read those; a statement by another member that names them resolves onto
the entry through the alias match that exists today. One limit no id form
removes: a third person who shares a member's name ("my friend Sara") is
read as the member. In a shared space that is the likelier meaning.

**The entry's vectors are made from `user <predicate> <object>`**, the text
a fact about `user` is embedded from today, not from the entry's id. This is
the one finding of the study that was not expected. Recall leans on that
text: with a member's facts embedded under their name or their id, a lone
member's scores on dataset v5 moved in 24 to 27 of the 78 gated scores. With
the vectors made from `user ...`, every one of the 78 is exactly the locked
value (4.3).

### 2.3 How a space with one member stays as it is

Which spaces file by member is decided by the **kind of space**, never by
counting members:

- A **personal space** has one member forever (decision N5). It keeps the
  `user` entry. The mapping returns `user`, so no line of the write or read
  path behaves differently, and nothing is migrated. Every space an install
  has today is one: the owner's `default` space stays private (N19).
- A **shared space** files by member from its first turn, including while
  it has one member. Nothing is ever re-filed when a second member joins.

Section 4.3 measures both halves. A personal space run through the
prototype gives exactly the locked scores. A lone member of a shared space,
filed under their own entry, also gets exactly the locked scores, provided
the entry's vectors are made as in 2.2. So the kind of space can decide, and
a lone member loses nothing by it. The owner decided it so (N30).

### 2.4 What the read side does

For a turn in a shared space, recall is told the asker.

- The asker's own entry is shown as `user`, as today. Another member's
  entry is shown under that member's name. A turn by another member is
  shown as `Omar: ... / Assistant: ...`; the asker's own stays `User: ...`.
- The recall block gains one line, outside the fence, only when the space
  has other members: who the model is talking to, and that facts under
  other names are about those members.
- A first-person question that names no other member is shown the asker's
  own facts and not the other members' ("self first"). A question that
  names a member is shown that member's facts and not the others' ("named
  first"). A question that does neither ("who is allergic to shellfish?")
  is shown everyone's. Facts that are about no member are never dropped.
  Both are measured in 4.2.
- Turns are never dropped this way: another member's turn may hold the
  answer ("Omar: Sara's flight is at nine").

## 3. The rules per member

A rule is what S1-11 builds. Where the owner decided, the row names the
decision (N30 to N36 in section 9 of the plan; section 6 here).

| Question | Answer |
|---|---|
| Whose words are `user_explicit` and `user_stated`? | **Rule.** The words of the member who is the turn's asker. For a fact under a member's own entry, only that member's own words carry those labels. |
| May one member state a fact about another? | **Rule (N34).** Yes, and it is kept as "said by Sara about Omar" under a new source label (`member_stated`), which never replaces Omar's own statements. Without this rule the trust gate lets it through: measured in 4.6. |
| Who may correct a fact that is not about a member (a project, a trip)? | **Rule.** Any member. Their own words are the user's words for the space; the newest statement wins, and the fact names who said it (`said_by`). |
| A member forgets their own turn. | **Rule.** As today: the turn, the facts it produced and its place in summaries go, for everyone in the space. The ledger needs no new column; the trail records the account. |
| A member forgets another member's turn, or keeps a shared chat out of memory. | **Rule (N32).** Only the turn's author and the space's owner may. Everyone can always forget their own turns. |
| A member forgets a fact about themself that someone else stated. | **Rule (N33).** They may always forget a fact filed under their own entry, and it is not learned again from that message. The other member's message stays, and Forget says so plainly: it still holds the words and can still be recalled, and its author or the space's owner can forget it. |
| Rewind (`/undo`, `/edit`, a restore) in a shared chat. | **Not this item.** It follows the chat, so S1-08 decides who may take back another member's turn; memory then forgets what was taken back, as today. |
| Weekly summaries. | **Rule.** In a shared space the turns are shown to the model with their author's name, and the prompt says the week is the members' conversations and to name who decided or asked for what. A personal space's prompt is unchanged. One summary per topic for the space, as today. |
| The text recall shows for a turn. | **Rule.** `User:` for the asker's own turn, the member's name for another's (2.4). |
| About me. | **Rule (N31).** One text per member per space, shown only in that member's own turns; each member edits their own. "Copy from my personal space" copies it once; the two texts are independent after that. |
| What recall shows the asker about other members. | **Rule (N35).** Everything in the shared space, under the member's name. A shared space has one memory (plan 4.8); what a member does not want shared belongs in their personal space. The space says so before anyone types something private: a notice, "Memory here is shared with Sara and Omar" (the members' names, English and Arabic), near the composer and on the Memory page. |
| A member leaves the space, or is removed. | **Rule (N36).** Every fact filed under the member's own entry is forgotten: what they said about themself, and what others said about them. Their messages in shared chats stay, as the conversation's history. Facts about shared things (a trip, a project) are not under their entry, so the space keeps them. The leave screen says this first, and a removal by the space's owner or the admin applies the same rule. S2-12 builds the flow; S1-11 provides the step (5.1). |
| Memory shared from a personal space into the workspace (N19, "memory by topic"). | **Rule.** A copied fact about `user` is filed under the sharer's entry in the workspace, with the sharer as `said_by`. |
| A first-person fact whose speaker is not known (an old message with no author, a turn replayed by an older build). | **Rule.** In a shared space it is not filed. The turn is still remembered. Nothing is ever filed under a guessed member. |
| The `user` entry in a shared space. | **Rule.** It stays as the space's own centre for the graph and for the anchors (`user related_to <subject>`), named after the space. No first-person fact is ever filed under it. |

## 4. The measurement

### 4.1 What was run

Everything is in `docs/audits/evidence/spaces_r3/`: the case set
(`speakers.json`), the prototype and runner (`study.py`), the vectors it
recorded (`vectors.npz`), the model's replies it recorded
(`deep_replies.json`) and its output (`results.json`). `study.py run`
repeats every number here from the recorded vectors and replies, with no
model.

- **The case set**, version 1: three members of one shared space (Sara,
  Omar, Layla), 30 turns in 29 chats (one chat has two authors), 32 facts,
  35 questions: 17 English, 10 Arabic, 8 mixed. By kind: 23 about the asker
  ("where do I live?"), 3 about the asker where the asker never said
  ("what am I allergic to?", asked by the one member who stated no
  allergy), 6 about another member by name, 3 about the space. It is scored
  apart. Dataset v5, its vectors and `thresholds.json` are untouched.
- **The method** is the memory benchmark's: a private database, turns
  written through `dual_write.episode_row`, facts through
  `mutate_belief(private=True)` at the time they were said, every question
  through the real `recall()`.
- **The variants** differ only in where a fact about a member is filed and
  how it is shown:
  - `control`: today. Every "I" is `user`; a statement about another member
    is filed under the name it was written with.
  - `name`, `id`: the member's own entry, with the name's slug or the
    account's id as its id. `id_names`: `id`, with recall reading the
    member's name for the id.
  - `name_uv`, `id_uv`: the same, with the entry's vectors made from
    `user <predicate> <object>`.
  - Shown `label` (names only), `self_first`, `named_first` (2.4).
- **Scores**: `hit`, `mrr` and `precision` as the benchmark defines them;
  `fact hit` (the answering fact itself was shown, not only its turn);
  `misattributed` (a fact about another member, or a turn by another
  member, shown to the asker as `user` / `User:`); `wrong first` (the first
  fact shown is another member's, shown as the asker's); `lost` (a fact its
  member never took back that is no longer current).
- **The model**: 241 new texts were embedded with bge-m3 on this PC, which
  also runs the live install, in two runs (239, then 2), at below-normal
  CPU priority, four threads at most, normal I/O priority, from the
  machine's model cache with the network off, in a data folder of the
  study's own. Nothing of the live install was read or written for it.
- **The deep pass** was recorded on 2026-11-18, with the owner's approval,
  through the live install's DeepSeek provider: 60 calls to
  `deepseek-flash`, the limit set for it (50,954 prompt tokens and 13,550
  completion tokens; the cost is unknown, because Kazma holds no price for
  that model). The live install was only read: its `.env` in memory, its
  provider list by two SELECTs in a Postgres session the server held
  read-only, its key from the vault file opened immutable. Nothing was
  written to it, and the replies hold no key.

### 4.2 Several members in one space

Overall, 35 questions:

| | hit | fact hit | mrr | precision | misattributed | wrong first | facts lost |
|---|---|---|---|---|---|---|---|
| `control` (today) | 0.886 | 0.581 | 0.630 | 0.446 | 84 items in 33 questions | 14 | 13 of 32 |
| `id_uv`, `label` | 1.000 | 0.935 | 0.715 | 0.431 | 0 | 0 | 0 |
| `id_uv`, `self_first` | 1.000 | 0.935 | 0.911 | 0.564 | 0 | 0 | 0 |
| **`id_uv`, `named_first` (chosen)** | **1.000** | **0.935** | **0.964** | **0.592** | **0** | **0** | **0** |
| `name_uv`, `named_first` | 1.000 | 0.935 | 0.964 | 0.592 | 0 | 0 | 0 |
| `name`, `named_first` | 1.000 | 0.968 | 0.969 | 0.587 | 0 | 0 | 0 |
| `id_names`, `named_first` | 1.000 | 0.968 | 0.969 | 0.578 | 0 | 0 | 0 |
| `id`, `label` (the raw id) | 1.000 | 0.968 | 0.677 | 0.430 | 0 | 0 | 0 |

By language, today against the chosen design:

| | n | hit | fact hit | mrr | precision | misattributed |
|---|---|---|---|---|---|---|
| English, `control` | 17 | 0.882 | 0.667 | 0.565 | 0.428 | 46 |
| English, chosen | 17 | 1.000 | 1.000 | 1.000 | 0.604 | 0 |
| Arabic, `control` | 10 | 0.900 | 0.444 | 0.670 | 0.428 | 22 |
| Arabic, chosen | 10 | 1.000 | 0.889 | 0.926 | 0.532 | 0 |
| Mixed, `control` | 8 | 0.875 | 0.571 | 0.726 | 0.512 | 16 |
| Mixed, chosen | 8 | 1.000 | 0.857 | 0.929 | 0.643 | 0 |

**Re-run on 2026-11-21.** Recall now judges a small store's turns as a
full store would (`recall._question_background`, the small-store rule of
the memory framework audit's part H), and the study was run again with it
(`results.json`). Only these runs moved; every other number in this report
is unchanged:

- The chosen design (`id_uv`, `named_first`, and `name_uv` with it):
  precision 0.592 to 0.623 (English 0.604 to 0.608, Arabic 0.532 to 0.598,
  Mixed 0.643 to 0.690; another member by name 0.539 to 0.667). Hit, fact
  hit and mrr are the same.
- Every other design's precision rose too: `self_first` 0.564 to 0.579,
  `name` 0.587 to 0.601, `id_names` 0.578 to 0.609, `label` 0.431 to
  0.443, the raw id 0.430 to 0.442.
- `control` (today's single entry): hit 0.886 to 0.857, mrr 0.630 to
  0.624, precision 0.446 to 0.453, misattributed 84 items to 80; Arabic hit
  0.900 to 0.800; another member by name, hit 0.833 to 0.667 and mrr 0.617
  to 0.583. With every member filed as one `user`, the answering turn is
  one more matching memory in a tiny store, and the raised background left
  one of them out. The design the study chose does not file that way.

**Re-run on 2026-11-22.** Two changes of the memory plan's finding 10
moved the study (`results.json`). The chosen design's numbers do not move.

- The Arabic words for "now" (الحين, الآن ...) are no longer content words,
  as English "now" is not (5fc4150b7). Layla's "شنو سيارتي الحين؟" (q22)
  matched her Tesla turn ("بعت المازدا، والحين سيارتي تسلا موديل ٣") by
  "والحين", and no longer does, so Omar's "سيارتي نيسان باترول" ranks above it
  among the turns. In the chosen design and every `id` design, her own fact
  `user car تسلا موديل ٣` is still shown first and q22's rr stays 1.0: only
  the two turns below it swap, Omar's under his name. In the `name`
  designs, which do not show the fact for q22, her Tesla turn goes from
  first to second behind Omar's: `name`, `named_first` mrr 0.969 to 0.953
  (Arabic 0.944 to 0.889); `name`, `self_first` 0.964 to 0.948 (Arabic
  0.926 to 0.870); `name`, `label` 0.730 to 0.728 (Arabic 0.667 to 0.657).
  Her old car (the Mazda) stays last in every design. Not a loss for the
  asker: the match was an accident of the turn's wording, and the change
  is what lets "وين أسكن الحين؟" find the user's home.
- "We moved to Rotterdam last month." (extraction turn e06) now gives
  `lives_in Rotterdam` in the quick pass, the fact its gold names
  (11655e4df): quick-pass facts 19 to 20 (section 4.4), English recall
  0.4615 to 0.5385 in the untagged and tagged runs.

By kind of question, chosen design (today in brackets): about the asker,
fact hit 0.957 (0.522) and mrr 0.971 (0.621); the asker never said, 3 of 3
show nothing as the asker's (0 of 3); another member by name, hit 1.000
(0.833) and mrr 0.917 (0.617); the space, mrr 1.000 (0.722).

What today's single entry does, in the run's own lines:

- Sara asks "Where do I live?" and is shown `user lives in Lusail` first.
  Lusail is where Omar moved. Sara's own "Kuwait City" is no longer a
  current fact: Omar's and Layla's statements replaced it, each in turn.
- Layla, who stated no allergy, asks "What am I allergic to?" and is shown
  `user allergic to shellfish` and `user allergic to peanuts`.
- 13 of the 32 facts are gone, every one overwritten by another member's
  statement of the same kind.
- Sara's guess "I think Omar lives in Dubai" is kept as a second entry,
  `omar`, and "Where does Omar live?" is answered from it.

What the chosen design does with the same turns: Sara is shown
`user lives in Kuwait City`, then the other members' turns under their
names. Nothing is lost, nothing is shown as the asker's that is not, and
Sara's guess about Omar is refused by the trust gate that exists today,
because Omar's own statement is under the same entry.

What it does not do: in 2 of the 31 questions that a fact answers, the fact
itself was not among those shown and the answering turn was (so the
question is still a hit). Both are Arabic or mixed.

### 4.3 One member

Dataset v5 (123 questions, 78 gated scores), with its one persona filed
each way:

| The persona's facts filed under | Gated scores equal to the locked ones | overall hit | overall mrr | overall precision |
|---|---|---|---|---|
| `user` (a personal space; today) | 78 of 78 | 0.9253 | 0.8444 | 0.6443 |
| their name, vectors from the name | 51 of 78 | 0.9038 | 0.8138 | 0.6443 |
| the account id, vectors from the id | 54 of 78 | 0.9139 | 0.8185 | 0.6392 |
| **their name or the account id, vectors from `user ...`** | **78 of 78** | **0.9253** | **0.8444** | **0.6443** |

- A personal space takes the first row by construction: the mapping gives
  `user` and nothing else differs. The run confirms the study's harness
  gives the locked scores.
- A lone member of a shared space, filed under their own entry, gets
  exactly the locked scores when the entry's vectors are made from
  `user <predicate> <object>`. Embedded under the name or the id instead,
  scores move both ways: for the name, held-out precision rose by 0.014
  while overall hit fell by 0.022 and Arabic hit by 0.125 (one question of
  eight).
- The price of `user ...` vectors is in questions about another member by
  name: fact hit 0.833 against 1.000 (one question of six). The "named
  first" rule brings their mrr to 0.917 and precision to 0.539.

### 4.4 Extraction with a speaker on every turn

The 30 turns of the extraction benchmark, each tagged with one of the three
members in turn, `user` mapped to the speaker's entry:

| Pass | Facts kept | About the speaker | Scores against the locked ones |
|---|---|---|---|
| Quick pass | 20 (19 before 2026-11-22: section 4.2's note) | 20, all filed under the speaker | 9 of 9 equal |
| Deep pass, the existing recording (deepseek-flash) | 37 | 31 under the speaker; 6 about someone else stay where they were | 9 of 9 equal |

Tagging a turn with its speaker changes no extraction score, because the
mapping is one to one.

Twelve new speaker turns (5 English, 4 Arabic, 3 mixed), quick pass only:
recall 0.60 English, 0.60 Arabic, 0.75 mixed; precision 1.00 in each; no
fact was filed under the wrong member. In the six turns that are about
someone else ("Omar lives in Lusail now.", "أخوي منصور ساكن في دبي.",
"We live in Muscat.") the quick pass kept nothing, which is right: it reads
only "I". Those statements are the deep pass's to keep.

**The deep pass on the same twelve turns**, with today's message:

| | gold facts | found | recall | kept | right | precision |
|---|---|---|---|---|---|---|
| English | 5 | 5 | 1.00 | 7 | 5 | 0.71 |
| Arabic | 5 | 5 | 1.00 | 6 | 5 | 0.83 |
| Mixed | 4 | 4 | 1.00 | 4 | 4 | 1.00 |

- It keeps what the quick pass cannot: "Omar lives in Lusail now." is
  written `omar lives_in lusail`, "عمر ساكن في لوسيل." is written under
  `عمر`, and both are filed under the member Omar by the alias match.
- No fact about someone else was filed under the speaker.
- The three facts beyond the gold are two relations
  (`sara spouse_is omar`, `omar has_brother منصور`) and "We live in
  Muscat.", which the model writes as `user lives_in Muscat`. Filed under
  the speaker, that is a fair reading: the speaker is one of "we".

**The deep pass told who speaks** (the two lines of 2.1, since dropped):

| | Result |
|---|---|
| The twelve speaker turns | The same as without the lines in every number: recall 1.00, precision 0.71 / 0.83 / 1.00, nothing filed under the wrong member. |
| The 30 turns of the extraction set, against the locked scores | English recall 0.769 to 0.692, precision 0.786 to 0.714. Arabic recall 0.909 to 0.636, precision 0.909 to 0.636. Mixed recall 0.70 to 0.80, precision 0.583 to 0.80. Six turns of 30 changed. |
| What the model wrote | `user` for 29 of its 35 facts, and never the speaker's name as a subject. |

Those six turns were then asked once more with today's message, no lines,
to see how much one and the same message varies:

| Turn | Gold | First asking (the locked recording) | Asked again, same message | With the lines |
|---|---|---|---|---|
| e07 | 2 | 2 | 2 | 1 |
| a04 | 1 | 1 | 0 | 0 |
| a06 | 2 | 2 | 0 | 0 |
| m05 | 2 | 1 | 2 | 2 |
| m07 | 1 | 1 | 1 | 1 |
| m08 | 1 | 1 | 1 | 1 |

Three of the four turns whose score moved with the lines moved the same
way with no lines at all: the model wrote `has_allergy` where it had
written `allergic_to`, and a relation from the sister's side where it had
written it from the user's. Only e07 moved with the lines alone. So the
lines' effect on the extraction set cannot be told apart from the model's
own variation, and they bring no gain where a gain was expected. They are
dropped (2.1).

One thing the lines did show: named the speaker, the model sometimes
writes a relation toward them, as `omar spouse_of user` or
`منصور sibling_of عمر`. It can do so with today's message too. So in a
shared space `user` as a fact's object is the speaker's entry, as it is as
a subject (5.1).

### 4.5 What was not measured

- **How much the deep pass varies.** Each message was asked once, six of
  them twice. One and the same message gave a different score on three of
  those six (4.4). A difference of one or two facts between two askings is
  therefore not a finding, and the locked extraction scores rest on one
  asking a turn too. That is a matter for the extraction benchmark, not
  for S1-11.
- **Weekly summaries** with authors' names: a model writes them.
- **The first-person reading** behind "self first" was right on 35 of 35
  questions, but it was written while looking at these questions. That is
  not evidence. S1-11 needs held-out questions, Gulf Arabic above all,
  where "I" is a prefix or a suffix and not a word.
- **Scale**: three members, 35 questions. One question is 3 points.
- The prototype applies "self first" and "named first" after recall's cut
  at five facts; S1-11 applies them before it.
- Recall with Postgres as the primary store.

### 4.6 The correction rule, through the real trust gate

Omar says "I live in Lusail" (`user_stated`). Sara then says "remember that
Omar lives in Dubai", which today's rule labels `user_explicit`: the user
asked, and the message holds the words. With a member's entry and today's
labels, the real `mutate_belief` **replaces Omar's own statement**: the
current fact becomes `omar lives_in Dubai (user_explicit)`.

So filing by member is not enough by itself. The label has to say whose
words they are (section 3): a statement about a member by someone else is
`member_stated`, which the gate that exists today already refuses to put
over that member's own words.

## 5. What S1-11 should be

### 5.1 The change

One new module is the only place that knows about members:
`kazma_core/memory/speakers.py`. It answers, for a space: is it shared; who
are its members and what are they called (from the identity store, read off
the loop); what is a member's entry id; is this subject a member's entry;
how is an entry shown to this asker. Everything else asks it.

| File | Change |
|---|---|
| `agent/graph_respond.py` | The memory record takes the asker from the bound scope. |
| `memory/consolidator.py` | The speaker travels with session, turn, tenant and time: the thread, the queued payload, the episode writer, the quick pass. |
| `memory/dual_write.py` | The episode's metadata keeps `said_by` (the account id). No column is added; the mirror already carries the metadata. |
| `memory/belief_extractor.py` | `_apply_beliefs_to_v2` maps `user` to the speaker's entry, in one line, and treats a member's entry as it treats `user`. The sanitizer refuses a model's subject of the reserved shape. The deep pass's message is unchanged (2.1). `user` written as a fact's object (`omar spouse_of user`) is the speaker's entry too. |
| `memory/belief_mutation.py` | `mutate_belief` takes `said_by` and keeps it in the fact's metadata. Its keys and rules do not change. |
| `memory/provenance.py` | The label `member_stated`, and one function that gives a write its label from the method, the subject's owner and the speaker. `_trust_weight` learns the label. |
| `memory/worker_bootstrap.py`, `turn_reconcile.py` | The deep pass reads the speaker from the episode; reconcile reads the message's author (S1-08). No author in a shared space: the turn is remembered, no first-person fact is filed. |
| `memory/recall.py`, `episode_text.py` | `recall(asker=...)`; the shown lines (2.4); the one line in the block; "self first"; the name read for an entry's id in ranking, keyword search and the walk's seeds. |
| `memory/belief_mutation.py`, `reembed.py` | One function for the text a fact's vector is made from (three copies today). A member's entry is embedded as `user <predicate> <object>` (2.2, 4.3). |
| `memory/self_hub.py`, `entity_protection.py`, `ego_anchor.py` | Members' entries are made when first needed, named from the account, and protected like `user`. Anchors are unchanged. |
| `agent/tool_builtins/memory.py` | `memory_store` and `memory_link_entities` file under the asker; `memory_search` passes the asker. |
| `memory/topic_summaries.py` | Authors' names in the turn lines and the shared-space prompt. |
| `memory/profile.py`, the schema, the Memory page | About me keyed by tenant and account; each member edits their own; "Copy from my personal space", which copies once (N31). |
| `memory/forget.py`, `kazma_ui/memory_api.py` | Who may forget what (N32), asked in one place. The wording of N33. One new step, **forget every fact under an entry**: each fact through the existing forget path (journaled first; the ledger, the mirror, the graph copy and the vectors follow), then the entry itself. The leave and remove flow (S2-12) and account deletion (S1-25) call it (N36). |
| The chat page, the Memory page, the catalog | The notice of N35, "Memory here is shared with ...", near the composer and on the Memory page, in English and Arabic. |
| `agent/graph_supervisor.py` | Passes the asker to recall and to About me. |

Depends on: S1-08 (messages name their author), S1-09 (the space is the
memory tenant), and an account having a display name and the names it is
called by (S1-33's account page, or a field added here).

### 5.2 Its gates

- **Behaviour**, `tests/test_memory_speakers.py` (the plan's gate): two
  members state different facts about themselves in one space, through the
  real graph and the real post-turn pipeline; each is recalled for the right
  one, in English, Arabic and mixed. **Negative control:** the same turns
  with the speaker left out are refused in a shared space; with the mapping
  removed, one `user` entry overwrites (the control of section 4).
- **Class gate, writers:** every function that writes a fact or an episode
  from a turn passes the speaker, found from the source the way
  `test_every_late_writer_passes_the_turn_time` finds the turn's time. A
  planted writer without it fails.
- **Class gate, readers:** in a shared space no line recall, the memory
  tools or a summary show attributes another member's fact or turn to the
  asker. It runs every question of the multi-speaker set as every member.
- **One-member gate:** a personal space run through the new code gives the
  locked v5 scores exactly, and the stored rows are byte for byte the same
  (no `said_by`, subject `user`).
- **The benchmark:** the multi-speaker set becomes
  `benchmark_data/speakers.json` with its own ratchet in `thresholds.json`
  under its own key. Dataset v5 and its thresholds are untouched.
- **Rules:** `member_stated` never replaces a member's own statement (4.6,
  with today's label as the negative control); a first-person fact with no
  speaker is not filed.
- **Forget by entry (N36):** after the step, no fact under the entry is
  recalled by any member, in the store, the mirror or the graph copy; the
  member's messages are still recalled; facts about shared things stay; a
  restore of an older backup does not bring the facts back (the forget
  journal). **Negative control:** the entry left in place.
- **Who may forget (N32, N33):** another member's turn is refused to a
  member who is neither its author nor the space's owner; forgetting a fact
  about oneself leaves the other member's message, and the answer says so.
- **The notice (N35):** a browser test, English and Arabic, reached by the
  sidebar: the names shown are the space's members; a personal space shows
  none.
- **Held-out questions.** The questions that judge "self first" and "named
  first" (Gulf Arabic above all, where "I" is a prefix or a suffix) are
  written by a different session than the one that builds S1-11, before the
  builder sees them. The builder's own questions tune; these decide.

### 5.3 What it costs

- About fifteen product files, most of them one parameter passed along.
  The real work is in three: the mapping and its rules (`speakers.py`,
  `belief_extractor.py`, `provenance.py`), recall's shown lines and "self
  first", and the vector text.
- No change to the keys or rules of `belief_mutation`, to the forget
  ledger, the mirror or the graph copy. One schema change: About me's key
  (N31).
- No new prompt and no new recording of the deep pass: its message is
  unchanged (2.1, 4.4).
- New vectors for the promoted set are already recorded here.
- Not in S1-11: who may rewind a shared chat (S1-08), member names on the
  account (S1-33), sharing memory by topic (N19).

## 6. The owner's decisions

Answered on 2026-11-18. They are recorded as **N30 to N36** in section 9 of
`docs/plans/SPACES_PLAN.md`, which is where to read them. The names D1 to D7
were this study's own and are not used elsewhere: older decisions already
carry those names.

| Here | Plan | Question | Proposed by the study | Decided |
|---|---|---|---|---|
| D1 | N30 | Which spaces file facts by member? | Every shared space, from its first turn. A personal space never does. | As proposed. |
| D2 | N31 | About me in a shared space. | One text per member per space, shown only in that member's turns; each member edits their own. | As proposed, plus "Copy from my personal space". It copies once; the two texts are independent after that. |
| D3 | N32 | Who may forget another member's turn, or keep a shared chat out of memory? | The turn's author and the space's owner. | As proposed. Everyone can always forget their own turns. |
| D4 | N33 | A member forgets a fact about themself that another member stated. Does the other member's message go too? | The fact goes and is not learned again from it. The message stays. | As proposed, plus honest wording: Forget says the other member's message still holds the words and can still be recalled, and that its author or the space's owner can forget it. |
| D5 | N34 | May a member state facts about another member? | Yes, kept as "said by", never replacing what the member said about themself. | As proposed. |
| D6 | N35 | What does recall show a member about the others? | Everything in the shared space, under the member's name. | As proposed, plus a visible notice, "Memory here is shared with Sara and Omar", near the composer and on the Memory page. |
| D7 | N36 | A member leaves a shared space, or is removed. | Their entry and facts stay. | **Changed.** Every fact under the member's own entry is forgotten, whoever said it. Their messages stay. Facts about shared things stay. The leave screen says so first; a removal applies the same rule. |
