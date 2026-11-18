# Audit: the whole memory framework, and the way to the best memory an agent has

Date: 2026-11-18. Branch: `claude/extraction-bench-stable-d2d77e`. Not pushed.
Audit only: no product code, benchmark code, fixture or lock was changed, and
no model was called.

This report covers everything under `kazma-core/kazma_core/memory/` and
everything that writes to it or reads from it. It folds in the benchmark audit
of the same branch (`docs/audits/AUDIT_MEMORY_BENCHMARKS_2026-11.md`, commit
`8a5e7fa88`) as its measurement part. It also checks the Spaces plan
(`docs/plans/SPACES_PLAN.md` at `2ceb89b7f`) against the memory code.

How to read the evidence words:

- **Measured**: I ran it through the product's own code. The script and its
  output are in `docs/audits/evidence/memory_framework_2026_11/` (`probes.py`,
  `probes.json`, `probes_real_embedder.json`) or in the benchmark audit's
  evidence folder. Section 8 says what was measured when.
- **Read**: I read the lines named.
- **Reported**: a helper agent read the lines named and I did not read them
  again. Treat these as leads to confirm before building on them.
- **Not measured**: said plainly, with why.

Outside claims (other systems, benchmarks, attacks) come from public papers
and docs, listed in section 10. Most scores there are self-reported by the
vendor and not reproduced by anyone else; section 3.3 says which.


## 1. Executive summary

**Where Kazma stands.** Kazma's memory is strongest where the field is
weakest. No open memory system and no chat product documents a forget that
follows a memory into its summaries, lessons, vectors, mirror, graph copy and
restores; Kazma does, with a test that plants a word through every writer. No
open system has any defence against memory poisoning; Kazma has a trust
ladder, screening and a fenced recall block. No leader exists on "say nothing
when nothing is known"; Kazma is built to abstain and its benchmark gates it.
No Arabic long-term-memory benchmark exists anywhere; Kazma already treats
Arabic and English as equals in the code. And its operations (restore drills,
crash matrices, soaks, converging copies) are a level no memory library
documents.

**Where it falls short.** The systems that lead the public benchmarks lead on
two abilities: knowing *when* something was true, and handling a fact that
*changed*. On exactly these two Kazma is weakest today, and I measured both:

1. **A user's own correction is accepted or refused by chance.** "I live in
   Kuwait City", and later "We moved to Rotterdam last month": Kazma keeps
   Kuwait City and refuses Rotterdam. Had the model happened to write the
   first city in lower case, the correction would have landed. On the
   benchmark's own turns the model changes the user's spelling in 5 of 15
   facts, and in doing so quietly strips the user's statement of its trust.
2. **Memory has no sense of time when it speaks.** The dates are stored. None
   reaches the model: no date on a fact, none on a remembered turn, and a
   fact's earlier value is never shown. Asked "when did I move to London?",
   the model is shown "We moved to London last month" with no date.
3. **Text the user only quoted or attached is stored as the user's own
   statement, at the user's own trust.** "Translate to French: My name is
   Pierre and I live in Lyon" stores the name Pierre. So does a forwarded
   PDF.
4. **One fact said twice under two names stays two current facts.** The
   model names the same statement differently one time in three.
5. **Forgetting leaves more than it says.** A forgotten fact keeps its
   subject and its predicate ("sara, diagnosed_with") in the database and in
   the search index. "Forget everything that mentions X" leaves X in the
   fact's own row.

Beyond those: Kazma has never been run on a public benchmark, so "best"
cannot be claimed or even located today; its own benchmark has the weak spots
the benchmark audit found; recall weakens on the topics a person talks about
most; and what is kept beyond turns and facts (lessons, procedures,
long-range summaries, a profile) is thin next to what the leaders build.

**What "best" would take.** Not a new architecture. Kazma's design choices
(keep every turn whole, facts with two clocks, exact search, no model in the
recall path, forget as a first-class operation) are the ones the research now
favours. The way to best is: fix the five measured faults; make time and
change first-class in what the model is shown; then prove it on LongMemEval
and LoCoMo beside the named baselines, and publish the first Arabic memory
benchmark. Section 4 gives the order, with a gate, a negative control and a
cost for each step. My estimate is about 125 builder-days for all of it. The
first ten weeks (measure honestly, say nothing wrong, forget means gone)
carry most of the gain and all five measured faults.

**Spaces.** The Spaces plan is right about memory's shape and incomplete
about its detail. Section 5 lists what it must add. Eleven items must land
**before S1-09** is built, because S1-09 makes the space the memory tenant
and several memory paths today either take the tenant from the retired mode,
run with no tenant at all, or read every tenant when asked as `default`.
Seven more must land **before S1-11**. Memory "shared by topic" (N19) is
decided by the owner and owned by no plan item.

**Decisions.** Section 6 lists every decision for the owner with my proposal:
the six benchmark decisions already agreed with the main session, and
thirteen new ones.


## 2. Findings, ranked by impact

Ranked by how much each can make Kazma tell its user something wrong, keep
what the user took back, or miss what the user said. F1 to F8 are measured.

### F1. A user's correction is accepted or refused by chance

**Measured** (probes 5, 8, 10).

What happens. Each turn goes through two passes. The quick pass reads a few
fixed sentence forms ("I live in X") and files the fact as the user's own
statement (`user_stated`). The deep pass asks the model and files what it
returns as an inference (`llm_inferred`). The trust gate
(`memory/provenance.py:64-71`) lets an inference replace the user's own
statement only inside the same turn.

So a correction the quick pass cannot read is an inference, and an inference
may not replace the user's statement:

| First turn | Later turn | Result |
|---|---|---|
| "I live in Paris." | "We moved to Rotterdam last month." (the benchmark's turn e06, the model's own recorded reply `lives_in rotterdam`) | Refused: `lower_trust_source`. Paris stays the current fact. |
| "I work at Google." | "I left Google, I am at Meta now." | Refused. Google stays. |
| "I live in Paris." | "I live in Rotterdam now." (a form the quick pass reads) | Accepted. |

And the deep pass of the *first* turn decides which of the two happens. In
the same turn the deep pass may replace the quick pass's fact. It does so
whenever the model wrote the value in other letters, because the "is it the
same value" test compares letter for letter (`belief_mutation.py`, the
`old_obj == obj` test):

| First turn (recorded reply) | After the first turn | "We moved to Rotterdam last month." |
|---|---|---|
| e01 "My name is Layla and I live in Lisbon now." The model wrote `lisbon`. | Home is `lisbon`, now an inference. The user's "Lisbon" is closed. | Accepted. |
| m01 "My name is Omar وساكن في Kuwait City." The model wrote `Kuwait City`. | Home is `Kuwait City`, still the user's statement. | **Refused.** Kuwait City stays for good. |

Over the benchmark's 30 turns, both passes read the same fact 15 times. The
model wrote it in the same letters 9 times, in other letters 5 times (Layla
to `layla`, Lisbon to `lisbon`, Kyoto to `kyoto`, Gulf Bank to `gulf_bank`,
Abu Faisal to `abu_faisal`) and as another value once.

Three faults in one:

1. The user's own later words lose to the user's own earlier words.
2. The user's statement loses its trust minutes after it is said, whenever
   the model respells it. From then on any inference, including one drawn
   from the assistant's summary of a web page, may replace it. The ladder's
   promise ("a fact the user stated yields only to the user's own words")
   does not hold for those facts.
3. The user's spelling is replaced by a slug. The Memory page and recall
   then say "user name is layla".

The extraction benchmark cannot see any of this: it scores each turn alone,
against an empty memory. No turn in it updates a fact.

What the best do. Zep (Graphiti) compares each new fact with the existing
ones between the same two entities and, on a contradiction, closes the old
one at the new one's start time. Mastra and Hindsight report 96.2 and 94.9 on
LongMemEval's knowledge-update questions. Mem0's current version does the
opposite (it only adds, so old and new coexist) and scores 35.7 on its own
contradiction test.

Fix. Trust follows *whose words hold the value*, not which pass read it:

- a deep-pass fact whose value is in the user's own words of that turn is
  `user_stated` (the 80 % held-words rule `provenance._holds` already exists
  for "remember" requests);
- a deep-pass fact whose value is only in the assistant's text stays
  `llm_inferred`;
- "the same value" is compared after folding case, underscores and Arabic
  forms, and the user's own spelling is the one kept.

This fix depends on F3's helper ("the user's own words", with quoted and
attached text removed). Without it, raising the deep pass's trust widens F3.

