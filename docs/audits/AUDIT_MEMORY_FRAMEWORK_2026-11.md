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
beside the named baselines, and publish the first Arabic memory benchmark. Section 4 gives the order, with a gate, a negative control and a
relative size for each step. The sizes compare the steps with each other;
they are not dates. The parts called measure honestly, say nothing wrong
and forget means gone are under half of the whole and carry most of the
gain and all five measured faults.

**Spaces.** The Spaces plan is right about memory's shape and incomplete
about its detail. Section 5 lists what it must add. Eleven items belong to
**S1-09** itself, because S1-09 makes the space the memory tenant and
several memory paths today either take the tenant from the retired mode, run
with no tenant at all, or read every tenant when asked as `default`. They
are S1-09's checklist, and the main session has sent them to its builder.
Seven more must be in place **before S1-11**. Memory "shared by topic" (N19)
is decided by the owner and owned by no plan item.

**Memory runs beside Spaces, not after it.** The owner's priority is Spaces.
Nothing here asks Spaces to wait, except this: S1-11 extends a trust gate
that is faulty today (F1, F3), so those fixes have to land first. They are a
small, separate piece of work (part C1 of section 4: no model call, no
prompt change) that a memory session can finish while S1-09 and S1-10 are
being built. If that session starts now, S1-11 starts when Spaces reaches
it, with no wait on memory.

### 1.1 The four decisions needed now

**Decided by the owner, 2026-11-18: yes to all four, as proposed. The other
fifteen go as proposed.** The owner added two things. Losing the live
install's memory would be acceptable ("nothing important out there"), so no
heavy migration care is built for it; existing data must still never be
broken silently. And the memory track runs on Opus, with Fable kept for the
tasks that need it.

| | Decision | Proposal |
|---|---|---|
| A | **Start a memory track now, in its own sessions, beside Spaces.** Its first piece is part C1: what S1-11 needs. | Yes. It carries decisions 8 and 16 (how trust is graded, and a fixed table of names for personal facts). |
| B | **Download LongMemEval for an outside measurement** (decision 7). Its code and its dataset card are MIT. LoCoMo is CC BY-NC 4.0, non-commercial. | LongMemEval: yes. LoCoMo: measure privately at most, and publish nothing from it unless the owner accepts the non-commercial term. No dataset text goes into the repository. |
| C | **One recording budget** (decision 2): 150 askings of the model, 300 at most, made once, after the fixes that change the prompt. | Yes. |
| D | **Rows now, the space inside new ids, no rebuild of the tables' keys** (decision 17). A memory database per space is kept for the sealed version. | Yes. It is already the working rule for S1-09 and needs the owner's word. |


## 2. Findings, ranked by impact

Ranked by how much each can make Kazma tell its user something wrong, keep
what the user took back, or miss what the user said. F1 to F8 are measured.
Each finding ends with a size: a relative unit for comparing one fix with
another, not a date (section 4 says what a unit is).

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

Fix. Trust follows *who is speaking about themself*, not which pass read
the fact, and not merely where the value's words are. "My sister moved to
Rotterdam" holds the word Rotterdam in the user's own words; if the model
wrote `user lives_in rotterdam` from it, a rule that looked only at the
value would replace the user's real home at the user's own trust. So:

