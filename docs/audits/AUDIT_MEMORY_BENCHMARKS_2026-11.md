# Audit: how memory's quality is measured (2026-11-18)

**Scope.** Every number Kazma keeps about how well memory works: the recall
benchmark (dataset v5, its held-out questions, the recorded vectors, the
ratchet in `tests/fixtures/memory_bench/thresholds.json`), the extraction
benchmark (the quick pass and the model's deep pass), the Postgres-primary
run, the scale bench and the golden eval. For each: what it rests on, how it
can pass when memory got worse, how it can fail when nothing changed, how
likely that is, and how to close it.

**This pass changed nothing.** No product code, benchmark code, fixture or
lock was edited. No language model was asked anything (no call to any
provider). The live install was not read. What was run, all on private
temporary databases at below-normal priority:

- replays of recorded model replies and recorded vectors (the files already
  in the repository, and the 60 replies of the Spaces R3 study, commit
  `2ceb89b7f` on `worktree-spaces-r3-study`);
- the lock's and the gate's own functions on temporary copies;
- the real embedder (bge-m3) from this machine's cache, network off, twice,
  about two minutes each, to compare its scores with the lock.

The scripts and their results are in
`docs/audits/evidence/memory_benchmarks_2026_11/`. Section 8 says how to
repeat each number.

**Words.** *False pass*: the gate is green and memory is worse than the
number says. *False fail*: the gate is red and nothing about memory changed.
*Asking*: one model call for one turn. *Lock*: the scores kept in
`thresholds.json`, which CI compares every run with.

---

## 1. The short answer

The recall benchmark is sound in its mechanics: it repeats exactly, the order
of the questions does not matter, both search paths agree, and the lock's
rules are tested with controls. Its weak points are what the scores rest on:
a handful of questions each, vectors stored rounded, and one answer that
clears a threshold by 0.0004.

The extraction benchmark's deep-pass scores are not sound. They rest on one
asking a turn, and they do not measure what their names say: in every
recording that exists, every fact the benchmark counts as "not found" was
kept by the model, under a predicate name the answer list does not hold. The
lock built on them cannot accept a second recording, and CI cannot see a
change to the prompt they came from.

Findings, ranked by how much they can mislead:

| # | Finding | Misleads how | How sure |
|---|---|---|---|
| F1 | The deep pass's "recall" and "precision" measure which word the model picked for a predicate, not whether it kept the fact | Both ways. The public page says 70 to 91 % of facts found; the recordings show every fact kept | Measured: 20 of 20 "misses" |
| F2 | One asking a turn: the same model, same message, moves Arabic recall from 0.91 to 0.64 | Both ways. A re-record moves the score with no change to anything | Measured on 6 re-asked turns |
| F3 | The extraction lock cannot accept a second recording; the only ways out are editing the lock by hand or recording until a run passes | False fail, then a biased lock | Demonstrated |
| F4 | CI cannot see the extraction prompt: change it and no score moves | False pass | Demonstrated |
| F5 | The recall lock is not the real model's score (2 of 78 differ), and one hit clears its threshold by 0.0004 | False pass on that hit; false fail at the next vector change | Measured |
| F6 | 40 of the 72 per-category scores rest on four questions or fewer; 6 rest on one | Both ways: one question is 25 to 100 points | Counted |
| F7 | The pgvector run never runs in CI, checks one direction only, and its floors disagree with what AGENTS.md says | False pass | Read from code and lock |
| F8 | The deep pass is measured off the product's path, and nothing measures "said, stored, then found" end to end | False pass | Read from code |
| F9 | The held-out questions have been consulted to decide changes, and are locked like the tuning ones | Slow false pass | Read from the guide |
| F10 | The "1,120 turns" of the pool are 241 different turns repeated | Overstates the test | Counted |
| F11 | The golden eval passes a recall that shows every memory whatever was asked | False pass | Demonstrated |
| F12 | The scale bench measures speed only; quality as memory grows is not measured | Blind spot | Read from code |
| F13 | Three ways a gate goes quiet instead of red | False pass | Two demonstrated |
| F14 | The quick pass is exact, but small and has no held-out part | Mild | Counted |
| F15 | The recorded vectors name the model but not its pinned commit | Mild | Read from code |

The recommended order of work is in section 6. The first two steps cost no
model call.

---

## 2. What each measurement rests on

| Measurement | Rests on | Runs in CI | Same result every run | Compared how |
|---|---|---|---|---|
| Recall, tuning questions (89) | `dataset.json` v5, `vectors.npz` (460 vectors, int8), `recall()` | Yes | Yes (verified) | Exact, both ways, 78 scores |
| Recall, held-out questions (40) | The same run | Yes | Yes | Exact, both ways, in the same lock |
| Extraction, quick pass | `extraction.json` v1 (30 turns, 34 facts), the heuristic rules | Yes | Yes | Exact, both ways |
| Extraction, deep pass | The same 30 turns, `extraction_llm.json` (30 replies, one a turn, `deepseek-flash`, 2026-10-15) | Replay only | The replay, yes. The recording, no | Exact against one recording |
| Postgres-primary, local vectors | The recall dataset, a real Postgres mirror, the local vector index | Postgres job | Yes | Overall scores within a stated distance of local |
| Postgres-primary, pgvector | The same, with pgvector's index | No (skips: CI's Postgres has no pgvector) | Not known (approximate index) | Floors, one direction |
| Scale bench | Random vectors, generated text, 1x/10x/100x | No (opt-in) | Timing: no | Seconds under a limit |
| Golden eval | 6 cases, each alone in its own database | 3 cases, words only | Yes | Pass or fail a case |

