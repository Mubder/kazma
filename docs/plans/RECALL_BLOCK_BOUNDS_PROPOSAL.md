# The recall block's bounds: a proposal (2026-11-21)

**Status:** measured, not built. The product is unchanged.

**Where it fits:** `docs/plans/MEMORY_TOP_SCORES_PLAN.md`, W1a ("a budget
by rank"), which waits for W0 (fix the instrument). These numbers come from
the instrument W0 sets out to fix, so they are evidence for W1a's direction,
not its acceptance:

- the 100 questions used so far (dev, in the plan's split);
- no noise run;
- the same model as reader and judge;
- LongMemEval's prompt, not Kazma's turn;
- no memory tools.

At n = 100 a score is about ±10 points at 95 %. The gap from 0.48 to 0.74
is larger than that; the differences among the middle rows are not.

## What was asked

On LongMemEval, 44 of the 51 questions Kazma's recall block missed had the
answer's round shown but cut by the block's bounds. The question was which
bounds keep the answer without growing the block past its budget.

The bounds in question:

- `limits.RECALL_SHOW_QUESTION` (200), `RECALL_SHOW_ANSWER` (300) and
  `RECALL_SHOW_ONE` (500): how much of each recalled conversation is shown.
- `limits.LIST_SHOW_*` (120/160/240): the same, for a list answer (a
  "how many" question, up to `LIST_LIMIT` memories).
- `recall._SUMMARY_SHOW_CHARS` (900): how much of a weekly summary.
- `format_recall_block(max_tokens=1500)`: the block's budget. Past it, whole
  lines are left out.

## How it was measured

`scripts/recall_block_bounds.py` asks the same recalls with other bounds.
No embedding model is loaded (recorded vectors only). Evidence:
`docs/audits/evidence/recall_block_bounds/`.

- **LongMemEval, the fixed sample of 100 questions** (`longmemeval`): the
  block's size, the lines the budget left out, and how often an answer
  written word for word in the shown rounds stays in the block (24
  questions qualify; a bare number such as a count does not, since it is in
  most rounds). With `--answer`, LongMemEval's own reader and judge prompts
  (deepseek-flash) score each variant's block end to end.
- **The retrieval benchmark** (`main`): the block's size and the answering
  memories shown in part. Its conversations are all shorter than today's
  bounds, so only the summary cut and the block's total size are measured
  there.

## What was found

| Variant (each recalled conversation) | Answered | Kept / 24 | Block, median chars | Lines left out |
|---|---|---|---|---|
| Today: 200 + 300 | 0.48 | 14 | 3,926 | 0 |
| 1.5 times today | 0.55 | 18 | 4,293 | 0 |
| Twice today | 0.58 | 19 | 4,392 | 0 |
| Three times today | 0.57 | 22 | 4,559 | 15 |
| Today's size, cut around the question's words | 0.56 | 18 | 3,926 | 0 |
| Each as wide as the budget allows ("fit") | 0.59 | 23 | 4,979 | 0 |
| Fit, the budget 2,500 tokens | 0.59 | 23 | 5,221 | 0 |
| Fit, the budget 4,000 tokens | 0.58 | 23 | 5,221 | 0 |
| Every one whole, no budget | 0.59 | 23 | 5,221 | 0 |
| The best at up to 1,500 around the question's words, the others at 500; list answers as today ("lead") | 0.59 | 21 | 4,386 | 0 |
| **The same, list answers too** | **0.74** | **22** | **5,344** | **0** |

Baselines from the same reader: the recalled rounds read whole, outside the
block, 0.77. The whole history, 0.72. The oracle, 0.94.

By question type, today against the proposal (n):

- single-session-user (14): 0.64 → 1.00
- knowledge-update (16): 0.38 → 0.81
- temporal-reasoning (26): 0.54 → 0.77
- multi-session (27): 0.30 → 0.44
- single-session-assistant (11): 0.55 → 0.82
- single-session-preference (6): 0.83 → 1.00

What this says:

1. Showing more of every conversation stops helping at 0.59, and a bigger
   budget adds nothing. Length is not what the reader lacks.
2. What helps is giving the best conversation room, cut around the words
   the question asks about, and keeping the others short.
3. Counting questions gain most when their list lines get the same
   treatment. Focusing the list lines alone, at their own bounds, reaches
   0.57; the lead shape on lists reaches 0.74.
4. Order does not matter: the same lines oldest first score the same (0.59
   whole, 0.59 fit).

On the retrieval benchmark the block's median is 2,630 characters and its
largest 5,335, against a body budget of 6,000. A summary cut at 1,200
instead of 900 shows two answering summaries whole and keeps the largest
block at 5,810. That was measured on today's bounds only, so it is not part
of the proposal.

## The proposal

In `format_recall_block`, once recall has chosen what to show:

1. **The best recalled conversation**: up to 1,500 characters
   (`MEMORY_SHOW_MAX`, what `memory_search` shows). Each long side is cut
   around the stretch holding the most of the question's content words
   (`query_terms.content_terms`, read as recall's word coverage reads them),
   not from its start. A short side gives its room to the other.
2. **The others**: 500 characters each, cut the same way, for a list answer
   too.
3. **The budget stays 1,500 tokens.** When it would leave a line out, the
   others give way first (down to 250 each), then the best one (down to
   500). No line is left out.
4. The note after a cut memory (`more_note`, "memory_read reads the whole
   memory") stays.

Cost: the block's median grows from 3,926 to 5,344 characters (about 350
tokens a turn). Its largest stays inside the budget; that measure is the
body only, without the rules before it.

## What building it needs

Today a conversation's displayed text is made inside recall, with fixed
bounds, on several paths: local, mirror, Postgres-primary, joined
follow-ups, and a speaker's name in a shared space. The block would need
each memory's two sides and the question's words to cut them at render
time. One way to do that:

- Keep the sides already clipped at 1,500 in the hit's metadata.
- Let the block build each line from its share.
- Keep the joined and the speaker-labelled lines as they are made today.

The "Memory used" record shows what the block showed, so it reads the same
lines.

Gates it needs:

- The LongMemEval end-to-end score recorded, with today's shape as the
  negative control.
- No line ever left out (a planted overrun as the control).
- The retrieval benchmark unchanged.
- A test that a long Arabic memory is cut around the Arabic question's
  words. The Arabic set was not measured end to end; the parallel set would
  do it.

## Left open

- **The gap from 0.74 to 0.77 and the framing.** The same rounds in the
  block format score 0.59, read whole outside it 0.77. Most of that gap
  closes with the lead shape. Whether the block's rules preamble or fence
  costs the rest was not measured.
- **One reader.** Every end-to-end number is deepseek-flash as reader and
  judge.
- **Spend** (2026-11-21, deepseek-flash): 2,286 calls, 1,773,583 prompt and
  103,874 completion tokens, across every variant above. Kazma holds no
  price for the model.