1. **A deep-pass fact is the user's own statement only when three things
   hold.** Its subject is the user. Its value is in the user's own words of
   that turn (F3's helper, with quoted and attached text removed, and the
   held-words rule `provenance._holds` that already exists). And the clause
   that holds the value has the speaker as its grammatical subject: "I",
   "we", or in Arabic a first-person verb or pronoun. A possessive is not
   enough: in "my sister moved" the subject is the sister. Kazma finds the
   clause itself. The model is not asked, and the prompt does not change.
   The Arabic quick pass already has this kind of rule (it refuses a
   participle outside the user's own clause), and S1-11 needs the same rule
   to decide which member "I" is, so it is one helper for all three.
2. **Anything else stays an inference**, and an inference still cannot
   replace the user's own statement.
3. **A refusal is no longer silent.** When the gate refuses to replace a
   user's own fact, it records a pending change the user can confirm: "You
   mentioned Rotterdam. Is that your home now?" A strict rule will miss
   some real corrections ("Moved to Rotterdam last month", with no "I");
   this turns each miss into a question.
4. **Every replacement of a user's own fact is visible and can be undone.**
   One line: "Updated: home Paris → Rotterdam", on the turn (a "Memory
   updated" row beside "Memory used") and on the Memory page with Undo. The
   old value is already kept as history, so undo reopens it. The deep pass
   runs after the reply is sent, so the line cannot be inside that reply;
   it is on the turn afterwards and listed for the chat apps
   (`/memory changes`).
5. **"The same value"** is compared after folding case, underscores and
   Arabic forms, and the user's own spelling is the one kept.

How likely is the sister case? In the recordings it did not happen: in the
benchmark's three turns about another person the model filed the fact under
that person each time (`hana`, `نورة`, `lulwa`), and the R3 study found "no
fact about someone else was filed under the speaker" in its twelve speaker
turns. That is 15 turns of one model. One such error would rewrite a
person's home at their own trust, so the rule does not rest on the model
getting it right.

This fix depends on F3's helper. Without it, raising the deep pass's trust
widens F3.

Gate: the tables above as tests through the worker's own path, plus an
update category in the extraction benchmark (state, then correct in free
words, in English, Arabic and mixed; expect exactly one current fact, the
new one, at the user's trust, and a "Memory updated" record). Negative
controls: today's grading refuses the correction; and third-person
sentences ("My sister moved to Rotterdam", "أختي انتقلت إلى روتردام", "My
manager يشتغل في Meta now"), each with a model reply that wrongly files it
under the user, must leave the user's own fact as it is and record no
update. A sentence with no subject must end as a pending change, never as a
silent refusal. Size: 5 to 6, after F3.

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
questions (decision 7). Negative control: the block without dates. Size:
step 1 is 2; step 2 is 4, plus the fresh recording (it changes the prompt,
see decision 2); step 3 is 5.

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
in English and Arabic. Negative control: today's raw message. Size: 3.

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
leaves two current facts. Size: 4 for the table, which is applied to the
names the model returns and changes no prompt, and 4 for the second layer,
which changes the prompt and so lands before the fresh recording.

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
Arabic word in two spellings. Negative control: today's scrub. Size: 4.

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
  under another name. Of nine "wrong" facts, seven were right facts under
  other names and one more was true and not listed.
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
test. Negative control: today's order. Size: 1.

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
Size: 1.

### F9. Recall weakens where a person talks most, and cannot follow a follow-up

**Measured** for the background (2026-11-18, 08:05 to 08:07 +0300, bge-m3,
`evidence/memory_framework_2026_11/dense_topic.py`, results in
`dense_topic.json`); **read** for the rest.

- **The "is this about the question" test uses a window of ranks.** A turn's
  evidence is its similarity above the "background", and the background is
  the mean of meaning ranks 4 to 15 (`recall.py:1324`), with fixed floors.
  The expected failure was a dense topic: when many turns are on one subject,
  ranks 4 to 15 fill with that subject and the background rises to meet the
  best match. **Measured, that is not what happens with distinct turns.**
  Forty different renovation turns among 134 unrelated ones: 10 of 10
  questions were answered at 10, 20 and 40 turns on the subject. The
  background rose only from 0.47 to 0.50, while the answer sat near 0.64.
  Near-identical log lines on one subject ("week 4 of the plan") did lose:
  10 of 10 at 10 and 20 turns, 9 of 10 at 40 and 80. **What the measurement
  found instead is a language gap.** Arabic paraphrase questions about one
  wedding found 5 of 8 at 8, 16 and 30 turns on the subject. The three
  misses had a similarity of 0.36 to 0.44 to their answer, under the rank
  window's background of about 0.40 to 0.42; the median of every turn was
  about 0.31, and against it all three would clear the floor. Kazma already
  met the same shape with weekly summaries (at 97 live summaries, "list me
  all my subscription resets" found none of ten) and moved summaries to a
  median. Turns and facts still use the window, and the measured cost of it
  is Arabic paraphrase, not topic density.
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
  to 0.5 (`recall.py:1259`). **Measured** with the same store, it reorders
  rather than hides: 8 of 8 answers from older chats were shown with and
  without it, and two moved from first to second place. A turn still inside
  the context window, boosted into the block, is still wasted space.
- **The turn is never told recall was incomplete.** The supervisor reads
  only "empty or not" (`graph_supervisor.py:1188`); a degraded or failed
  recall looks like "nothing known". **Reported**: on the Postgres-primary
  path errors become an empty list.

Fix: a median or fixed-quantile background for turns and facts, swept again
on the benchmark with a dense-topic category and Arabic paraphrase
questions (the measured loss); a one-line standalone gist per
turn written by the deep pass (the column and the embedding rule already
exist); two read tools, "open this memory" and "list by subject, name or
dates", with ids in the block; no boost for turns still inside the context
window; a note to the model when recall was incomplete. Whether to also
rewrite a follow-up with a model is decision 14.

Gate: benchmark categories "dense topic" (20 to 100 turns on one subject),
"follow-up", "count and list"; a forced embedder failure through the real
supervisor node shows the note. Negative controls: the rank window; the raw
follow-up. Size: 12.

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
memory; a poisoned assistant text stores nothing. Size: 3.

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
first. Size: 5.

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
extracted. Size: 3. Do it early: it makes every later measurement
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
them; suggest profile lines the user approves (decision 10). Size: 18.
Learning on a signal saves a model call on almost every turn.

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
per week; a line in the daily digest and three alerts. Size: 5.

### F15. Scale

Kazma's own guide states the ceiling: about 8 seconds a question at 170,000
memories, because meaning search compares every row. **Reported**: several
sweeps read every row of every tenant on each 15-minute pass, in one serial
lane with no deadline. I measured no latency of my own. Mem0's paper
measured search at 0.15 to 0.2 s for Mem0 and 0.5 to 0.8 s for Zep, on its
own hardware and stores.

Fix: keep exact search as the truth, and past a size narrow the candidates
first (tenant, dates, keyword hits, an approximate index's top hits) and
re-score them exactly, with a gate that the benchmark's results are equal.
Give each sweep a cursor and a budget. Size: 10. Not urgent for one
operator's store.

### F16. Tenancy faults that exist today

**Measured** (probe 3) and **Read**. They matter once there is a second
tenant, so they are in section 5: a turn's identity has no tenant; two GET
routes read `default` for any caller; a tenant error becomes a junk tenant
that writes proceed under; turn reconcile files a turn under the chat
store's tenant.

### F17. Known misses, left as they are for now

**Measured** (the review of the speaker-rule fix, 2026-11-18).

- **A named workplace with the article is read as a field.** "شغال في البنك
  الأهلي وساكن في جدة" keeps the home and no employer: the Arabic quick pass
  takes "أشتغل في البنك" as "I work in banking" (a field, which it rightly
  keeps nothing from) and does not tell a named bank ("البنك الأهلي") from
  the field. "أشتغل في بنك الخليج" (no article) is kept. The deep pass still
  reads it, at an inference's trust. Unchanged until the quick pass can tell
  a name from a field.
- **The sentence-end rule's decided cost.** A person named in one sentence
  is not carried into the next unless introduced by name, so "صديقتي معلمة.
  ساكنة في جدة" files the second sentence under the user. Accepted once F1's
  visible, undoable updates exist; it is one of F1's second-half cases.


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
2. Finds the right turns (recall at k) on LongMemEval with no model in the
   path (and on LoCoMo privately, whose licence is non-commercial).
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


## 4. The plan to "best", in order, with sizes

**What the sizes are.** Each size is a relative unit, for comparing one
piece of work with another. One unit is roughly what a careful human
builder does in a day, and that is all it means. It is not a date. In this
project an agent session lands a unit much faster than a day, and several
sessions run at once, so the sum of the units says how big the whole is and
nothing about when it is done. My error on any single size is about a
third. "Calls" are model calls, which need the owner's yes each time.

### 4.1 Beside Spaces, not after it

The owner's priority is Spaces. This plan does not ask Spaces to wait.

- **S1-09** needs nothing from this plan first. Section 5.2 is S1-09's own
  checklist: S1-09 covers items 1 to 8, 10 and 11, and item 9 belongs to the
  R6 migration step. The main session has sent it to the S1-09 builder,
  with decision 17's proposal as the working rule (no key rebuilds; the
  space is salted into new ids).
- **S1-11** needs two things from this plan first, and only these: the
  benchmark's edge rule (benchmark audit step 4, size 3) and part C1 below
  (size 14). Both are free of model calls and change no prompt, so neither
  waits for the recording.
- **Everything else** in this plan runs in memory sessions of its own,
  beside Spaces, and touches different files from the Spaces builders' for
  the most part. The shared files are the write path's two modules
  (`belief_extractor.py`, `belief_mutation.py`) and `recall.py`; C1 should
  merge before S1-11 starts on them.

**The earliest S1-11.** If a memory session starts C1 and the edge rule
now, they are a smaller piece of work than S1-09 and S1-10 together, and
S1-11 can start as soon as Spaces reaches it: its date is then set by
Spaces' own order, not by memory. My expectation, from the pace of this
project's recent sessions, is that C1 and the edge rule take a dedicated
session a few days, not weeks. That is an expectation, not a promise, and
it cannot begin before the owner's yes to decision A. If the memory track
does not start until Spaces asks for it, S1-11 waits for all of C1. One
thing in the edge rule needs the embedder (new float vectors), so it runs
outside the quiet hours.

### 4.2 The order

Two rules shape it:

- **Measure before and after.** The benchmark's scoring and lock are fixed
  first, and the new categories are added while they still fail, so every
  later fix shows as a number that moved.
- **Record once.** The fixes that change the deep pass's prompt (the date,
  the previous turn, the subject's current facts, the spelling rule, the
  names offered, retractions, the fenced input) are gathered in part C2.
  Each changes every recorded reply's key. They land together, and the
  fresh recording (decision 2: 150 askings, 300 at most) is made once after
  them. Part C1 is kept free of prompt changes on purpose, so S1-11 does
  not wait for it.

| Part | What | Closes | Gate (and its negative control) | Calls | Size |
|---|---|---|---|---|---|
| **S1-09's checklist** (not a part of this plan) | Section 5.2, items 1 to 8, 10, 11; item 9 with the R6 migration step. | F16 | The Spaces matrix with a victim planted under `default`; a sweep under enforcement with no scope bound fails loudly. Control: today's code reads across. | 0 | in S1-09 |
| **B. Measure honestly** | Benchmark audit steps 1 and 2 (no false passes; score what was kept). Step 4, the edge rule, which S1-11 needs. Step 5, sizes, and step 6, pgvector in CI, with the new categories that fail today: update, temporal, quoted and attached text, short statements, dense topic, follow-up, count and list. The deep pass through the ledger with failover at a declared temperature (F12). The outside retrieval harness (decision 7, first half). | F6, F12 | A lock that cannot keep an unreachable floor; each new category red on today's code. Control: the old scorer calls a kept fact "not found". | 0 | 20 |
| **C1. The trust gate, as S1-11 needs it** | In this order: the user's own words (F3); trust by who is speaking about themself, with visible and undoable updates and pending changes (F1); Kazma's class wins (F7); filler by words (F8); the table of names, applied to the names the model returns (F4, first layer). No prompt change. | F1, F3, F7, F8, F4 (part) | The update, quoted-text and short-statement categories go green on the quick pass and on the existing recording. Controls: each fix switched off turns its category red; the third-person sentences of F1. | 0 | 14 |
| **C2. What the model is told and shown** | Dates shown (F2 step 1); the date and the previous turn given to the extractor (F2 step 2); the subject's current facts and "replaces" (F4, second layer); the user's spelling asked for; retractions; swarm results and the fenced deep-pass input (F10). Then the one recording, the extraction rebaseline, and benchmark audit step 7 (names in use, end to end). | F2 (part), F4 (rest), F10 | The temporal category and the model-pass update category go green. Controls: each fix switched off; the block without dates. | 150 (300 at most), plus 150 for step 7 | 17 |
| **D. Forget means gone** | The tombstone keeps nothing readable; `forget_mentions` complete and journaled; the journal covers every forget; the horizon lists every copy. | F5, F11 | The planted-word scan with the word in subject, predicate and value, after every kind of forget, and after a restore. Control: today's scrub. | 0 | 9 |
| **E. Against the world** | LongMemEval-S end to end on a sample, with full-context and oracle baselines on the same reader. The published poisoning attacks run against Kazma. The first parallel English and Arabic memory set. Results on the benchmarks page. | F6 | The page is generated from the result files, like today's. Control: a run without its baselines is refused by the page generator. | about 600 for 100 questions with both baselines | 15 |
| **F. Recall** | Time words in the question (F2 step 3); median background; the gist per turn; ids, "open" and "list" tools; no boost inside the window; the "recall was incomplete" note. | F2 (rest), F9 | Dense-topic, follow-up, count-and-list categories green; outside recall at k not lower. Controls: the rank window; the raw follow-up. | 0 for the path with no model | 17 |
| **G. Consolidation** | Lessons on a signal, merged; tool tallies no longer injected; monthly and per-project roll-ups with named sources; suggested profile lines. | F13 | Twelve routine turns keep a correction; a forget empties a roll-up built from it; nothing joins About me without the user's yes. Control: the 12-slot queue. | one per roll-up, in the background | 18 |
| **H. Running it** | A recall record per turn and the quality signals; a "what Kazma knows about me" page with source, date, history and inline correction; cursors and budgets for the sweeps; candidate narrowing with exact re-scoring past a size; the benchmark audit's step 8. | F14, F15 | A week of failed extraction pages once; equal benchmark results with narrowing on. Control: narrowing without re-scoring changes results. | 0 | 20 |

About 130 units in all. Parts B, C1, C2 and D are 60 of them, carry most of
the gain and close all five measured faults. D, G and H do not depend on C
and can run in other sessions beside it. B's benchmark work and C1 can run
in two sessions at once: they touch different files.

### 4.3 Status of part C2

Added as each piece lands.

- **Dates shown (F2 step 1), 2026-11-20: done.** Every recalled turn
  carries its day, every fact the month it became true, and a single-valued
  fact the value it replaced with its months, in the operator's zone
  (`recall._date_hits`). Probe 4's shape, seeded with Paris in May and
  London in October, now reads "[since Oct 2026] user lives in London;
  before that: Paris (May 2026 to Oct 2026)" and "[28 May 2026] User: I
  live in Paris". Gate: `tests/test_memory_recall_dates.py`; control:
  recall without the dating step.
- **Before the prompt changes, 2026-11-20: a declared change waits for its
  recording.** `lock --prompt-changed "why"` names each change in the lock;
  until the recording the model-pass rows replay the old recording through
  today's code (a drift check) and the benchmarks page says they do not
  measure the new prompt. The product and the benchmark build the deep
  pass's message with one function, and a recording names its message form.
  Gate: `tests/test_memory_pending_recording.py`.
- **The date and the previous turn given to the extractor (F2 step 2),
  2026-11-20: built, not yet recorded.** The memory worker gives the deep
  pass the turn's day in the operator's zone and the chat's remembered turn
  before it (context only); the model gives a fact's start (`since`),
  which Kazma keeps beside the fact when it is a real date no later than
  the turn, never in place of when it was said; recall shows it. The write
  scenarios gain a `temporal` category (four, English and Arabic, passing
  on written replies). What the model does with the new message is
  measured by the recording. Gate: `tests/test_memory_extractor_context.py`.
- **The subject's current facts and "replaces" (F4, second layer),
  2026-11-20: built, not yet recorded.** The deep pass is shown the facts
  held about the turn's people and things, numbered, and may say which one
  a new fact replaces; Kazma takes it only for the same subject and a fact
  still current, and the trust gate still decides. "hana occupation pilot"
  then "hana works_as captain, replaces f1" leaves Hana a captain, not
  both. The write scenarios gain a `replaces` category (six, English and
  Arabic, passing on written replies). Gate: `tests/test_memory_held_facts.py`.
- **Retractions, 2026-11-20: built, not yet recorded.** The deep pass may
  say a fact stopped being true ("I stopped", "no longer"); Kazma ends it
  instead of writing it. The model's own reading ends at once; a fact the
  user stated is asked about first ("Has this ended?", only when the value
  stands in the user's own words with no one else named), and "Yes, it
  ended" records an ending the user can undo. The quick pass reads no
  endings. The write scenarios gain a `retraction` category (seven, English
  and Arabic). Gates: `tests/test_memory_retractions.py`, the browser test
  of the change list.
- **The fenced deep-pass input (F10, its second half), 2026-11-20: built,
  not yet recorded.** The turn before, the facts held and the turn itself
  travel inside one data fence; a fact carrying an injection payload or a
  fence marker is refused; a value only the reply holds is Kazma's reading
  when recalled. A poisoned reply stores nothing. Gate:
  `tests/test_memory_deep_pass_fenced.py`.
- **The user's spelling asked for (F1's third fault), 2026-11-20: built, not
  yet recorded.** The prompt asks for an object as the user wrote it, and
  Kazma keeps the user's spelling whatever the model writes: the words of
  the message that are the same value, folded. "kuwait_city" from "We moved
  to Kuwait City" is stored "Kuwait City", still the model's reading. The
  old recording, replayed through it, scores the same. Gate:
  `tests/test_memory_users_spelling.py`.
- **Swarm results (F10, its first half), 2026-11-20: done.** A swarm
  task's result names the chat that started it and its space; a chat kept
  out of memory stores nothing from its tasks, and forgetting the chat
  takes them. No prompt change. Gate:
  `tests/test_swarm_results_follow_their_chat.py`. Not done: the weekly
  summary still groups a swarm result with the user's turns (reported, not
  read).
- **The recording's machinery (benchmark audit R2), 2026-11-20: built, no
  call made.** A recording holds five askings a turn; every asking is
  scored, a score is the total over them, and its spread is kept in the
  lock. The recorder asks through the product's own call, saves after every
  reply, goes on where it stopped and never past its call limit; a new
  recording is locked only with five askings a turn and finished. Gates:
  `tests/test_memory_extraction_record.py` and the askings cases in
  `tests/test_memory_benchmark.py`.
- **What the recording asks (extraction dataset v3), 2026-11-20: built, not
  yet recorded.** Ten turns, English and Arabic, ask what C2 changed: a
  start from the turn's day, the turn before as context only, a held fact
  replaced, a fact ended, planted text kept out. The model's pass scores
  each (`dated`, `replaced`, `ended`); until the recording they wait, named.
  The quick pass finds less on them (it reads no dates or held facts) and
  keeps nothing it should not. Gate: `tests/test_memory_benchmark_c2_turns.py`.
- **The recording, 2026-11-20: made and locked** (the owner's yes;
  deepseek-flash; 40 turns, 5 askings each, 200 calls; 307,945 prompt
  tokens, 259,712 of them cache hits, and 31,740 completion tokens, 21,916
  of them reasoning, as DeepSeek counted them; Kazma holds no price for the
  model). On the 30 turns the old recording answered, the new prompt keeps
  every fact in English and the mixed turns and 96.4 % in Arabic (two of
  five askings of a06 kept Noura's job but not that she is the user's
  sister); "same name" rises (English 0.77 to 0.80, Arabic 0.91 to 0.92);
  nothing kept is wrong in English and Arabic. On the ten C2 turns, every
  fact is found, every held fact replaced and every ending read, in both
  languages; nothing is taken from the turn before or from the planted
  text; one start in ten is a month late (r01's first asking: "last
  month" said on 31 May given as May). The write scenarios all still pass.
  The declared prompt changes end with it.

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

- **S1-09's own checklist**: eleven items (5.2). Without them S1-09 either
  writes a turn's memory under the wrong tenant, fails every background
  sweep, or leaves paths that read every space. S1-09 covers items 1 to 8,
  10 and 11; item 9 belongs to the R6 migration step. The main session has
  sent the list to the S1-09 builder.
- **Before S1-11**: seven items (5.3). The largest is that S1-11 extends a
  trust gate that today refuses a member's own corrections (F1). The first
  two items are part C1 of section 4, which a memory session builds beside
  S1-09 and S1-10.
- **S1-26**: "background sweeps read index columns only" is not true of
  memory, and vectors and the search index are content (5.4).
- **S1-27**: the registry it counts, exports and erases "from" does not list
  memory's outside copies, and memory has no hard erase today (5.5).
- **S1-28**: a memory restore is one file for every space (5.6).
- **N19**, memory shared by topic: decided by the owner, owned by no item
  (5.7).

### 5.2 S1-09's checklist (the space is the memory tenant)

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
| 9 | **The old tenants (R6) are in more places than turns and facts.** The forget ledger and journal, About me, summaries and their periods, entities (each old tenant has its own `user` hub, so a merge into one space needs the entity merge), queue payloads, the mirror, the Neo4j copy. And on an install that used `per_platform` or `per_user`, turn reconcile filed second copies of turns under the chat store's tenant; from S1-09 on it must take a turn's space from the thread's record (S1-07), not from the chat store's row. This item belongs to the R6 migration step. | Read: `memory/turn_reconcile.py:141`. The plan's own step 4 of its migration counts turns and facts. | The count report names every table and copy. Nothing merges until the ledger and journal move with their rows; otherwise forgets stop applying. |
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
| 1 | **F3 and F1 land first** (part C1). S1-11 adds the label `member_stated` and the rule "a member's own words always win" (N34) to the trust gate. | Today the gate refuses a person's own free-form correction, or is undone by the model's spelling (F1), and counts quoted and attached text as the person's own words (F3). In a shared space that becomes: Omar forwards a document about Sara, and Kazma files it as Omar's own statement. F1's rule "the clause's subject is the speaker" is also the rule S1-11 needs to decide which member "I" is, so it is built once, as one helper. |
| 2 | **F4's table of names lands first** (part C1), **or N34 holds only on paper.** | "Never replaces what the member said about themself" is a comparison under one predicate name. With free names, Sara's own "pilot" and Omar's "Sara is a captain" are two current facts under two names, and no rule is ever asked. |
| 3 | **The benchmark's edge rule lands first** (benchmark audit step 4). S1-11's one-member gate is "the locked v5 scores exactly". | One of those scores rests on a question 0.0004 above the floor that flips on another machine (benchmark audit). The R3 study has since dropped its speaker lines from the deep pass's message, because their effect could not be told apart from the model's own variation; so S1-11 changes no prompt and needs no recording of its own. Part C1 changes no prompt either, for the same reason: the locked model scores stay replayable through both. |
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

Nineteen decisions came out of the two audits. **Decided by the owner on
2026-11-18: the four of 6.1 as proposed, and the fifteen of 6.2 as
proposed.** Each is listed with the part of section 4 it belongs to. The
owner's word on live memory: losing it would be acceptable, so its
migration needs no special care, but existing data must never be broken
silently.

### 6.1 Needed now (decided: yes to all four)

| | Decision | Proposal | What a yes starts |
|---|---|---|---|
| **A** | **Start a memory track now, in its own sessions, beside Spaces.** | Yes. First the benchmark's edge rule and part C1, which S1-11 needs; then B, C2 and D. It carries decision 8 (trust follows who is speaking about themself, with every change of a user's own fact visible and undoable) and decision 16 (Kazma owns the names of about forty common personal facts; the list comes to the owner for review when it is drafted). | One memory session beside the Spaces builders. No model calls. |
| **B** (decision 7) | **Download a public benchmark to measure Kazma against the world.** LongMemEval: the code is MIT and the dataset card on Hugging Face (`xiaowu0162/longmemeval-cleaned`) says MIT; its filler chats come from ShareGPT and UltraChat, whose licences the card does not state. LoCoMo: CC BY-NC 4.0 (`LICENSE.txt` in `snap-research/locomo`), non-commercial. | LongMemEval: yes, and it is the one to publish from. LoCoMo: at most a private measurement; publish nothing from it unless the owner accepts the non-commercial term or its authors agree, since KazmaAI is a company. In both cases no dataset text is committed: the data sits in a local cache outside the repository, downloaded on the owner's yes, and result files hold question ids and scores only. | Retrieval through the real `recall()` first: no model call, about an hour of the embedder per 100 questions, outside quiet hours. It gives the "before" number for every fix. The end-to-end run comes in part E and asks again for its calls. |
| **C** (decision 2) | **One recording budget.** | 150 fresh askings of the model, 300 at most, with the owner's yes in the recording session and the read-only rules. Made once, after part C2's prompt changes; recording before them spends the budget twice. The old 36 replies are kept as a drift check. | Nothing yet. It is spent at the end of C2. |
| **D** (decision 17) | **Spaces: how memory's rows are keyed.** | Rows now, with the space salted into new ids and no rebuild of the tables' keys. A memory database set per space folder is planned for the sealed version (N6 kept that open). | Already the working rule for S1-09; it needs the owner's word before S1-09 merges. |

### 6.2 As proposed (decided)

| # | Decision | Proposal | Part |
|---|---|---|---|
| 1 | How the extraction benchmark scores a fact. | Three outcomes: kept, kept under an expected name, unwanted. A "same name each time" score. Entries that must not be kept. | B |
| 3 | A hit that flips between machines. | It stops the lock, with the margin named and an "edge list" as the only exit. Never a threshold nudge. | B, before S1-11 |
| 4 | The held-out questions. | Rename today's set honestly and keep gating it. A fresh set written by another session is the sealed one, out of the per-commit gate, with every look logged. | B |
| 5 | pgvector in CI. | A second service and job, exact against approximate settled first; fail on a named skip. | B |
| 6 | The benchmarks page. | Each model-pass row says how many askings it rests on, generated from the lock. | B |
| 8 | Trust follows who is speaking about themself (F1). | As amended in F1: subject, own words and a first-person clause; refusals become pending changes; every change of a user's own fact is shown and can be undone. | C1 (in decision A) |
| 9 | Publish the first English and Arabic memory benchmark. | Yes. A parallel set, each language on its own, written by a different session than the builders. Nobody has one. | E |
| 10 | A profile Kazma suggests. | Suggestions only: "You said you prefer X. Add to About me?" Built from the user's own statements, never from inferences; nothing binds without a yes. | G |
| 11 | Dates in what the model is shown (F2 step 1). | Yes. A few tokens a line. | C2 |
| 12 | Lessons on a signal, not on every turn (F13). | Yes. It also removes one model call from almost every turn. | G |
| 13 | Stop injecting tool tallies into turns. | Yes. Keep the tallies for health. | G |
| 14 | A model in the recall path (rewrite a follow-up, rerank). Kazma has none today. | Not by default. Build the rewrite with no model, then measure a model rewrite and a local reranker on the outside benchmark as options and decide on the numbers. | F |
| 15 | A forgotten fact keeps nothing a person can read (F5). The Memory page can then say only "a fact was forgotten", not about whom. | Yes. The export already behaves this way. | D |
| 16 | Kazma owns the names of the common personal facts (F4). | Yes; about forty names with their aliases, in English and Arabic, reviewed by the owner. | C1 (in decision A) |
| 18 | Spaces: memory by topic (N19) gets an item and a study. | A study like R3, after S1-11, before anything is built. | after S1-11 |
| 19 | Spaces: erasing a space is a hard delete, journaled; a restore is install-wide. | Yes to both, said in plain words on the delete and restore screens. | S1-27, S1-28 |

One more question is open and not yet a decision: a read-only count on the
live install of how often these faults have already happened (facts under
two names, the user's statements now marked as inferences, corrections the
gate refused). It needs the owner's yes in the session that runs it, and
would say how urgent C1 is for the owner's own memory.


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

All times are Kuwait time (+03:00) on 2026-11-18. The owner's quiet-hours
rule (no tests or measurements from 01:00 to 10:00 except small targeted
ones) was written down at 05:22 and reached this session at about that
minute.

**Stated plainly, because the owner is looking for the cause of the fans:**
before the rule existed, this session loaded the embedding model (bge-m3)
three times between 04:02 and 05:12, and ran the full test suite on eight
cores for 31 minutes, from 04:21 to 04:52.

| When | What | Load |
|---|---|---|
| 03:52 to 04:18 | The benchmark audit's replays and lock demonstrations (its own evidence folder). | One process at below-normal priority; recorded vectors, no model. |
| 04:02 and 04:07 | The real-embedder comparison, run twice. | bge-m3 loaded each time; 460 texts embedded in about 125 seconds, four threads, below-normal priority. |
| 04:21 to 04:52 | The full test suite (`scripts/fast_test.py --cores 8`), started for the benchmark audit's commit and stopped at the main session's request. It never finished. | Eight cores for 31 minutes, at the priority `fast_test.py` gives its own processes. The largest load of the night from this session. |
| about 04:15 to 04:24 | The benchmark audit's commit: metrics regeneration (`pytest --collect-only`) and the three commit gates. | One process, a few minutes. |
| 05:06 to 05:19 | Seven helper agents read code and public web pages. | No tests, no model. |
| 05:12 | Probe 4 with the real embedder (`probes_real_embedder.json`, taken from that run's own output). | bge-m3 loaded once; a handful of texts. |
| 05:19, 05:26, 05:30 | The probes with no embedder (`probes.json`), three runs. | Under one second each, below-normal priority. |
| 05:38 to 06:10 | This audit's commits: the commit gates twice, and one metrics regeneration (`pytest --collect-only`). | One process each, a few minutes, below-normal priority. |
| 08:05 to 08:07 | The dense-topic measurement and the boost's effect (`dense_topic.py`, results in `dense_topic.json`), then probe 4 with the real embedder again (`probes_real_embedder.json`, replacing the 05:12 file; the same result). | One process at a time, below-normal priority; bge-m3 loaded once per run. The owner had lifted the quiet hours. |

After the rule arrived, only the rows from 05:26 on were run; the rest was
reading and writing. The three measurements queued then (the dense topic,
the boost, probe 4 again) ran at 08:05 to 08:07, after the owner lifted the
quiet hours; F9 carries their numbers.

## 9. What was not measured

- **Any outside benchmark.** No LongMemEval or LoCoMo run exists. Every
  statement about where Kazma would score is an expectation.
- **Any model behaviour beyond the recordings.** No model was called. The
  deep pass's behaviour is read from the one recording of 2026-10-15 and the
  R3 study's recordings.
- **F9 on facts**, and the claim that the graph walk cannot change a
  ranking past forty facts: read from the code or reported. F9 on turns and
  the session boost were measured (section 8).
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
  https://github.com/dial481/locomo-audit. Licence: CC BY-NC 4.0, read from
  the repository's `LICENSE.txt` on 2026-11-18. Non-commercial.
- LongMemEval: https://arxiv.org/abs/2410.10813 ·
  https://github.com/xiaowu0162/LongMemEval ·
  https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned ·
  https://xiaowu0162.github.io/longmemeval-v2/. Licence: MIT for the code
  and on the dataset card, read on 2026-11-18. The repository says its
  filler chats come from ShareGPT and UltraChat; the card states no licence
  for those.
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
`dense_topic.py`, beside it, is run the same way: it loads bge-m3 from the
local cache, about four minutes, and `--fake` checks that it runs with a
toy embedder whose numbers mean nothing. The benchmark audit's four scripts
are described in that audit's last section.