Gate: the three tables above as tests through the worker's own path, plus an
update category in the extraction benchmark (state, then correct in free
words, in English, Arabic and mixed; expect exactly one current fact, the
new one, at the user's trust). Negative control: today's grading refuses the
correction. Cost: 3 to 4 days, after F3.

### F2. Memory has no sense of time when it speaks

**Measured** (probe 4, with the real embedder and with words only).

Kazma stores two clocks on every fact (when it was true, when Kazma learned
it) and the time of every turn. Almost none of it reaches the model:

- The recall block has no date on any fact or turn
  (`recall.format_recall_block`).
- Only current facts are recalled. A fact's earlier value is kept and never
  shown.
- The extractor is given the turn's text and nothing else
  (`belief_extractor.py:240`). It is not told the date, so "last month" and
  "the 3rd of May" are stored as text or dropped. The recorded reply for
  "We moved to Rotterdam last month" is `lives_in rotterdam` with no time.
- A question's time words ("last week", "before", "in March") are treated as
  ordinary words to match.

The probe stored "I live in Paris" (January) and "We moved to London last
month" (late May), with the facts as the product writes them (Paris from
2026-01-09 to 2026-05-31, London since). What the model was shown:

| Question | Shown |
|---|---|
| "Where do I live?" | Fact: "user lives in London". History: "User: I live in Paris, near the Canal Saint-Martin." Neither has a date. |
| "When did I move to London?" | "User: We moved to London last month". No date, so "last month" cannot be resolved. |
| "Where did I live before London?" | The only fact shown is London. With the real embedder the Paris turn appears, marked "(possibly related)"; on words alone no turn appears. |

What the best do. Zep resolves relative dates against each message's own
timestamp when it extracts, and shows facts with their date ranges. Hindsight
stores when a thing happened and when it was mentioned, and has a time
channel in recall. The LongMemEval paper measured +11.3 % temporal recall
from expanding a question's time words into a date range. On LongMemEval's
temporal questions the reported scores are Mastra 95.5, Hindsight 91.0, Zep
62.4.

Fix, in three steps of rising cost:

1. Show the dates Kazma already has. Each remembered turn gets its date
   ("12 Jan 2026"). Each fact gets "since May 2026". A single-valued fact
   with history gets one more line: "before that: Paris, Jan to May 2026".
   No model, no new storage.
2. Give the extractor the turn's date and ask for the absolute time a
   statement names. "Last month" said on 31 May becomes a start in April.
3. Read time words in the question. A date range filters and ranks by the
   turn's time and the fact's validity. "Before", "used to" and "at the time"
   ask for the history rows.

Gate: a temporal category in the recall benchmark ("when did", "before X",
"in March", "what changed"), which fails today, and LongMemEval's temporal
questions (decision 7). Negative control: the block without dates. Cost:
step 1 two days; step 2 four days plus a fresh recording (it changes the
prompt, see decision 2); step 3 one week.

### F3. Text the user quoted or attached is stored as the user's own statement

**Measured** (probe 7) for the quick pass. **Read** for the attachment path
and the "remember" path.

The English quick pass runs its patterns over the whole message:

| Message | Stored, at the user's own trust |
|---|---|
| "Translate to French: My name is Pierre and I live in Lyon." | `user name_is Pierre`, `user lives_in Lyon` |
| "Fix the grammar: I work at Initech and I love Mondays." | `user works_at Initech`, `user prefers Mondays` |
| "Summarise this email: Hi, my name is Dana and I work at Acme Corp." | `user name_is Dana`, `user works_at Acme Corp` |
| "I need to finish this report by Friday." | `user prefers to finish this` |
| "Please file this." with an attached letter saying "my name is Sara Lee and I work at Acme Corp" | `user name_is Sara Lee`, `user works_at Acme Corp` |

The last row matters most. A chat app puts an attachment's text into the
user's message, inside a data fence
(`kazma-gateway/kazma_gateway/agent_handler/attachments.py:395-401`). The
quick pass does not strip fences. The "remember" check judges the whole
message too (`provenance.py:107`), so a memory tool that stores a line of the
attachment can earn `user_explicit`, the highest trust there is. No memory
module calls `prompt_fence.outside_fences`.

This is memory poisoning through the front door: a forwarded document can
change the user's name, and with F1 the user then cannot correct it in free
words.

What the best do. Nothing: none of the open memory systems defends against
poisoning at all, and the published attacks succeed against them (MINJA:
98.2 % injection success with ordinary queries; SpAIware planted a ChatGPT
memory from a web page). Kazma is ahead in design. This hole is in the
design's one weak place.

Fix. One helper, "the user's own words": the message with data fences,
attachment blocks and quoted blocks removed, and with the body of an
instruction form ("translate:", "fix:", "summarise this:", and the Arabic
forms) treated as quoted. The quick pass, the "remember" detector,
`provenance._holds` and F1's new grading all read through it.

Gate: every quick-pass pattern planted inside a real `build_user_content`
attachment message and inside each instruction form stores no `user_*` fact,
in English and Arabic. Negative control: today's raw message. Cost: 3 days.

### F4. One fact under two names stays two current facts

**Measured** (probes 1 and 2; and the benchmark audit: the model gave the
same statement the same name in 6 of 9 re-asked turns).

The model chooses each fact's predicate name freely. Kazma merges two names
only when they are made of the same words (`predicates._predicate_key`: of
nine real pairs from the recordings, it merges one, `occupation` and
`occupation_is`). So:

| First | Then | Current facts afterwards |
|---|---|---|
| `hana occupation pilot` | `hana works_as captain` | both |
| `user favorite_drink tea` | `user favorite_morning_drink coffee` | both |
| `user allergic_to peanuts` | `user has_allergy peanuts` | both (the same fact twice) |

A person whose job changed is, to Kazma, a pilot and a captain. Both are
shown under "Known Facts". Kazma is right not to merge by meaning alone (its
own notes record two different accounts at similarity 0.94).

What the best do. Zep asks a model, per new fact, whether it duplicates or
contradicts the existing facts between the same two entities. Mem0's 2025
version asked a model to choose add, update, delete or nothing against the
ten most similar memories.

Fix. Two layers:

1. A fixed table of names for the common personal facts (home, employer,
   job, name, languages, diet, allergies, family, and so on, about forty),
   with their known aliases, and each marked single-valued or a list. No
   model. This also closes F7.
2. For everything else, the deep pass is shown the subject's current facts
   (it is already shown the names in use) and may say which one its new fact
   replaces. Kazma accepts that only for the same subject, and the trust
   gate still decides.

Gate: the extraction benchmark's "same name each time" score (benchmark
decision 1) and the update category of F1. Negative control: free naming
leaves two current facts. Cost: one to two weeks. It changes the prompt, so
it lands before the fresh recording.

### F5. Forgetting leaves more than it says

**Measured** (probe 9). More in F11.

1. **A forgotten fact keeps its subject and predicate.** A turn "Sara was
   diagnosed with celiac disease" and its fact were forgotten the way the
   Memory page forgets. Afterwards the fact's row reads `sara`,
   `diagnosed_with`, and an empty value, and the search index still answers
   a search for "diagnosed". The export hides the subject and predicate of a
   forgotten fact, which shows the intent; the storage does not match it.
   The planted-word test plants its word only in the value, so it cannot see
   this.
2. **"Forget everything that mentions X" leaves X in the fact.** After
   `forget_mentions("BLUE-HERON-42")` the fact is ended, and its value still
   reads "the vault codeword is BLUE-HERON-42". It is not recalled; it is
   still in the row, the mirror, the export and every backup taken after.
   **Reported**: the same call does not search subjects, predicates, ended
   facts, weekly summaries, entity names or About me, compares with SQLite's
   `LIKE` (ASCII case only, no Arabic folding), and is not written to the
   forget journal, so a restore brings back what it removed
   (`memory/forget.py:1183-1225`).

What the best do. Less. ChatGPT, Claude and Copilot keep a saved memory when
the chat it came from is deleted. Gemini asks the user to delete every chat
that holds a fact. Zep, the most thorough open system, documents that shared
summaries are not rebuilt after a delete. Kazma already leads here; these
gaps are what stands between "leads" and "exact".

Fix. A forgotten fact keeps its ids and where it came from, nothing a person
can read: subject and predicate are emptied with the value, and the index is
purged. `forget_mentions` sends every match through the same scrub as a
forgotten turn's facts, searches every content column the store registry
names, folds both sides, and is journaled.

Gate: the existing completeness scan, with the word planted in the subject,
the predicate and the value, and run after `forget_mentions` too, with an
Arabic word in two spellings. Negative control: today's scrub. Cost: 4 days.