---

## 3. Findings

### 3.1 F1: the deep pass's scores measure the predicate's name, not the fact

A gold fact is a list of acceptable `(subject, predicate names, value)`. A
reply counts only when its predicate is one of the listed names. The model
names predicates freely, so a kept fact under another name is counted twice
against it: once as a gold fact "not found" (recall), once as a kept fact
that is "wrong" (precision).

Every reply of every recording was read through the product's parser and the
benchmark's matcher (`extraction_replies.py`):

| Recording | Gold facts asked | Counted "not found" | Of those, the value was kept anyway | Not kept at all |
|---|---|---|---|---|
| The locked one (30 turns) | 34 | 7 | 7 | 0 |
| Six turns asked again (R3) | 9 | 3 | 3 | 0 |
| All 30 with two speaker lines (R3) | 34 | 10 | 10 | 0 |

In 77 gold facts asked, the model lost none. What the benchmark calls a miss:

| Turn | Gold | What the model kept |
|---|---|---|
| e03 "I prefer tea over coffee in the mornings." | `user prefers tea` | `user favorite_morning_drink tea` |
| e10 "I love hiking and I use a Garmin watch." | `user prefers hiking`, `user uses Garmin` | `user enjoys_activity hiking`, `user uses_tool garmin_watch` |
| a03 "أحب الشاي بالنعناع." | `user prefers الشاي بالنعناع` | `user favorite_drink الشاي بالنعناع` |
| a04 "عندي حساسية من الفول السوداني." (asked again) | `user allergic_to …` | `user has_allergy الفول السوداني` |
| a06 "أختي نورة طبيبة أسنان." (asked again) | `نورة job …`, `user sister نورة` | `نورة works_as طبيبة أسنان`, `نورة relation_to_user أخت` |
| m05 "My daughter Lulwa تدرس في Bayan School." | `user daughter Lulwa` | `lulwa child_of user` |

The same holds for precision. The locked recording has 9 kept facts counted
wrong. Seven are the facts above. One is true and not on the list
(`user previously_worked_at Ooredoo`, from "moved from Ooredoo"). One is
unwanted (`assistant name_is kazma`, from "Call me Abu Faisal يا Kazma").
Read by a person, 35 or 36 of 37 kept facts are right; the benchmark says 28.

**What it hides.** The thing that does vary is real and matters: the name.
Of 9 facts kept with the same value in both askings of the same message, 3
came back under another name (`allergic_to` then `has_allergy`, `profession`
then `works_as`, `previously_worked_at` then `former_employer`). In the
product a fact under two names is never replaced by its newer value (AGENTS.md
15G, "one fact, one predicate name"). The product's answer to that is the
list of names in use that it sends with each turn; the benchmark sends none
(3.6), so neither the problem nor its cure is measured.

**How it misleads.** The public benchmarks page and the memory guide print
"Model pass: recall 76.9 / 90.9 / 70.0 %, precision 78.6 / 90.9 / 58.3 %"
under "facts found" and "facts found that are right". On the recordings that
exist the first is 100 % and the second is about 95 %. A real loss of facts
by the model would show as a drop of the same size as a change of wording,
and nobody could tell them apart.

**How likely.** Certain: it is every miss in every recording.

### 3.2 F2: one asking a turn

The deep pass asks at the client's configured temperature: 0.7 unless the
owner set another (`_llm_extract` passes none, `LLMConfig.temperature`). Asked
again, the model answers differently. The R3 study asked six turns a second time with the identical
message:

| | Found, first asking | Found, asked again |
|---|---|---|
| e07 (2 gold facts) | 2 | 2 |
| a04 (1) | 1 | 0 |
| a06 (2) | 2 | 0 |
| m05 (2) | 1 | 2 |
| m07 (1) | 1 | 1 |
| m08 (1) | 1 | 1 |

Put those six second replies in place of the first ones and score the
recording again:

