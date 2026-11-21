# The sealed question set for memory recall: specification

**Status: specified 2026-11-20; to be written by another session.** The
memory track built the command that scores it (`memory_bench.py sealed`,
`tests/test_memory_benchmark_sealed.py`); it does not write or read the
questions.

## Why

The recall benchmark's second set of 40 questions was called "held out"
until 2026-11-20. Two decisions were taken by looking at it, and it is gated
in the same lock as the tuning questions, so every change kept is one that
did not lower it (benchmark audit F9, R8). The owner decided (decision 4,
2026-11-18): rename that set honestly and keep gating it (done), and add "a
fresh set written by another session ... the sealed one, out of the
per-commit gate, with every look logged". This is that set.

## Who writes it, and what they may read

A session that has not built or tuned recall, and that runs nothing: no
benchmark, no `recall()`, no embedder, no test. It may read:

- this file;
- `kazma-core/kazma_core/memory/benchmark_data/dataset.json`: the memories
  (`sessions` with their `turns`, `facts`, `summaries`) and the existing
  questions (`questions`, `second`), only to name the memories that answer
  and to avoid repeating a question;
- the builder's descriptions of the categories in `scripts/memory_bench.py`
  (the `QUESTIONS` list shows what each category means; read it for that,
  not to imitate the wording).

It does not read the lock (`tests/fixtures/memory_bench/thresholds.json`),
the edge list, the guide's benchmark report, or any per-question result: a
question written to pass or fail is not a measurement.

## What it covers

96 questions, **8 in each of 12 categories** (the benchmark audit's R6
minimum: a category is locked only with 8 questions or more):

| Category | What it asks | `gold` |
|---|---|---|
| `single` | Something the user said in one turn. | The turn, and the fact extracted from it if there is one: `"p01#2\|f05"`. |
| `paraphrase` | The same kind of thing, asked in words the memory does not use. | As `single`. |
| `assistant` | Something Kazma itself said (an assistant reply), not the user. | The turn. |
| `multi` | An answer that needs two or more memories (two turns, or a turn and a summary). | One entry per piece: `["p03#1\|f09", "p07#2"]`. |
| `update` | A fact that changed: the newest value answers. | The newest; the older value's memories go in `below`. |
| `keyword` | Exact words: a code, a number, a name spelled one way. | As `single`. |
| `noise` | A question with filler around it ("so, um, remind me, what was it that..."), mostly stopwords. | As `single`. |
| `fact` | A fact the extraction stored, asked about directly. | The fact first, its turn as the alternative: `"f12\|p04#1"`. |
| `overview` | A catch-me-up question ("how did training go that month?") a weekly summary answers. | The summary, with the turns it stands for as alternatives: `"sr2\|r02#1"`. |
| `arabic` | Asked in Arabic. Half Gulf (Kuwaiti), half Modern Standard Arabic. | As the category it would otherwise be. |
| `mixed` | Asked in one language about a memory said in the other, or switching language inside the question. | As `single`. |
| `abstain` | Nothing in memory answers, but the question is about a person or thing memory holds (another person's dentist, a car's colour). | `[]`: nothing should be shown. |

Also:

- **Languages.** Besides `arabic` and `mixed`, at least two questions of
  every other category but `keyword` are in Arabic. Gulf first where it is
  natural.
- **No repeats.** No text equal to an existing question once case, spaces
  and punctuation are folded; no question that differs from an existing one
  by a word or two. Ask about memories the existing questions do not, where
  the dataset has them.
- **`below`** names memories that must rank under the answer: the stale
  value of an update, a near miss about another person. It may be empty.
- **Every id exists** in the dataset: a turn is `<session id>#<turn number>`
  (turns counted from 1), a fact is its `id`, a summary is its `id`.

## The file and its format

`tests/fixtures/memory_bench/sealed_questions.json`, UTF-8, LF line endings,
JSON with `indent=1` and `ensure_ascii=False`:

```json
{
 "version": 1,
 "dataset_version": 5,
 "written_by": "<the session's name>",
 "written_on": "YYYY-MM-DD",
 "note": "Written apart from recall's builders, from the dataset and this specification only; never scored by its writer.",
 "questions": [
  {
   "id": "s001",
   "category": "single",
   "lang": "en",
   "text": "What did I name the new kitten?",
   "gold": ["p00#1|f00"],
   "below": [],
   "why": "one line: the memory it is about, and what makes it hard"
  }
 ]
}
```

- `id`: `s001` to `s096`, unique.
- `lang`: `en`, `ar` or `mixed`; for `ar`, also `"dialect": "gulf"` or
  `"msa"`.
- The example above is the shape only; its ids and text are made up.

The writer commits only this file, on a branch from `main`, and says so in
its report. It does not edit the builder, the lock, the tests or any other
file.

## How it is used (built by the memory track)

- **Never in the per-commit gate.** CI checks the file's shape only: the
  counts per category, unique ids, every gold id present in the dataset, no
  repeat of an existing question. It never scores it.
- **Scored only on request:** `python scripts/memory_bench.py sealed --look
  "why"`. A look without a reason is refused. Its texts are embedded with the
  real model the first time (written beside the file as
  `sealed_vectors.npz`), then replayed.
- **Every look is logged** in the lock's `sealed` section: the date, the
  reason, the commit, the file's hash and the scores.
- **A look that decides a change spends the set** (`--decided`): the next
  look is refused until the questions are new (a new version, written by
  another session).
- **The benchmarks page** shows the sealed scores apart, with the number of
  looks and the date of the last one.
- **A new dataset version** (the benchmark audit's step 5 records the
  vectors again and may renumber memories) needs the file checked against
  it, and rewritten by another session if its ids no longer hold. The
  builder checks it: the dataset records the version since which every
  memory id holds with the same text (`ids_hold_since`, anchored to that
  version's own fingerprint, `benchmark.memory_fingerprint`; a memory added
  later says `new_in`), and a set written for that version or later passes.
  Dataset v6 (part F, 2026-11-21) only added memories: a set written for v5
  holds.
- **A question the benchmark comes to ask** fails the shape check (no
  repeat). On 2026-11-21 part F's new temporal question q1304, written
  without the set, turned out to repeat s062: q1304 was dropped from the
  benchmark, and s062's text is now known to the session that builds
  recall. Replacing s062 is the writer's.