### F6. There is no outside yardstick, and the inside one has weak spots

**Measured** in the benchmark audit; summarised here.

- Kazma has never been run on LongMemEval, LoCoMo or any public benchmark.
  Nothing can say today where it stands against Zep, Mem0, Hindsight or
  Mastra, in either direction.
- The deep-pass scores rest on one asking per turn of a model that gives a
  different answer one time in two. Six second askings moved Arabic recall
  from 0.909 to 0.636.
- The scoring counts a kept fact as "not found" when the model used a name
  the gold list does not have: all 20 "not found" facts of 77 were kept
  under another name. Seven of nine "wrong" facts were right.
- The lock cannot follow a second recording, keeps old floors across a new
  dataset, and loses its model scores quietly when the recording is absent.
- 40 of 72 recall scores rest on four questions or fewer. One question sits
  0.0004 above the floor and flips on another machine.
- The benchmark has no update, no temporal, no follow-up, no dense-topic and
  no poisoning category, which is why F1, F2 and F3 were invisible to it.

Fix: the benchmark audit's twelve fixes in its agreed order, the new
categories this report names, and an outside-benchmark harness (decision 7).
Costs are in that audit and in section 4.

### F7. An agent write labelled "a list" gets past the trust gate

**Measured** (probe 6).

`mutate_belief` takes the caller's label first and Kazma's own list of
single-valued names second (`belief_mutation.py`, about line 180). The
agent's memory tools default the label to `set`, a list
(`agent/tool_builtins/memory.py:444, 726, 922`), and the extractor defaults a
missing label to `set` (`belief_extractor.py:287`).

| Step | Result |
|---|---|
| The user: "I live in London." | London, the user's statement. |
| Control: the agent writes `lives_in Tokyo` labelled single-valued | Refused: `lower_trust_source`. |
| The agent writes `lives_in Tokyo` labelled a list | Added. London and Tokyo are both current. |
| Any later single-valued write by the agent (`lives_in Osaka`) | Accepted. London, the user's own statement, is closed as an extra row. |

In the one recording there is, the model labelled no single-valued name as a
list (29 single-valued, 8 lists), so today this needs the agent's tool, whose
default it is.

Fix: for a name Kazma classifies, Kazma's class wins over the caller's
label. F4's table makes most personal names classified. Gate: the probe as a
test. Negative control: today's order. Cost: 1 day.

### F8. Short statements are never read

**Measured** (probe 7; and the benchmark audit's turn m06, never sent to the
model).

A message of twelve characters or fewer is "filler" by length
(`belief_extractor.py:146`), for both passes. "I'm diabetic", "I'm vegan",
"Call me Sam" and "No, Lisbon" teach nothing. Kazma's own rule for small talk
is "by its words, never by length"; this path predates it.

Fix: decide filler by words (the small-talk detector exists). Gate: a list of
short statements in English, Arabic and Gulf Arabic that must reach
extraction, and greetings that must not. Negative control: the length rule.
Cost: 1 day.

### F9. Recall weakens where a person talks most, and cannot follow a follow-up

**Read**. The dense-topic effect on turns is **not measured** (it needs the
embedder; queued for after the quiet hours, section 8).

- **The "is this about the question" test uses a window of ranks.** A turn's
  evidence is its similarity above the "background", and the background is
  the mean of meaning ranks 4 to 15 (`recall.py:1324`), with fixed floors.
  When twenty turns are on one topic, ranks 4 to 15 are all on that topic,
  the background rises to meet the best match, and the lift falls under the
  floor. Kazma already met this with weekly summaries (at 97 live summaries,
  "list me all my subscription resets" found none of ten) and moved
  summaries to a median. Turns and facts still use the window.
- **A follow-up is searched as it is typed.** "And her sister?" is the
  recall query (`agent/graph_supervisor.py:492`). A turn's vector is made
  from its question alone (`episode_text.py:88`), so a turn whose question
  was "yes, do that" is out of reach by meaning. The LongMemEval paper
  measured +9.4 % recall from adding extracted facts to what is indexed.