| | Locked recording | The same with six second askings | All 30 with speaker lines |
|---|---|---|---|
| English recall / precision | 0.769 / 0.786 | 0.769 / 0.786 | 0.692 / 0.714 |
| Arabic recall / precision | 0.909 / 0.909 | 0.636 / 0.636 | 0.636 / 0.636 |
| Mixed recall / precision | 0.700 / 0.583 | 0.800 / 0.727 | 0.800 / 0.800 |

Arabic recall moves 27 points on six turns. The third column is a slightly
different message (two lines naming the speaker; the R3 study found their
effect no larger than this variation) and lands on the same values.

The repository's own history shows it a third time. The first recording of
2026-10-15, scored with the corrected comparison, gave English 0.69 and mixed
0.80 (commit `feb0c406d`). The recording locked minutes later gave English
0.77 and mixed 0.70. The only change between them was a prompt line about
keeping Arabic script. Mixed recall fell 10 points after a change meant to
help mixed text. That may be the prompt or it may be chance; one asking
cannot say, and it was locked as the measure.

**Size.** From two askings a turn (the locked one and the one with speaker
lines), the spread of one asking is about 5 points for English, 14 for
Arabic and 7 for mixed, as one standard deviation of recall. One gold fact
is 8, 9 and 10 points. This estimate is rough: it rests on two askings, one
of them a slightly different message. Measuring it properly needs new
askings (section 5.2).

**How it misleads.** A re-record after any change moves each score by up to
a few facts with no cause. A real change of the same size cannot be seen.
And the locked recording is, of those that exist, the best Arabic one.

**How likely.** Certain at the next recording.

### 3.3 F3: the lock cannot accept a second recording

`scripts/memory_bench.py::_extraction_floor` keeps, score by score, the
higher of the old lock and today's run. The gate
(`test_extraction_only_improves`) fails when a score is under the lock, and
also when it is over it. There is no trade and no rebaseline for the
extraction scores.

Run on temporary copies with the second recording of 3.2
(`extraction_lock.py`):

1. `lock` writes Arabic 0.909 (from the first recording) beside mixed 0.800
   (from the second). No recording reaches both.
2. The gate on the second recording: "Extraction got worse:
   llm.ar.recall: 0.6364 < 0.9091".
3. Put the first recording back: "Extraction got worse:
   llm.mixed.recall: 0.7 < 0.8".

So the first re-record breaks the gate for good. The ways out are to edit
`thresholds.json` by hand, or to record again and again until one run clears
every score at once. The second way locks the luckiest run, which the next
run will fall under.

A new version of the extraction dataset does not reset it either: the lock
wrote version 2 and kept the old floors (case 4 of the same script).

**How likely.** Certain at the next recording. It has not bitten yet because
the recording has not been made again since 2026-10-15.

### 3.4 F4: CI cannot see the extraction prompt

The replay finds a reply by the hash of the user message alone
(`benchmark._ReplayClient`). The system prompt, `_EXTRACT_SYSTEM`, is not
part of the key, and the recording does not say which prompt it answered.
With the prompt replaced by "Keep nothing", every deep-pass score stayed the
same (case 3 of `extraction_lock.py`). No test names `_EXTRACT_SYSTEM`.

The prompt has not changed since the recording (checked in git), so the lock
is not stale today. But the prompt is the part of the deep pass most likely
to be edited, and an edit passes CI untested while the public page goes on
printing the old numbers. The recording also keeps no date, no provider, and
no count of askings.

**How likely.** Certain at the next prompt edit.

### 3.5 F5: the recall lock rests on rounded vectors, and one hit on 0.0004

`vectors.npz` keeps one byte a number (int8). The model's real vector is
anywhere within half a rounding step of the recorded one.

**The real model, measured.** bge-m3 was loaded from the local cache and the
benchmark run on its own vectors (`recall_real_model.py`):

- each real vector against its recording: cosine 0.9997 at the lowest,
  0.9999 on average;
- the similarity of a pair of texts differs from the recorded one by 0.0005
  on average, 0.0029 at most;
- **2 of the 78 locked scores differ**: `overall.precision` 0.6443 against
  0.6464, `single.precision` 0.6559 against 0.6642. Three questions show one
  more memory than in the replay. Hit rates and MRR are equal.

So the lock is close to the model and not equal to it. Run on real vectors,
the gate would fail with "Recall improved -- lock it in".

**The edge, measured.** The recorded vectors were moved at random inside
their own rounding, 30 times (`recall_replay.py`). That is the size of the
gap between the recording and the model, in other directions:

- every score unchanged in 3 draws of 30; in the others 2 to 11 scores move;
- 12 of the 129 questions change in at least one draw, most of them by one
  memory shown more or less (precision);
- **one hit flips in 7 draws of 30**: question q601, "what is it that I do
  for a living?", in the category "noise" (3 questions). When it flips,
  `noise.hit_rate` goes from 1.0 to 0.667 and `overall.hit_rate` from 0.925
  to 0.895.

