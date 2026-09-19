# TypeSafe sweep of the Python tree — method record

**Date:** 2026-09-19
**Subject:** three System One sweeps (`jev-1.13.0`) over 834 tracked `.py` files, ~121k lines
**Cost:** $1.12 total, 15,586 requests, ~96,000 individual judgments, 0 API errors
**Relationship to `AUDIT_TYPESAFE_VERIFICATION_2026-09-19.md`:** that file records what
the *vendor's* 1338-window report turned out to be. This one records what happened when
we drove the same model ourselves, and — more usefully — what does not work.

---

## Outcome

**Zero vulnerabilities.** Two documentation defects, both fixed:

| defect | commit |
|---|---|
| `save_github_token_to_env` had not written `.env` since audit B8 was fixed; the name and two docstrings still said it did | renamed `bind_github_token_to_process`; `save_token` docstring corrected |
| `GatewayMessage.reply_target` docstring claimed it builds a target "from context_metadata"; it returns `sender_id`, already platform-prefixed | docstring corrected |

Neither was found by the sweep's security questions. The first was found by a
doc/code consistency question; the second likewise. Every security flag that
survived triage was a false positive, hand-verified against the real code.

---

## The three passes

| pass | scope | cost | result |
|---|---|---|---|
| 1. defect sweep | 4,709 AST units × 7 questions | $0.349 | 0 findings after triage |
| 2. context-resolved re-ask | 4,157 units, callee bodies + call sites in state | $0.439 | no improvement (see below) |
| 3. doc/code consistency | 6,354 documented definitions × 5 questions | $0.297 | 2 defects, poor recall |

Calibration ($0.005, 66 units) planted five known-bad and four known-good units.
All five positives fired on the correct axis (0.95–0.99) and the negatives stayed
dark, so the questions do discriminate. The problem is never discrimination.

---

## The measurement that matters: `needs_context` does not move

Pass 1 asked, of every unit, whether judging it required code not shown.
**Median 0.87.** Only 11.7% of units were self-judgeable. This reproduces the
vendor report's R3 (`needs_context ≈ 0.7`) at finer chunking, and higher.

Pass 2 tested the obvious fix — resolve the call graph and re-ask. It does not work:

| context supplied | n | before | after | delta |
|---|---|---|---|---|
| none | 847 | 0.79 | 0.79 | **0.00** |
| 1–2 callees | 949 | 0.82 | 0.82 | **0.00** |
| 3–4 | 819 | 0.86 | 0.87 | +0.01 |
| 5 | 298 | 0.88 | 0.89 | +0.01 |
| 6 (max) | 1,244 | 0.91 | 0.92 | +0.01 |

There is no dose-response. Units handed six callee bodies moved +0.01; units handed
nothing moved 0.00. Enriching the state made the model marginally *less* certain,
which is the documented "large state full of irrelevant detail" failure mode
cancelling any gain.

**Do not retry this.** "Is this code correct" is multi-hop by nature — the
`indirection` failure mode for jev-1.13. One hop does not fix it and more will not.

## What did work: fixing the question, not the state

Pass 1's `authz_gap` wording flagged 15 units at ≥0.85. Rewording it to require a
concrete path *from an unauthenticated caller*, with call sites visible, took that
to **0**. All 15 were wording artifacts. Question precision beat state enrichment
by a wide margin, at no extra cost.

## Where pass 3 failed

A doc/code consistency question is self-contained by construction, so it should have
been the right shape. It half was — it found both real defects — but it cannot be
thresholded:

- The natural positive control (`save_github_token_to_env`, a *known* stale name)
  scored **0.41**.
- Benign abstract stubs (`send`, `deliver`, `send_alert` — body is `pass`) and
  deliberate retirement shims (`create_memory_backup` and siblings) scored
  **0.90–0.97**, because "body is `pass`" literally satisfies "the name names an
  action the body does not perform".
- Filtering those two classes in code leaves 22 candidates at ≥0.85 — triageable —
  but relaxing to ≥0.40 to catch the control yields **1,312** docstring flags.

No usable threshold exists. If this is ever re-run, exclude `pass`/`...`/
`NotImplementedError` bodies and `retired|deprecated` docstrings *in code* first.

---

## False-positive classes, so they are not re-derived

| flagged | score | why it is not a bug |
|---|---|---|
| `migration/path_rewrite.py` PRAGMA f-string | sink 0.93 | `_is_safe_identifier` allowlists at line 234, before the call at 239 — **the same shape as R1 in the vendor report** |
| `kazma_ui/dashboard.py` `clear_all_sessions` | authz 0.91 | `is_sensitive_path` is default-deny for all `/api/*` (audit M1) |
| `backup/restic_repo.py` `ensure_password` | secret 0.84 | `logger.critical` takes the path, never the secret |
| `kazma_ui/app.py` bootstrap | secret 0.90 | logs that a secret was generated, never its value |
| `backup/universal.py` `_rmtree_force` | robustness 0.87 | deliberate Windows workaround; `onerror=` verified still valid on 3.14.7 |
| `migration/exporter.py` COUNT f-string | sink 0.86 | table names come from `sqlite_master`, and are quoted |

---

## Standing conclusion

A System One sweep over this repository is a cheap smoke alarm for self-contained
defects and a good doc-drift detector with a human reading every hit. It is not a
security review, and a clean result from it means "no obvious self-contained defect",
not "safe". Three question shapes were tried for $1.12; the yield was two wrong
sentences and three reusable measurements. Re-running pass 1 on *new* code is cheap
enough to be worth it. Designing a fourth shape is not, absent a new idea.