- **The block is always five facts, five turns, two summaries**, each turn
  cut to 200 and 300 characters, with no id and no tool to open one in full
  or to list all. Questions that need counting or a full list ("how many
  times", "all my subscriptions") cannot be answered from it.
- **The chat's own turns get a boost of 0.35** on an evidence scale of 0.04
  to 0.5 (`recall.py:1259`), so the block tends to fill with the chat the
  model is already reading.
- **The turn is never told recall was incomplete.** The supervisor reads
  only "empty or not" (`graph_supervisor.py:1188`); a degraded or failed
  recall looks like "nothing known". **Reported**: on the Postgres-primary
  path errors become an empty list.

Fix: a median or fixed-quantile background for turns and facts, swept again
on the benchmark with a dense-topic category; a one-line standalone gist per
turn written by the deep pass (the column and the embedding rule already
exist); two read tools, "open this memory" and "list by subject, name or
dates", with ids in the block; no boost for turns still inside the context
window; a note to the model when recall was incomplete. Whether to also
rewrite a follow-up with a model is decision 14.

Gate: benchmark categories "dense topic" (20 to 100 turns on one subject),
"follow-up", "count and list"; a forced embedder failure through the real
supervisor node shows the note. Negative controls: the rank window; the raw
follow-up. Cost: two to three weeks.

### F10. What a chat leaves outside its own memory controls

**Read**.

- **Swarm results.** A swarm worker's result is stored as a memory with no
  chat and no tenant passed (`swarm/worker_dispatch.py:78`,
  `memory/swarm_bridge.py:188-193`: session `swarm:<worker>`, importance 3).
  A chat set to "Don't remember this chat" that starts a swarm task leaves
  "Task: ... Result: ..." as a recallable memory. Forgetting the chat does
  not reach it. **Reported**: the weekly summary renders it as the user's
  words.
- **The deep pass reads the assistant's text unfenced.** The model is given
  "User: ... Assistant: ..." with no line saying the text is data
  (`belief_extractor.py:240`). The screen is one override check and the
  model's own confidence. An assistant answer that summarised a web page can
  become a fact, and under "Known Facts" it reads like any other.

Fix: a swarm result carries its chat's keys and tenant, asks the forget
ledger, and is forgotten with its chat. The deep pass's input is fenced like
every other model input, and facts whose value is only in the assistant's
text are shown as inferred. Gate: a kept-out chat's swarm run writes no
memory; a poisoned assistant text stores nothing. Cost: 3 days.

### F11. Forgets and restores

**Read** where a line is named, otherwise **Reported**.

- The forget journal's replay applies at most 200 entries a pass and stops
  at the first that fails (`memory/forget_journal.py:207-229`, read). The
  sweep does not report what is left.
- Keeping one turn out of memory writes the ledger and no journal entry
  (`memory/forget.py:1136`, read). After a restore of an older memory
  database that turn can be learned again.
- **Reported**: a chat that was forgotten and then deleted can come back
  after a restore under its second key; two installs sharing one Postgres
  mirror can lose a forget or push text back over a tombstone; a forgotten
  value can re-enter from the chat's own checkpoint or chat-store copy.
- The declared erasure horizon (`memory/memory_stores.py:304-316`, read)
  does not name the Postgres dumps of the mirror tables, the WAL archive and
  base backups, the Neo4j store, or migration bundles. Vectors are content
  too: published work rebuilds 92 % of short texts exactly from their
  embeddings.

Fix: the journal covers every kind of forget; replay continues past a failed
entry of an unrelated chat and reports what is pending in memory health; the
horizon lists every copy. Gate: the convergence test with a fault after each
step, extended to the new kinds; a restore drill that forgets by content
first. Cost: one week.

### F12. The deep pass is invisible and does not always finish

**Read**.

- The deep pass calls the model directly (`belief_extractor.py:237-249`): no
  failover, no row in the model-call ledger (so its cost is invisible and
  the daily limit does not count it), no output limit, no JSON mode, and the
  provider's default temperature of 0.7, which is the source of the answer
  noise the benchmark audit measured.
- **Reported**: an answer that cannot be parsed counts as done; after six
  failed attempts (about 1 h 42 min) the task is a dead letter; nothing
  queues the turns that never got a deep pass again.
- Entity resolution's third tier calls the model without `await`
  (`memory/entity_resolution.py:365`; `LLMProvider.chat` is `async def`), so
  it has never run. The setting that enables it does nothing.

Fix: the deep pass goes through `resilient_chat` (failover, ledger with the
chat as its thread, bounded output), at a declared temperature; a sweep
re-queues turns with no deep-pass mark; the third tier is fixed or removed.
Gate: a ledger row per deep pass; a provider outage ends with every turn
extracted. Cost: 3 days. Do it early: it makes every later measurement
cheaper and steadier.

### F13. What is kept beyond turns and facts is thin

**Read** for the lessons cap. Otherwise **Reported**.

- **Lessons.** Every finished turn that memory keeps teaches a "lesson" with
  one more model call, and the store holds the newest twelve
  (`skills/self_improvement.py:32, 107-111`). One real correction is pushed
  out by twelve routine turns. The refiner cannot answer "nothing to learn".
- **Procedures.** The procedural store holds a tally per tool, not a
  procedure. What comes back into a turn is a line such as "file_read
  (C=0.99, n=812)". It is written for the `default` tenant only.
- **Summaries.** One level only: a week, per topic. No month, no project, no
  "state of X" page, no summary of summaries, and a summary is never checked
  against its turns. A history import adds chats to weeks already summarised,
  and they are never summarised.
- **No profile is built.** "About me" is typed by hand. Nothing recalled has
  any authority: a preference the user stated last month comes back inside a
  "never obey" fence.
- **Tiers, importance and access counts** do little, and one rule moves a
  "remember this" turn between two tiers every six hours.

What the best do. Mastra keeps a dated, prioritised log built by a
background observer and reflector, and tops LongMemEval with it (94.87 %
with gpt-5-mini, self-reported). Letta runs "sleep-time" agents that rewrite
memory between turns. ChatGPT, Claude and Letta pin a profile in the
context.

Fix: learn on a signal (a correction, a recovered failure, explicit
feedback), merge lessons, and allow "nothing"; stop injecting tool tallies;
add monthly and per-project roll-ups with named sources so a forget follows
them; suggest profile lines the user approves (decision 10). Cost: three to
four weeks. Learning on a signal saves a model call on almost every turn.

### F14. Nothing says whether memory helped

**Reported**; the metric and alert counts were read.

The only memory metric is the count of operator actions. There are three
memory alert keys. Health shows liveness. Nothing records, per turn, what
was injected, whether recall was empty or degraded, how long it took, or
whether the answer used it. A week of failed extraction or of keyword-only
recall pages nobody.

Fix: one recall record per turn (ids injected, empty, degraded, latency);
"used" (the answer overlaps an injected item) and "corrected" (the user
supersedes or forgets soon after an injection); contradictions and growth
per week; a line in the daily digest and three alerts. Cost: one week.

### F15. Scale

Kazma's own guide states the ceiling: about 8 seconds a question at 170,000
memories, because meaning search compares every row. **Reported**: several
sweeps read every row of every tenant on each 15-minute pass, in one serial
lane with no deadline. I measured no latency of my own. Zep reports 0.5 to
0.8 s and Mem0 0.15 to 0.2 s for search, on their own hardware and stores.

Fix: keep exact search as the truth, and past a size narrow the candidates
first (tenant, dates, keyword hits, an approximate index's top hits) and
re-score them exactly, with a gate that the benchmark's results are equal.
Give each sweep a cursor and a budget. Cost: two weeks. Not urgent for one
operator's store.

### F16. Tenancy faults that exist today

**Measured** (probe 3) and **Read**. They matter once there is a second
tenant, so they are in section 5: a turn's identity has no tenant; two GET
routes read `default` for any caller; a tenant error becomes a junk tenant
that writes proceed under; turn reconcile files a turn under the chat
store's tenant.


## 3. Kazma against the state of the art, part by part

### 3.1 The comparison

| Part | Kazma today | The best elsewhere | Verdict | What would make it the best |
|---|---|---|---|---|
| **What is kept** | Every turn whole, forever (archive is a move, never a delete). Facts beside it. | Zep and LongMemEval's authors found that keeping the raw rounds beats keeping only extracted facts. Letta's plain "search the files" agent scored 74.0 % on LoCoMo, above Mem0's graph. | **Leads in design.** | Nothing to change. Keep saying it. |
| **Writing a fact** | Two passes. The quick pass is exact and free. The deep pass is one model call a turn, noisy (F12), and blind to the date and the previous turn. | Zep: about six model steps a message, with the previous four messages. Mem0: one call with a rolling summary and the last ten messages. Hindsight: narrative facts with an occurrence time. | Behind on context given to the model; ahead on cost. | Give the deep pass the date, the previous turn and the subject's current facts (F1, F2, F4). Still one call. |
| **A fact that changes** | Replaces by subject and name. Names are unstable (F4); the trust gate refuses free-form corrections or is silently undone (F1). | Zep closes the old edge at the new one's start. Mastra 96.2, Hindsight 94.9 on LongMemEval knowledge updates. | **Behind. The largest gap.** | F1, F4, F7, and retractions ("I stopped", "no longer"), then the LongMemEval knowledge-update score beside theirs. |
| **Time** | Two clocks stored on every fact, turns ordered by when they were said, late writers handled. None of it shown or asked (F2). | Zep shows date ranges and resolves "last week". Hindsight has a time channel. | Equal in storage, **behind in use.** | F2's three steps. |
| **Recall** | Exact meaning search over every row plus keywords plus a graph walk, ranked on evidence, with a floor. No model in the path. Fixed 5/5/2 block. | Zep and Hindsight: several channels, then a cross-encoder reranker; no main model in the path. LongMemEval: +9.4 % from indexing extracted facts with each round. | Ahead on honesty (exact, abstains) and cost. Behind on follow-ups, dense topics, lists and counts (F9). | F9. A reranker is worth measuring on the outside benchmark before deciding (decision 14). |
| **Saying nothing** | Built in: an evidence floor and gap; the benchmark gates abstention. | No leader. Mem0's own BEAM abstention is 52.5 and 40.0. LongMemEval has 30 abstention questions. | **Leads in design.** Unproven outside. | Run LongMemEval's abstention questions and a "don't know" control. |
| **Forgetting** | A forget follows the turn into facts, summaries, lessons, vectors, the mirror, the graph copy and Time Travel; a ledger stops it being learned again; a journal survives restores; a test plants a word through every writer. | Products keep a saved memory when its chat is deleted. Zep is the most thorough open system and documents gaps. MemoryAgentBench's "selective forgetting" tops out at 32.5. | **Leads**, with F5 and F11 open. | F5, F11, then publish the planted-word result as a number nobody else has. |
| **Trust and poisoning** | A trust ladder, screening of agent writes, a fenced recall block, recall rules outside the fence. | Nothing in any open system. Attacks: MINJA 98.2 % injection, AgentPoison over 80 % at 0.1 % poison. | **Leads in design**, with F1, F3, F7, F10 open. | Close them, then run the published attacks against Kazma and publish the rate. |
| **Lessons, procedures, profile, long-range summaries** | Twelve lessons, tool tallies, weekly topic summaries, a hand-typed About me (F13). | Mastra's observer and reflector; Letta's sleep-time agents and pinned blocks; LangMem's prompt optimiser. | **Behind.** | F13, with Kazma's own rule kept: everything derived names its sources, so a forget follows it. |
| **Several people** | One `user` per tenant. The Spaces R3 study designs an entry per member with "said by". | cognee has permissions on datasets. Zep has per-user and shared graphs. No system has per-speaker trust in one memory. GroupMemBench's best is 46.0 %. | Level today; **could lead** with S1-11. | Section 5, then GroupMemBench. |
| **Arabic** | Folding, Gulf and standard stop words, an Arabic quick pass, ids that keep their script, a benchmark in English, Arabic and mixed. | Nothing found. No Arabic long-term-memory benchmark exists. | **Leads by default.** | Publish the first parallel English and Arabic memory benchmark (decision 9). |
| **Copies and operations** | Converging mirror and graph copies with outboxes, restore drills, a crash matrix, a soak harness, an operator trail. | Not documented by any memory library. | **Leads.** | Signals about quality, not only liveness (F14). |
| **Measurement** | Its own ratcheted benchmark with held-out questions. Weak spots in F6. No outside number. | LongMemEval and LoCoMo scores, mostly self-reported. | **Behind on evidence the world accepts.** | The benchmark audit's fixes and decision 7. |
| **Cost and speed** | No model call to recall. One model call a turn to extract, one more for a lesson. Recall 8 ms on the benchmark store (Kazma's own figure). | Zep cuts LongMemEval context from 115,000 to 1,600 tokens and answers in 2.6 s against 28.9 s. Mem0 search p95 0.2 s. | Likely ahead per turn, **not measured** at size. | Tokens and p50/p95 reported with every benchmark run. |

### 3.2 What "the best" has to mean

There is no single leaderboard to top. The honest claim is a set of
properties, each with a number beside the named alternatives:

1. Answers from memory as well as the leaders on LongMemEval's five
   abilities, with the reader model named and the full-context and oracle
   baselines beside it.
2. Finds the right turns (recall at k) on LongMemEval and LoCoMo with no
   model in the path.
3. Says nothing when nothing is known.
4. Handles a changed fact and a dated question.
5. Forgets from every copy, proven by a planted word.
6. Resists the published memory-poisoning attacks.
7. Does all of it in Arabic as well as in English.
8. At a stated cost in tokens and time.

Kazma can already make claims 3 and 5 in design, and nobody else makes 5, 6
or 7 at all. Claims 1 and 4 are where it would lose today (my expectation
from F1 and F2; **not measured**).

### 3.3 How far to trust the outside numbers

- LoCoMo has known faults: an audit found 6.4 % of its answers wrong and its
  judge accepting 63 % of deliberately vague wrong answers. Sending the whole
  conversation to the model beats most memory systems on it (72.90 against
  Mem0's 66.88 in Mem0's own paper).
- LongMemEval-S now fits inside a large context window, and its scores
  depend heavily on the reader model (Mastra: 84.23 with gpt-4o, 94.87 with
  gpt-5-mini). Vendor scores are not comparable with each other.
- Zep and Mem0 dispute each other's LoCoMo figures (Zep at 65.99 by Mem0,
  75.14 by Zep).

So an outside run of Kazma must carry its own baselines on the same reader,
or it proves nothing. That is what decision 7 asks for.


## 4. The plan to "best", in order, with costs

Costs are builder-days by my estimate, good for comparing sizes and no
better than plus or minus a third. "Calls" are model calls, which need the
owner's yes each time.

Two rules shape the order:

- **Measure before and after.** The benchmark's scoring and lock are fixed
  first, and the new categories are added while they still fail, so every
  later fix shows as a number that moved.
- **Record once.** Five fixes change the deep pass's prompt (the date, the
  previous turn, the subject's current facts, the spelling rule, the name
  table). Each changes every recorded reply's key. They land together, and
  the fresh recording (benchmark decision 2: 150 askings, 300 at most) is
  made once after them, not once per fix.

| Stage | What | Closes | Gate (and its negative control) | Calls | Days |
|---|---|---|---|---|---|
| **A. Before S1-09** | The eleven items of section 5.2. | F16 | The Spaces matrix with a victim planted under `default`; a sweep under enforcement with no scope bound fails loudly. Control: today's code reads across. | 0 | 5, shared with the Spaces builders |
| **B. Measure honestly** | Benchmark audit steps 1 and 2 (scoring: kept, kept under an expected name, unwanted; lock rules). New categories that fail today: update, temporal, quoted and attached text, short statements, dense topic, follow-up, count and list. The deep pass through the ledger with failover at a declared temperature (F12). The outside retrieval harness (decision 7, part 1). | F6, F12 | A lock that cannot keep an unreachable floor; each new category red on today's code. Control: the old scorer calls a kept fact "not found". | 0 | 16 |
| **C. Say nothing wrong** | In this order: the user's own words (F3); trust by whose words (F1); Kazma's class wins (F7); filler by words (F8); the name table and "replaces" (F4); dates shown (F2 step 1); the date and the previous turn given to the extractor (F2 step 2); retractions; swarm results and the fenced deep-pass input (F10). Then the one recording and the extraction rebaseline. | F1, F2 (part), F3, F4, F7, F8, F10 | The update, temporal, quoted-text and short-statement categories go green. Controls: each fix switched off turns its category red again. | 150 (300 at most) | 28 |
| **D. Forget means gone** | The tombstone keeps nothing readable; `forget_mentions` complete and journaled; the journal covers every forget; the horizon lists every copy. | F5, F11 | The planted-word scan with the word in subject, predicate and value, after every kind of forget, and after a restore. Control: today's scrub. | 0 | 9 |
| **E. Against the world** | LongMemEval-S and LoCoMo end to end on a sample, with full-context and oracle baselines on the same reader. The published poisoning attacks run against Kazma. The first parallel English and Arabic memory set. Results on the benchmarks page. | F6 | The page is generated from the result files, like today's. Control: a run without its baselines is refused by the page generator. | about 600 for 100 questions with both baselines | 15 |
| **F. Recall** | Time words in the question (F2 step 3); median background; the gist per turn; ids, "open" and "list" tools; no boost inside the window; the "recall was incomplete" note. | F2 (rest), F9 | Dense-topic, follow-up, count-and-list categories green; outside recall at k not lower. Controls: the rank window; the raw follow-up. | 0 for the heuristic path | 17 |
| **G. Consolidation** | Lessons on a signal, merged; tool tallies no longer injected; monthly and per-project roll-ups with named sources; suggested profile lines. | F13 | Twelve routine turns keep a correction; a forget empties a roll-up built from it; nothing joins About me without the user's yes. Control: the 12-slot queue. | one per roll-up, in the background | 18 |
| **H. Running it** | A recall record per turn and the quality signals; a "what Kazma knows about me" page with source, date, history and inline correction; cursors and budgets for the sweeps; candidate narrowing with exact re-scoring past a size. | F14, F15 | A week of failed extraction pages once; equal benchmark results with narrowing on. Control: narrowing without re-scoring changes results. | 0 | 20 |

In all about 125 builder-days, six months for one builder. Stages B, C and D
(about ten weeks) carry most of the gain and all five measured faults.
Stages D, G and H do not depend on C and can run in other sessions beside
it. Spaces stage 1 runs beside all of it once stage A is in.

What I would not do:

- **Not a graph database as the source of truth.** Zep's design needs one;
  Kazma's facts in SQLite with a Neo4j copy already give the walk, and by
  the recall code's own arithmetic the walk cannot change a ranking past
  forty facts (reported, not measured). Fix time and change first; they are
  where the leaders gain.
- **Not a model in the recall path by default.** It is the one cost the
  leaders also avoid.
- **Not approximate search as the default.** Kazma's Knowledge work already
  measured what it loses.
- **Not Mem0's "only add".** It is the cheapest write path and its own
  contradiction score shows the price.


## 5. What the Spaces plan must consider about memory

The plan read is `docs/plans/SPACES_PLAN.md` at `2ceb89b7f`, with the R3
study. Its shape for memory is right: the space is the tenant, the mode
setting goes, `default` becomes an ordinary space, and a shared space files
facts per member. What follows is what the memory code needs that the plan
does not yet say.

### 5.1 The short version

- **Before S1-09**: eleven items (5.2). Without them S1-09 either writes a
  turn's memory under the wrong tenant, fails every background sweep, or
  leaves paths that read every space.
- **Before S1-11**: seven items (5.3). The largest is that S1-11 extends a
  trust gate that today refuses a member's own corrections (F1).
- **S1-26**: "background sweeps read index columns only" is not true of
  memory, and vectors and the search index are content (5.4).
- **S1-27**: the registry it counts, exports and erases "from" does not list
  memory's outside copies, and memory has no hard erase today (5.5).
- **S1-28**: a memory restore is one file for every space (5.6).
- **N19**, memory shared by topic: decided by the owner, owned by no item
  (5.7).

### 5.2 Before S1-09 (the space is the memory tenant)

| # | What the plan must add | Evidence | Gate |
|---|---|---|---|
| 1 | **A turn's memory tenant comes from the bound scope, never from the turn's state.** Today three transports and compaction put a tenant into the graph state from the mode (`resolve_tenant_id`), and recall, post-turn memory and the memory tools read that value. S1-07 binds the scope from the thread's record; memory does not ask it. A turn paused before S1-09 (an approval, a cut turn) resumes with the old value from its checkpoint. | Read: `kazma-ui/kazma_ui/sse_chat/__init__.py:1078`, `kazma-ui/kazma_ui/voice_turn.py:245`, `kazma-gateway/kazma_gateway/agent_handler/store.py:252`, `kazma-core/kazma_core/compaction.py:320`; `agent/graph_respond.py:424-427` (`state.get("tenant_id", "default")`); `agent/graph_supervisor.py:1185`. | A checkpoint holding tenant `telegram`, resumed under the scope of space X, reads and writes X only. Control: today's code writes `telegram`. |
| 2 | **Memory's enforcement switch is Spaces' switch.** Memory enforces tenants when the install is multi-user or in production mode, not when Spaces is enforced. One account with two spaces counts as "not enforced": the Memory page shows `default`, About me falls back to `default`'s, and `default`'s forget ledger applies to every tenant's writes. | Read: `memory/config.py` (`memory_tenant_enforced`), `memory/profile.py` (`about_for_turn`). Reported: `memory/forget.py:153` (`tenant_id IN (?, 'default')`). | One account, two spaces: each space's page, About me and ledger are its own. Control: today's switch. |
| 3 | **Every "install's view" site, by name, with a gate.** The plan states the intent. The code has it in about fifty places, and each is a path where asking as `default` returns every space. The plan must also say what the admin uses instead (install work, with counts and no content). | Read earlier in this audit: `kazma-ui/kazma_ui/memory_api.py` (344, 477, 2213), `routes_direct/memory.py` (`_tenant_clause`), `entity_key.py:73`, `entity_resolution.py:579, 626`, `entity_retire.py:97`, `forget.py:285-300`, `hygiene.py:176`, `topic_summaries.py:1089`, `worker_bootstrap.py:212`. Reported: 24 comparisons in `memory/`, 27 in the UI. | A source gate: no memory code compares a tenant with the literal `default`. Planted old comparisons as its control. |
| 4 | **Background work binds a space or declares install work.** The queue handlers and the thirty or so 15-minute sweeps run with nothing bound. Under enforcement `require_space_id()` raises, so every one fails. The list of tenants they walk comes from the episodes table (a space with facts and no turns gets no sweep) and on any error becomes `["default"]`, logged at DEBUG. | Read: `memory/worker_bootstrap.py:664-694` (`_distinct_tenants`). Reported: the job table in section 5.8. | Every sweep runs under enforcement on a store with two spaces and touches both; a failed space list is a failed sweep. Control: nothing bound raises. |
| 5 | **Writers that pass no tenant.** The procedural recorder, swarm results (and their reader, which asks for tenant `system`), history import from the page and the CLI, the manual Reconsolidate route (the literal `default`), the evolution log. | Read: `swarm/worker_dispatch.py:78`, `memory/swarm_bridge.py:188-193`, `kazma-ui/kazma_ui/routes_direct/memory.py:971`. Reported: `agent/tool_registry.py:248`, `swarm/phonebook.py:42`, `history_import_api.py:111`, `routes_direct/memory.py:956`. | The Spaces matrix gains a case per writer. Control: the write lands in `default`. |
| 6 | **A turn's identity includes its space.** The id of a remembered turn is a hash of chat, turn number and text, with no tenant. The same turn written for a second space is ignored, reported as written, and found by recall in the first space only. History import's "already held" check has no tenant either, so it tells one space what another holds. A note stored with `memory_store` uses the chat name `memory_store` and turn 0 for every space. | Measured: probe 3. Read: `memory/dual_write.py` (`_episode_id`), `memory/history_import.py` (`_existing`). | The same export imported into two spaces gives two sets of memories, each recalled only in its own. Control: today's id. |
| 7 | **Tables keyed by `id` alone, and tables with no tenant.** Turns, facts, archived facts, summaries, merges and procedures have `id` as their whole key. The reconcile state is one row for the install (one cursor for every space). The applied-forget marks, summary sources and the task queue have no tenant column (the queue's is inside its payload). Recall has id-only reads and updates. | Read: `memory/schema_v2.py`. Reported: `vector_engine.py:182`; `recall.py:896, 1516, 1688, 2303, 2365` and the access-count update at 2259-2260. | Each id-only statement names its tenant, or is declared with why. This is the entity key's gate (M21), widened. |
| 8 | **Routes that take the tenant from somewhere else.** Two GET routes read `default` for any caller (the Neo4j graph export and the procedure search). The graph-clear route takes the tenant from a query value. A tenant error becomes the tenant `__unscoped__`, and writes proceed under it. The operator trail records the session's tenant, not the one acted in. The route gate plants its victim under `beta` only, so it cannot see a read of `default`. | Read: `routes_direct/memory.py:971, 1259`; `memory_api.py:284-293`. Reported: `routes_direct/memory.py:133`; `memory_actions.py:103-107`; `tests/test_memory_routes_tenant_scope.py:328, 338`. | The matrix plants a victim under `default` as well. Control: the two routes answer it. |
| 9 | **The old tenants (R6) are in more places than turns and facts.** The forget ledger and journal, About me, summaries and their periods, entities (each old tenant has its own `user` hub, so a merge into one space needs the entity merge), queue payloads, the mirror, the Neo4j copy. And on an install that used `per_platform` or `per_user`, turn reconcile filed second copies of turns under the chat store's tenant. | Read: `memory/turn_reconcile.py:141`. The plan's own step 4 of its migration counts turns and facts. | The count report names every table and copy. Nothing merges until the ledger and journal move with their rows; otherwise forgets stop applying. |
| 10 | **The registry lists memory's outside copies.** `SPACE_RULES` has rows for the three memory SQLite files. It has none for the Postgres mirror tables, pgvector or Qdrant, the Neo4j copy, the nightly export files or the repair copies. Step 0's gate ("every store declares its space rule") reads SQLite stores, so these escaped it. S1-27 works "from the registry". | Read: `kazma_core/store_registry.py` (`SPACE_RULES`), `memory/memory_stores.py` (`EXTERNAL_STORES`). | The space-rule gate also reads `EXTERNAL_STORES`. Control: a planted outside copy with no rule. |
| 11 | **Recall's promise across spaces, as a test of every channel.** Recall has ten ways in: keywords, meaning, the graph walk, the bridge from turns to facts, summaries, the past-chats fallback, the mirror top-up, procedures, About me, lessons. Each is filtered by tenant today in its own way. | Reported: the tenant defaults listed in 5.8. | The same token planted in two spaces; each channel forced in turn; no hit across. Control: one channel with its filter removed. |

Two smaller things to decide with these, not blockers: fairness (one queue
with two memory slots and one weekly-summary allowance for all spaces, so a
busy space delays the others), and the mirror. Two installs that share one
Postgres mirror share the tenant `default` today by design; once `default`
is "the owner's space" on each install, the mirror's key needs the install
or the space ids need to be unique across installs.

### 5.3 Before S1-11 (an entry per member)

| # | What the plan must add | Why |
|---|---|---|
| 1 | **F3 and F1 land first.** S1-11 adds the label `member_stated` and the rule "a member's own words always win" (N34) to the trust gate. | Today the gate refuses a person's own free-form correction, or is undone by the model's spelling (F1), and counts quoted and attached text as the person's own words (F3). In a shared space that becomes: Omar forwards a document about Sara, and Kazma files it as Omar's own statement. |
| 2 | **F4 lands first, or N34 holds only on paper.** | "Never replaces what the member said about themself" is a comparison under one predicate name. With free names, Sara's own "pilot" and Omar's "Sara is a captain" are two current facts under two names, and no rule is ever asked. |
| 3 | **The benchmark's steps 1 and 2 and the edge rule land first.** S1-11's one-member gate is "the locked v5 scores exactly". | One of those scores rests on a question 0.0004 above the floor that flips on another machine (benchmark audit). And the speakers' lines change what the model returns: with them the recorded scores were English 0.692 and 0.714, Arabic 0.636 and 0.636, mixed 0.800 and 0.800, against a lock of 0.769/0.786, 0.909/0.909, 0.700/0.583. S1-11 needs its own recording, counted in decision 2's budget. |
| 4 | **One home for the text a fact's vector is made from.** R3 changes it to `user <predicate> <object>`. | It is built in three places today: `memory/belief_mutation.py:607`, `memory/reembed.py:288` and the SQL at `memory/reembed.py:332`. A repair pass with the old text would quietly re-make every vector the old way. |
| 5 | **Who, on the write path and the forget path.** `said_by` is in the plan for facts. | Nothing carries the asker today: not the turn, the post-turn record, the queue payload, the ledger or the journal. N32 ("the turn's author and the space's owner may forget") needs the author on the turn and the actor on the journal entry, or a restore replays forgets nobody can attribute. |
| 6 | **Summaries and lessons in a shared space.** | Weekly summaries are written from text that labels every turn "User:" under "a user's conversations" (reported: `memory/topic_summaries.py:148, 449`), so a shared space's summary will give every member's words to one person. And N36 forgets a leaving member's facts; the summaries that state those facts in prose are rebuilt when a *turn* is forgotten, not when a fact is. The plan must either rebuild them or say on the leave screen that they stay. |
| 7 | **About me and the recall block.** | About me is one row per tenant (`memory_profile`), and N31 wants one per member per space. The recall block has no way to say who said a thing, and no dates (F2); N35 shows "everything in the shared space, under the member's name". |

### 5.4 S1-26 (a key per space, and sealing)

The plan seals a space's secrets now and its content later, and says of the
later version that search indexes open only while the space is unlocked.
For memory the plan must add:

- **Sweeps read content.** Macro sleep, vector repair, turn reconcile, the
  weekly summaries, the deep pass, reconsolidation, forget-by-mention, the
  search index's own maintenance, the mirror sync and the graph sync all
  read text. "Background sweeps read index columns only" is not true of
  memory. A sealed space's memory either waits for the space to be unlocked
  or these jobs need a design of their own.
- **Vectors and the search index are content.** The registry's list of
  memory's content columns leaves out the vectors (its comment says they
  "follow their row"), the entity ids and the full-text index's own tables.
  A short text can be rebuilt from its vector. Queue payloads hold a turn's
  text while it waits.
- **The realistic sealed shape is one memory database set per space**, in
  the space's folder, opened with the space's key. Column encryption breaks
  both the full-text index and meaning search. The owner kept this open for
  memory (N6). It should be decided before S1-09 changes any key (5.2,
  items 6 and 7): if a file per space is coming, salt ids with the space now
  and do not rebuild the tables' keys (decision 17).
- **The Postgres mirror, pgvector, Qdrant and the Neo4j copy sit outside any
  sealing.** In the sealed version they are per space with their own keys,
  or off for sealed spaces.

### 5.5 S1-27 (count, export and erase one space)

- **There is no hard erase in memory today.** Forget is a tombstone by
  design, and a tombstone keeps the subject and predicate (F5) and a ledger
  row with the chat's key. Erasing a space must delete: rows in every table,
  the ledger, the journal's entries, the search index's segments (a purge,
  as Forget does), freed pages, queue payloads, audit rows, remote vectors
  (deletes are by id only today), the mirror's rows (it has no delete path
  for turns), the graph copy's nodes and edges for that tenant, the nightly
  export files (named by tenant, never removed), the space's lessons and its
  Time Travel steps.