Why q601: the fact that answers it has evidence 0.0604, and a fact is shown
from 0.06 (`recall._BELIEF_FLOOR`). It clears the floor by 0.0004, less than
the rounding of the recording. The floor was chosen by a sweep on this
benchmark.

The same two values, 0.667 and 0.895, are the pgvector run's floors for the
same two scores. The guide puts that gap down to "one stopword-heavy
question its keyword search misses". The CI test that uses the same keyword
search with the local vector index scores equal to local. So the vectors,
not the keywords, are the likelier cause: pgvector stores and compares them
slightly differently, and that is enough for q601. This was not run here
(no pgvector database), so it stays "likely".

**Smaller noise is harmless.** At a fiftieth and a thousandth of a rounding
step, the size of a float difference between two machines, nothing moved in
8 draws of 8 each. CI on another CPU is not at risk.

**How it misleads.** The page prints "a question with noise around it:
100 %". That hit depends on the fourth decimal of one similarity. The next
re-record of the vectors (any change to a text, a new model commit) has
about one chance in four of losing it, and then the gate fails on a change
that did nothing, and the lock cannot go down without a trade the rule
forbids (3.6).

**How likely.** The 2-score difference: certain, small. The flip: about one
in four at each vector change.

### 3.6 F6: most scores rest on a few questions

| Questions behind the score | Categories | Locked scores |
|---|---|---|
| 1 | held-out keyword, held-out noise | 6 |
| 2 to 4 | held-out overview (2); multi, noise, and held-out arabic, held-out assistant, held-out fact, held-out single (3); fact, keyword, held-out update (4) | 34 |
| 5 to 8 | held-out paraphrase (5), assistant (6), update, held-out mixed (7), arabic, paraphrase, held-out abstain (8) | 23 |
| 10 to 18 | overview (10), abstain, single (18) | 9 |

40 of the 72 per-category scores rest on four questions or fewer. One
question is 25 to 100 points of such a score.

Two more things follow from the sizes:

- **The overall hit rate is the mean of the categories**, not of the
  questions. The one held-out keyword question weighs as much in
  `heldout.overall.hit_rate` (9 points) as the eight abstention questions
  together.
- **The trade rule cannot be used for a hit.** A trade may lower a score by
  0.05 at most. The largest category has 18 questions, so one question is
  0.056. No trade can ever accept one lost hit anywhere. The lock's `trades`
  list has been empty since the rule was added (2026-10-14), and the guide
  records several changes dropped as "past the trade rule".

**How it misleads.** Both ways. A change that helps five questions and
costs one in a small category is refused, whatever it is worth. And a score
like "held-out keyword 100 %" says one question was answered.

### 3.7 F7: the pgvector run

There are two Postgres-primary measurements under one name.

- `tests/test_memory_pg_primary.py` runs in CI's Postgres job: the mirror's
  keyword search with the **local** vector index. It checks the overall
  scores stay within a stated distance of local (0.01, 0.01, 0.02). AGENTS.md
  15K quotes this one: "hit rate and MRR equal to local".
- `tests/test_memory_benchmark_pg.py` uses **pgvector**. It needs a database
  with the extension. CI's Postgres is the plain `postgres:18` image, so the
  test skips there on every run, and `scripts/postgres_suite.py` does not
  fail on a skip. Its floors in `thresholds.json` (`postgres_primary`) come
  from a run made by hand (last changed 2026-10-14), and 13 of the 78 are
  below local (hit rate 0.895 against 0.925). The public page and the guide
  quote this one.

The pgvector test compares one way only (a drop fails, a rise is ignored),
so its floors can sit below what the code does, which the recall test's own
header warns against. Its index is approximate (HNSW) whenever Postgres
chooses to use it, so two runs need not agree, and its lock uses the same
"keep the higher" rule as 3.3.

**How it misleads.** An install on pgvector could lose recall quality with
every gate green. And two documents state two different results for "the
Postgres path".

### 3.8 F8: off the product's path, and no end-to-end number

The deep-pass benchmark calls `_llm_extract(text, "")` and scores the reply.
The product does more around that call:

- it sends the assistant's reply too, and the lists "Predicates already in
  use" and "Subjects already in use" (the benchmark sends neither);
- it never sends a filler turn: turn m06 ("ok تمام"), the only mixed turn
  with nothing to keep, is skipped by `is_filler_turn` before any call, so
  `llm.mixed.quiet` measures a call the product never makes;
- after the call, a fact passes the confidence filter (0.7), the subject
  rules, and `mutate_belief` with its same-words naming rule. On the locked
  recording the filter removes nothing: all 37 kept facts carry 0.7 or more,
  the unwanted one included.

And the recall benchmark seeds its facts already well named, by the
builder. So no number anywhere answers the question a user would ask: "I
said it; is it found later?" Extraction is scored alone on its raw reply,
recall alone on perfect facts.

### 3.9 F9: the held-out questions

The held-out set was added on 2026-10-14 and described as "never used to
choose a threshold". Two things wear that down:

- The guide records two decisions taken by looking at it (a threshold sweep
  on 2026-10-14, a new signal on 2026-10-15, both dropped because held-out
  abstention stayed at 0). Deciding against a change is also a use.
- Its scores are in the same lock, under the same rule: a change that lowers
  a held-out score fails the build. So every change kept from now on is one
  that did not hurt the held-out set. Over time that fits the code to it.

It also holds 40 questions over 11 categories, five of them with 3 or fewer
(3.6), about the same memories as the tuning questions.

**How likely.** Not misleading today (tuning and held-out agree within a few
points). It erodes with each change.

### 3.10 F10: the pool of unrelated chat

The benchmark describes "a large pool of unrelated chat" and reports
"1,317 memories". The 300 unrelated sessions are drawn with repeats from a
short list: their 1,060 turns are 181 different turns, about six copies of
each. With the 60 turns the questions are about, the 1,120 turns hold **241
different turns** and 134 different user messages. That is why 460 vectors
are enough.

Repeats are not harmless filler. Recall judges a memory against the
background of the question's ranks 4 to 15; with six copies of each
unrelated turn, that background is made of copies. The thresholds were swept
on this pool. Whether they would move on a pool without repeats was not
measured here (it needs new texts and their vectors).

### 3.11 F11: the golden eval

Six cases, two optional. Each case clears the database, writes its own one
to three memories, asks, and passes if the expected word is anywhere in what
came back. Without an embedder three cases run.

A recall replaced by "show every memory, whatever was asked" passes all
three. There is nothing else in the database to show wrongly, and no case
says what must not appear. The file has no negative control.

On the Memory page it is the button "Run golden eval" and reports a pass
rate. It is a smoke test (recall returns something for an obvious question)
with the name of a quality gate.

### 3.12 F12: the scale bench

Opt-in (`KAZMA_TEST_MEMORY_SCALE`), never in CI, on random vectors. It
measures seconds at 1x, 10x and 100x against limits set at about twice one
run on one machine. That is a fair speed check.

What it does not do: measure quality as memory grows. The recall thresholds
were chosen on about 1,300 memories (241 different turns). The live install
held about 1,700 on 2026-10-13 (the scale bench's own note), and grows. Nothing says whether the hit rate or the abstention
rate holds at 10 times the pool.

### 3.13 F13: three ways a gate goes quiet

- **No recording.** With `extraction_llm.json` absent, `lock` writes a floor
  with no deep-pass score in it and the gate passes (case 2 of
  `extraction_lock.py`). Deleting the file removes the gate without a word.
- **No numpy.** `tests/test_memory_benchmark.py` starts with
  `pytest.importorskip("numpy")`. If the import ever fails in CI, every
  memory benchmark gate is skipped, and a skip is green. CI installs numpy
  today.
- **A skipped Postgres test** (3.7).

### 3.14 F14: the quick pass

It is exact and repeats, and its 15 misses of 34 are real (the value is not
kept under any name: relatives, jobs, dates). Three limits:

- 34 facts and 6 quiet turns over three languages. `heuristic.mixed.quiet`
  rests on one turn.
- No held-out part. The Arabic rules were written the day the benchmark
  showed 0.00, with these turns in view. `tests/test_memory_arabic_facts.py`
  checks other sentences, which helps, but they are not scored.
- A value matches when either contains the other. `user prefers "hiking
  and"` counts as right for "hiking".

### 3.15 F15: which model the vectors came from

`vectors.npz` says `BAAI/bge-m3`. The product pins that model to one commit
(`embedder.DEFAULT_MODEL_REVISION`). Nothing ties the recording to the pin:
a change of the pinned commit would leave CI replaying the old model's
vectors. The Knowledge benchmark and the swarm routing benchmark replay
recordings of the same int8 format, so 3.5 and this apply to them too; they
were not measured in this audit.

---

## 4. What holds

Checked in this pass and found sound:

- **The recall replay is exact.** A second run gives the same 78 scores and
  the same result for each of the 129 questions.
- **The order of the questions does not matter**, although recall counts
  accesses: shuffled, and with the held-out set asked first, every result is
  the same.
- **Both search paths agree**: sqlite-vec and the NumPy fallback give the
  same results.
- **The lock equals the replay**: 0 of 78 differ.
- **No clock in the ranking**: recency is a rank among the candidates, not a
  time since now. The scores will not drift with the date.
- **The lock's rules are tested with controls**: it refuses to lower, it
  refuses `--rebaseline` on the same dataset, the trade rule refuses each
  bad shape, the dataset file equals what the builder builds, a recall that
  finds nothing fails the gate.
- **The extraction prompt is the one the recording answered** (no change
  since 2026-10-15).
- **The quick pass's numbers are what they say.**

---

## 5. How to make it bulletproof

### 5.1 The standard

Six rules. Every fix below is one of them applied.

1. **A score measures what its name says.** If a reader would call the fact
   kept, the score counts it kept.
2. **A score comes with its spread.** Where the source varies (a model
   asked), the number is a mean over several askings, and the lock keeps how
   far the mean can move by chance.
3. **The lock tells three cases apart**: the code changed (the recording is
   the same: compare exactly), the recording changed (compare with the
   spread), the dataset changed (a new instrument: start again).
4. **A recording says what it is**: the prompt it answered, the model, the
   provider, the date, the number of askings. CI refuses a recording that
   does not match the code.
5. **Nothing goes quiet.** A missing recording, a skipped gate, a missing
   score is a failure.
6. **Held-out stays held out**, or is called what it has become.

### 5.2 The fixes

Each with the gate that would hold it, the negative control, and the cost.

**R1. Score the deep pass on what it kept (fixes F1).** Three scores a
language in place of two:

- *kept*: the gold fact's value, under the right subject (either side of a
  relation), under any predicate name. This is "facts found".