- **The repair copies and backups hold every space.** `memory_state.db.pre_*`
  and every backup generation hold the erased space until they age out. The
  plan's "honest limit" should name them.
- **Erase must be journaled.** Without a "space erased" entry, restoring an
  older memory database brings the space back, and turn reconcile rebuilds
  its memories from the chat store if the chats are still there. Order:
  chats first, then memory, then the journal entry.
- **Export.** Memory's export today has no importer, gives facts without
  their ids, and has no format version (reported: `memory_api.py:2573-2693`).
  The plan should say whether "export this space" is for reading or for
  moving, and build the importer if it is for moving.
- **Count.** Tables with no tenant column cannot be counted per space (5.2,
  item 7).

### 5.6 S1-28 (backups and restore)

- **A memory restore is one file for every space.** `restore_memory_backup`
  copies a backup over the live database. Restoring for one space rolls
  every space back; the forget journal then replays everyone's forgets, and
  everyone's memories written since are rebuilt by turn reconcile. The plan
  should state that restore is install-wide and the admin's, and what
  members are told.
- **The replay is capped and ordered for the whole install.** At most 200
  entries a pass, stopping at the first failure (F11). One space's failing
  entry holds up every space's forgets after a restore. It needs an order
  per space.
- **Spaces in memory that the identity store no longer has.** After a
  restore of one store and not the other, memory can hold rows for a space
  that does not exist, and the reverse. A sweep should report both.