- *named*: of the facts kept, the share under one name across the askings of
  a turn (and, in R5, the share under a name from the list in use). This is
  the real variable.
- *unwanted*: kept facts that match no gold value and are not on a short
  list of true extras for the turn ("moved from Ooredoo").

Gate: an extractor that renames every predicate keeps *kept* at its value
and loses *named*; one that drops every third fact loses *kept*. Today's
matcher is the control: it cannot tell the two apart. Cost: no model call
(the existing replies are scored again), a new version of the extraction
dataset, a few seconds of CI.

**R2. Several askings a turn (fixes F2).** The recording keeps a list of
replies for each turn. Every asking is scored; a score is the total over all
askings; its spread comes from how much each turn's result differs between
its askings. The recorder saves after every reply, never asks again what it
has, and stops at a call limit it is given.

How many: enough that two standard errors of each language's score are less
than one gold fact. From today's rough estimate that is 10 askings for
Arabic under the old scoring. Under R1 the recordings show no spread in
*kept* at all, so the number should be chosen after R1, on a first set of 5:

- 5 askings for 30 turns is 150. 36 exist (the 30 locked, the 6 asked
  again). **114 new calls** on `deepseek-flash`, about 97,000 prompt and
  26,000 completion tokens (from the R3 study's 849 and 226 a call).
- If a score is still wider than one fact, go to 10: **150 more**.
- The limit to approve: 264 calls. Kazma has no price for this model, so the
  cost is not stated here.

Gates:
- the lock refuses a recording with fewer askings than the standard on any
  turn. Control: today's one-reply file is refused.
- the odd and the even askings of one recording, scored apart, land inside
  the stated spread and do not change the lock. Control: under today's
  one-asking rule the same two halves move the lock (this is 3.2).
- an extractor made worse on purpose (R1's) falls outside the spread.

**R3. Lock rules for a recorded score (fixes F3, F4, F13).** The lock keeps,
for the deep pass: the recording's hash and exact scores, and a reference
(mean and spread) that moves only for a reason.

- Same recording: compare exactly, both ways. This is a code change in the
  parser or the matcher, and there is no chance in it.
- New recording, same prompt and dataset: compare means against the spread.
  Inside it: the reference stays, the new recording's hash and scores are
  written. Below it: refused, unless traded. Above it: the reference rises.
- The prompt's hash differs from the recording's: the gate fails with
  "record again". Control: change one word of the prompt in a test, the gate
  fails; today nothing moves.
- A new extraction dataset version: `lock --rebaseline` covers the
  extraction scores too.
- No recording, or a score missing from the run: failure, not a shorter
  lock.

Cost: no model call. The prompt check and the missing-file check are small
and can land first.

**R4. Vectors as the model made them (fixes F5, F15).** Record float32, not
int8: the file grows from 0.4 MB to under 2 MB and the replay becomes the
model, bit for bit, on the recording machine. Store the model's commit
in the file and compare it with the pin.

Gates:
- the recording's commit equals `DEFAULT_MODEL_REVISION`. Control: change
  the pin in a test, the gate fails.
- a real-model run, marked `real_model`, made before any lock that follows a
  vectors change: its scores equal the lock. Control: today it differs in 2.
- an edge check in `lock`: ten seeded replays with the vectors moved by the
  amount measured here. A hit that flips in any of them stops the lock until
  the question or the ranking is fixed. Scores that move by one memory shown
  are listed in the lock, so a review sees them. Control: today's dataset
  stops on q601.

Cost: no model call. Two minutes of local embedding to record, about one
minute of CPU in `lock`, nothing in CI beyond a file read.

**R5. Measure the product's path (fixes F8).** Two additions that reuse the
recordings of R2:

- the same turns asked with a list of names in use, scoring how often the
  model reuses a listed name (the product's defence against two names):
  150 calls at 5 askings;
- an end-to-end set: a recorded reply is stored through the product's own
  writer, then a question about it goes through `recall()`. One score: said,
  stored, found. No new model call beyond R2's; the vectors of its texts are
  recorded locally.

Also: drop turn m06 from the model's quiet score, or score it as "never
sent".

**R6. Enough questions behind each score (fixes F6).** A category is locked
only with 8 questions or more; smaller ones are reported and pooled. The
overall hit rate is counted over questions, with the per-category mean
beside it. The trade rule is written in questions ("one question in a
category of 12 or more"), not in points. The held-out set grows to at least
5 a category. Cost: about 40 new questions, a new dataset version, vectors
recorded again, one rebaseline. No model call.

Gate: the builder refuses a locked category under the minimum. Control:
today's dataset is refused (multi, noise, fact, keyword).

**R7. The pgvector run (fixes F7).** CI's Postgres job uses an image with
pgvector, so the run happens. The suite fails when a test it names skips.
The comparison goes both ways. The benchmark asks pgvector for an exact
search, or states a distance from local as the other Postgres test does.
The two measurements get two names, and AGENTS.md 15K and the guide say
which is which. Cost: one image line in CI, about a minute of job time.

If pgvector stays out of CI, the alternative is to remove its floors and
the sentence on the public page.

**R8. Held-out (fixes F9).** Either seal it: scored only by an explicit
command, each look written into the lock with its date and reason, new
questions after any change that was decided by it. Or keep it as it is and
change the words: "a second set, not used to sweep thresholds" on the page.

**R9. The pool (fixes F10, F12).** Build the unrelated chat without repeats,
and add one run at several times the pool, so the report can say whether
the scores hold as memory grows. Cost: a few thousand generated turns, their
vectors recorded locally (about 20 minutes of CPU, once), no model call.

**R10. The golden eval (fixes F11).** Give each case twenty unrelated
memories and a "must not show" list, add the control (a recall that shows
everything fails), and call the button what it is. Or retire it in favour of
running the recall benchmark's held-out part with the install's own
embedder.

**R11. No quiet gates (fixes F13).** A plain `import numpy` in the gate
file. The Postgres suite names the tests that must run.

**R12. The public page says what a number rests on.** For a recorded score:
the model, the date, the number of askings and the spread. A deep-pass
number from fewer askings than the standard is not printed.

---

## 6. The order

| Step | What | Fixes | Model calls | Why here |
|---|---|---|---|---|
| 1 | R3's two small checks (the prompt's hash in the recording, no recording is a failure), R11 | F4, F13 | 0 | Stops the false passes that exist today; a day's work |
| 2 | R1: score what was kept | F1 | 0 | Changes what the next recording should measure, so it comes before recording |
| 3 | R2 and the rest of R3: several askings, the lock with a spread | F2, F3 | 114, up to 264 | The owner's yes is needed for the calls |
| 4 | R4: float vectors, the edge check, q601 | F5, F15 | 0 | The recall lock's one live risk |
| 5 | R6: sizes, trade rule in questions | F6 | 0 | Needs a new dataset version; do it with step 4's re-record |
| 6 | R7: pgvector in CI | F7 | 0 | Small, independent |
| 7 | R5: names in use, end to end | F8 | 150 | The first number a user would recognise |
| 8 | R8, R9, R10, R12 | F9 to F12 | 0 | Slower risks |

Steps 1, 2, 4 and 6 are independent of each other. Step 3 needs step 2.
Step 5 is best done with step 4, because both end in recording the vectors
again and one rebaseline.

Until step 3 is done, the public page should not print the Model pass rows
as they are, or should say beside them "one asking a turn; see the audit".

### Status of the steps

Added as each step lands (the owner decided section 7 on 2026-11-18: the
framework audit's section 6.2, decisions 3 to 6).

- **Step 1.** R3's two checks are in (a recording names the prompt it
  answered, and no recording is a failure), and R11's plain `import numpy`
  in the gate file (commit `7a71ef6bc`). The Postgres suite naming the tests
  that must run comes with step 6.
- **Step 2.** Done: the deep pass is scored by value (`found`, `named`,
  `right`, `quiet`), extraction dataset version 2 (commit `7a71ef6bc`).
- **Step 4, 2026-11-20: the edge check is in; the float recording is not.**
  `lock` replays the benchmark in ten seeded draws with every recorded
  number moved inside its rounding (half a step) and refuses when a hit
  flips, naming the question and its margin; the edge list in
  `thresholds.json` is the only exit (decision 3). On today's dataset it
  stops on q601 (it flips in 1 of the 10 draws), which is on the list with
  its margin, 0.0004, and its reason. Ten other questions change what they
  show in some draw; the lock lists them. CI asks the draws again for the
  questions within 0.01 of recall's cut, so a change that brings a question
  to the edge is seen without a lock (`tests/test_memory_benchmark_edges.py`).
  Not done: the float32 recording and the check of the model's commit, which
  need the embedder and belong with step 5's re-record, and the real-model
  run before a lock.
- **Step 6, R7 (and R11's second half), 2026-11-20: built; its first CI run
  measures.** A second CI job runs the tests marked `pgvector` on
  `pgvector/pgvector:0.8.7-pg18` (pinned by digest), beside the plain
  `postgres:18` job, and the Postgres suite names the tests each job exists
  for: one that skips fails the job. Both jobs install numpy (`ci-light`),
  without which the Postgres-primary benchmark skipped on every run. The two
  measurements have two names. The pgvector benchmark asks every question
  with pgvector's own search and with every row compared, prints both, and
  holds the product's to the lock's floors both ways; the floors are the
  2026-10-14 run made by hand, marked unverified, so a difference is
  reported, not failed, until `lock-pgvector` locks them from a named CI
  run. Exact against approximate (decision 5) is settled from those numbers,
  not by default: the product's search is unchanged.
- **Step 8, R8, 2026-11-20: today's set is renamed, and stays gated.** The
  40 questions are the second set everywhere a reader sees them (the
  dataset's key, the lock's `second.*` scores, the benchmarks page, the
  guide and its 2026-10-14 report, which says why) and they stay in the
  per-commit gate (decision 4). A check holds the words: nothing on the
  page, in the guide, the builder or the benchmark's docstring calls a set
  held out unless it names it sealed (`tests/test_memory_benchmark.py`,
  the old words as the negative control). The sealed set itself is to be
  written by another session: `docs/plans/MEMORY_SEALED_SET.md` specifies
  what it covers, the file and the format. What scores it is built: CI
  checks the file's shape only, `memory_bench.py sealed --look "why"` alone
  scores it, every look is logged in the lock, and a deciding look spends
  the set (`tests/test_memory_benchmark_sealed.py`, on a planted set until
  the real one is written).
- **Step 8, R12, 2026-11-20: the page says how many askings each model-pass
  row rests on** (decision 6). The extraction lock counts, per pass and
  language, the turns, the gold facts and the askings, and the page prints
  them in a "Rests on" column: 12, 10 and 8 askings, one a turn. A lock that
  does not count a row refuses the page. The page also prints the edge
  list's hit (q601, step 4). Not done here: the spread beside a recorded
  score, which needs several askings a turn (step 3).

---

## 7. Decisions for the owner

1. **The corrected scoring (R1).** It changes published numbers: on the
   recordings that exist, "facts found" by the model reads 100 % in all
   three languages, and the weakness moves to a new line, "same name each
   time". Approve this as extraction dataset version 2?
2. **The recording budget (R2, R5).** 114 calls now, up to 264, on
   `deepseek-flash` through the live install, read-only; later 150 for the
   names-in-use set. The yes must be given in the session that records.
3. **Edge questions (R4).** Should a hit that flips inside the rounding stop
   the lock (strict, recommended), or only be listed?
4. **Held-out (R8).** Seal it, or reword the page.
5. **pgvector (R7).** Add it to CI, or remove its floors and its sentence
   from the public page. The live install has no pgvector.
6. **The public page until then.** Mark or remove the Model pass rows.

---

## 8. How to repeat the evidence

All four scripts are in `docs/audits/evidence/memory_benchmarks_2026_11/`
and write their result beside themselves. Run them with the checkout's
Python and its packages on the path, and with `KAZMA_DATA_DIR` and
`KAZMA_USER_HOME` set to private empty folders. None calls a provider.

| Script | Sections | Reads | Time |
|---|---|---|---|
| `extraction_replies.py` | 3.1, 3.2, 3.8 | The locked recording; the R3 study's replies | Seconds |
| `extraction_lock.py` | 3.3, 3.4, 3.13 | The same, and the lock's own functions on temporary copies | Seconds |
| `recall_replay.py` | 3.5, 3.6, 3.10, 4 | `vectors.npz`, `dataset.json` | About 10 minutes |
| `recall_real_model.py` | 3.5 | The same, and bge-m3 from the local cache, network off | About 2 minutes |

The two extraction scripts need the R3 study's evidence folder
(`docs/audits/evidence/spaces_r3/` once that branch is merged; before that,
pass `--r3` a copy taken from commit `2ceb89b7f`).

The golden eval check of 3.11 is three lines: patch
`kazma_core.memory.recall.recall` with a function that returns every row of
the case's database, run `run_golden_eval()`, and read `passed`.

**Not measured, and said so above:** the pgvector run (no such database
here), the pool without repeats, quality at several times the pool, the
Knowledge and swarm routing recordings, and the true spread of the deep
pass, which needs the askings of R2.