### 5.7 Memory shared by topic (N19)

The owner decided that the workspace starts empty and the owner picks what
to share, "memory by topic" among it. No item builds it. R3 says it is not
in S1-11, and S2-12 moves chats, documents and files.

It is not small. A plan item needs to say:

- what "a topic" selects: turns (by the weekly summaries' grouping), facts
  (by entity), or both;
- whether sharing copies or links. A copy needs the identity fix of 5.2 item
  6, and a link from the copy to its source, so that forgetting the source
  reaches the copy. Without the link, sharing becomes a way to keep what was
  forgotten;
- that the forget ledger travels with the copy (a fork already does this:
  `forget.inherit_ledger`);
- that the owner's personal facts ("user lives in X") are re-filed under the
  owner's member entry in the workspace (S1-11), since a shared space has no
  single `user`;
- how to unshare, and what the other members are told.

My proposal is a short study, like R3, after S1-11 (decision 18).

### 5.8 The copies, summaries and jobs per space

- **Neo4j**: per space by the `tenant_id` property, under the existing
  `(tenant_id, key)` constraint. Neo4j Community has one database, so "a
  database per space" is not available. The export route must filter by
  tenant and by install (5.2, item 8). Erase removes this install's nodes
  and edges for the tenant.
- **Postgres mirror**: the tenant column is there. Its dumps and the WAL
  archive hold every space, which N10 already accepts.
- **Summaries**: periods are keyed by tenant and period, and the grouping
  bar is each tenant's own, so they are per space already. The allowance of
  two weeks in flight is shared by every tenant, oldest first.
- **Where each background job gets its tenant today** (reported):

| Job | Tenant comes from |
|---|---|
| Post-turn memory | its payload |
| Deep pass | the turn's row |
| Macro sleep, nightly export, daily reconsolidation | the list of tenants found in the turns table |
| Manual Reconsolidate | the literal `default` |
| Weekly summaries | the turns' tenant, into the payload |
| Entity merge | its payload; none means every tenant |
| Turn reconcile | the chat store's row |
| Forget convergence | the journal entry |
| Mirror, graph, vector repair, recovery | each row's own column |
| Swarm result, evolution log, procedural recorder, history import | none, so `default` |


## 6. Decisions for the owner

### 6.1 The six benchmark decisions, as agreed with the main session

These still wait for the owner. One refinement from this audit is marked.

| # | Decision | Proposal |
|---|---|---|
| 1 | How the extraction benchmark scores a fact. | Three outcomes: kept, kept under an expected name, unwanted. A "same name each time" score. Entries that must not be kept. |
| 2 | New askings of the model. | 150 fresh askings, 300 at most, with the owner's yes in the recording session and the read-only rules. The old 36 are kept as a drift check. **Refined here: make the recording once, after the prompt-changing fixes of stage C, and count S1-11's speakers recording in the same budget.** Recording before them spends the budget twice. |
| 3 | A hit that flips between machines. | It stops the lock, with the margin named and an "edge list" as the only exit. Never a threshold nudge. |
| 4 | The held-out questions. | Rename today's set honestly and keep gating it. A fresh set written by another session is the sealed one, out of the per-commit gate, with every look logged. |
| 5 | pgvector in CI. | A second service and job, exact against approximate settled first; fail on a named skip. |
| 6 | The benchmarks page. | Each model-pass row says how many askings it rests on, generated from the lock. |

### 6.2 New decisions

| # | Decision | Proposal | Cost of yes |
|---|---|---|---|
| 7 | **Run Kazma on the public benchmarks.** Download LongMemEval and LoCoMo (public datasets), run retrieval through the real `recall()` first, then answer a sample end to end beside the full-context and oracle baselines. | Yes. Retrieval first: it needs no model call and gives the "before" number for every fix. End to end after stage C. The download needs the owner's yes. | Retrieval: about an hour of the embedder per 100 questions, after quiet hours. End to end: about 600 calls per 100 questions with both baselines. |
| 8 | **Trust follows whose words hold the value** (F1). | Yes, after the "user's own words" helper (F3). The alternative is that corrections keep depending on how the model spelled the first value. | Stage C. |
| 9 | **Publish the first English and Arabic memory benchmark.** A parallel set, each language on its own, written by a different session than the builders. | Yes. Nobody has one, and it is the claim nobody can take from Kazma. | 5 days in stage E. |
| 10 | **A profile Kazma suggests.** Today nothing remembered has authority, and About me is typed by hand. | Suggestions only: "You said you prefer X. Add to About me?" Built from the user's own statements, never from inferences, and nothing binds without a yes. | Part of stage G. |
| 11 | **Dates in what the model is shown** (F2 step 1). | Yes. A few tokens a line. | 2 days. |
| 12 | **Lessons on a signal, not on every turn** (F13). | Yes. It also removes one model call from almost every turn. | Part of stage G; could be done alone in 3 days. |
| 13 | **Stop injecting tool tallies into turns.** | Yes. Keep the tallies for health. | Half a day. |
| 14 | **A model in the recall path** (rewrite a follow-up, rerank). Kazma has none today. | Not by default. Build the no-model rewrite, then measure a model rewrite and a local reranker on the outside benchmark as options and decide on the numbers. | Measured in stage F. |
| 15 | **A forgotten fact keeps nothing a person can read** (F5). The Memory page can then say only "a fact was forgotten", not about whom. | Yes. The export already behaves this way. | Stage D. |
| 16 | **Kazma owns the names of the common personal facts** (F4): about forty names with their aliases, in English and Arabic. | Yes, with the list reviewed by the owner. | Stage C. |
| 17 | **Spaces: rows now, a memory database per space for the sealed version.** | Rows now, with the space salted into new ids and no rebuild of the tables' keys. Plan the sealed version as one memory database set per space folder. Decide before S1-09 touches a key. | Saves a table rebuild now. |
| 18 | **Spaces: memory by topic (N19) gets an item and a study.** | A study like R3, after S1-11, before anything is built. | 3 days for the study. |
| 19 | **Spaces: erasing a space is a hard delete, journaled; a restore is install-wide.** | Yes to both, said in plain words on the delete and restore screens. | Part of S1-27 and S1-28. |


## 7. What already holds

Read or reported during this audit, stated in AGENTS.md, and worth
protecting as the rest changes:

- **Order by when it was said.** A late writer files a fact at its turn's
  time, and an older statement becomes history in its place
  (`memory/belief_mutation.py`).
- **The forget ledger is asked inside the insert's own transaction**
  (`memory/dual_write.py:289-301`), and the journal is written before
  anything changes.
- **Derived text follows a forget**: summaries are emptied and rebuilt,
  lessons from the turn are taken back, Time Travel steps go.
- **A stored, failed and refused write are three answers**, and a failed one
  goes to a durable queue with backoff.
- **Recall abstains, labels weak hits, and tells an incomplete search from
  an empty one** inside the search tool.
- **Exact meaning search, every row, no slice.**
- **The recall rules sit outside the data fence**, and every memory tool's
  output is fenced.
- **Arabic**: ids keep their script, grammar rules for the Arabic quick
  pass, folding on both sides of a search.
- **About me is never inferred, and is refused, not cut, past its limit.**
- **The copies converge**: outboxes filled by triggers, a daily full
  comparison, a probe for a copy that went back.

## 8. What was measured, and when

All times are Kuwait time (+03:00) on 2026-11-18.

| When | What | Model loaded |
|---|---|---|
| 03:52 to 04:18 | The benchmark audit's replays and lock demonstrations (its own evidence folder). | bge-m3, twice, at about 04:04 and 04:09 |
| 04:21 | A full test run was started and stopped at the main session's request. It never finished. | none |
| 05:12 | Probe 4 with the real embedder (`probes_real_embedder.json`, taken from that run's own output). | bge-m3, once |
| 05:26 and 05:30 | Probes 1 to 10 with no embedder (`probes.json`). Under one second each, at below-normal priority. | none |

The owner's quiet-hours rule (no tests or measurements from 01:00 to 10:00
except small targeted ones) reached this session at 05:20. Everything before
it was run without knowing of it. After it, only the two no-model probe runs
above were made; the rest was reading.

Queued for after 10:00, to be run when the review continues:

1. The dense-topic measurement behind F9 (twenty to a hundred turns on one
   subject, the real embedder, through `recall()`).
2. The effect of the 0.35 boost for the chat's own turns (F9).
3. Probe 4 with the real embedder again, from the final script.

## 9. What was not measured

- **Any outside benchmark.** No LongMemEval or LoCoMo run exists. Every
  statement about where Kazma would score is an expectation.
- **Any model behaviour beyond the recordings.** No model was called. The
  deep pass's behaviour is read from the one recording of 2026-10-15 and the
  R3 study's recordings.
- **F9's dense-topic effect on turns and facts**, the session boost, and the
  claim that the graph walk cannot change a ranking past forty facts. These
  are read from the code or reported.
- **Latency and scale.** No number here is mine. The 8 ms and the 8 seconds
  at 170,000 memories are Kazma's own published figures.
- **The Postgres-primary path, the mirror with two installs, and a real
  Neo4j.** Findings there are reported from reading.
- **The live install.** Nothing was read from it. How many of these faults
  have already happened in the owner's own memory is unknown; a read-only
  count (facts under two names, user statements now marked as inferences,
  refused corrections in the audit log) would say, and needs the owner's
  yes.
- **Everything marked Reported** in section 2 was read by a helper and not
  re-read by me.

## 10. Sources

Other systems:

- MemGPT and Letta: https://arxiv.org/abs/2310.08560 ·
  https://docs.letta.com/guides/agents/memory-blocks ·
  https://www.letta.com/blog/benchmarking-ai-agent-memory ·
  https://arxiv.org/abs/2504.13171
- Zep and Graphiti: https://arxiv.org/html/2501.13956 ·
  https://github.com/getzep/graphiti ·
  https://help.getzep.com/deleting-data-from-the-graph
- Mem0: https://arxiv.org/html/2504.19413 ·
  https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm
- LangMem: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- A-MEM: https://arxiv.org/html/2502.12110
- cognee: https://docs.cognee.ai/core-concepts/overview ·
  https://arxiv.org/abs/2505.24478
- HippoRAG and HippoRAG 2: https://arxiv.org/abs/2405.14831 ·
  https://arxiv.org/html/2502.14802
- Hindsight: https://arxiv.org/html/2512.12818v1
- Mastra Observational Memory: https://mastra.ai/research/observational-memory
- MIRIX: https://arxiv.org/html/2507.07957 · Nemori:
  https://arxiv.org/html/2508.03341 · LightMem:
  https://arxiv.org/abs/2510.18866 · MemOS:
  https://arxiv.org/html/2507.03724

Benchmarks:

- LoCoMo: https://arxiv.org/abs/2402.17753 ·
  https://github.com/snap-research/locomo · its audit:
  https://github.com/dial481/locomo-audit
- LongMemEval: https://arxiv.org/abs/2410.10813 ·
  https://xiaowu0162.github.io/longmemeval-v2/
- MemoryAgentBench: https://arxiv.org/abs/2507.05257 · GroupMemBench:
  https://arxiv.org/abs/2605.14498 · BEAM: https://arxiv.org/abs/2510.27246 ·
  PerLTQA: https://arxiv.org/abs/2402.16288
- The Zep and Mem0 dispute:
  https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- Emergence on LongMemEval:
  https://www.emergence.ai/blog/sota-on-longmemeval-with-rag

Products:

- ChatGPT memory: https://help.openai.com/en/articles/8590148-memory-faq
  (read from search snippets; the page refused a direct fetch)
- Claude memory: https://support.claude.com/en/articles/11817273 ·
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- Gemini: https://support.google.com/gemini/answer/16413516
- Microsoft 365 Copilot:
  https://learn.microsoft.com/en-us/microsoft-365/copilot/copilot-personalization-memory

Attacks, defences and erasure:

- MINJA: https://arxiv.org/abs/2503.03704 · AgentPoison:
  https://arxiv.org/abs/2407.12784
- SpAIware:
  https://embracethered.com/blog/posts/2024/chatgpt-macos-app-persistent-data-exfiltration/
- Unit 42:
  https://unit42.paloaltonetworks.com/indirect-prompt-injection-poisons-ai-longterm-memory/
- A-MemGuard: https://arxiv.org/abs/2510.02373 · write-time trust scores:
  https://arxiv.org/abs/2601.05504
- Text from embeddings: https://arxiv.org/abs/2310.06816 · retrieval after a
  delete: https://arxiv.org/abs/2608.20352
- The EDPB's 2025 report on erasure:
  https://www.edpb.europa.eu/system/files/2026-02/edpb_cef-report_2025_right-to-erasure_en.pdf
- Other users' records: https://arxiv.org/abs/2502.13172 ·
  https://arxiv.org/abs/2511.14937

The outside facts were gathered by two research helpers from these pages. I
did not open every page myself. A number taken into a public claim should be
checked against its page first.

## 11. How to repeat

From the worktree's root, with `PYTHONPATH` set to the worktree's six package
folders and both variables pointing at empty folders:

    KAZMA_DATA_DIR=<empty folder> KAZMA_USER_HOME=<empty folder> python docs/audits/evidence/memory_framework_2026_11/probes.py

It calls no model, loads no embedder, writes only to private temporary
databases and the folder named, and takes under a second. With `--real` it
runs probe 4 alone with bge-m3 from the local cache (not in quiet hours).
The benchmark audit's four scripts are described in that audit's last
section.
