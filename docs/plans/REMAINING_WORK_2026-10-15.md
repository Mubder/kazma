# Remaining work, 2026-10-15

The owner's instruction (2026-10-15): close every remaining item and bug,
using the live install to test, so that nothing is left when the work stops.
This page is the one list. Each item names where it came from, what closes it
and where it stands. An item is closed only with its evidence.

Decisions the owner made that day: every decision is taken by the best
industry solution, replacing a dependency where that is what it takes
(downloads approved for that); D6 handoff is a product feature; D8 semantic
routing and the Refiner are fixed, not removed; Q04 (independent review) stays
for the owner's own review; the pre-deploy backup copy was deleted (moved to
the Recycle Bin); kazma.ai allows Cloudflare Web Analytics.

## Code

| Item | Source | What closes it | State |
|---|---|---|---|
| Two cron alert-loss windows | Scheduled soak | An alert outbox in the state change's own transaction, drained after commit; real-process kill regressions | In progress |
| One snapshot step over its cap after a crash | TT-G3 soak | Insert and eviction in one transaction; the soak's allowance removed | Done: `tests/test_time_travel_capture_atomic.py` (real-process kill matrix, the old two transactions as control) |
| AGENTS §18 B migration note | TT-09 | §18 B matches `path_rewrite` / `importer` | Done: `test_agents_18b_names_what_the_import_rewrites` |
| Semantic routing | SW-E10, D8 | One embedding space, explicit vectors, off the loop, keyword fallback said | In progress |
| The pipeline Refiner | SW-E15, D8 | Its output reaches the result; tokens and cost in the ledger | In progress |
| Catch-all autoscaler instance never reused | SQ01 soak | An idle catch-all instance takes the next unmatched task | In progress |
| Handoff end to end | SW-E13, D6 | A shipped worker hands off through a tool; guards intact | In progress |
| Chaos runs | SQ04 | Fault injection per case with the recorded outcome | In progress |
| Knowledge retrieval quality | KQ01 | Versioned EN/AR benchmark, explicit search and auto-inject, ratchet | In progress |
| A crawl seeded with a private address | 2026-10-14 deploy | Refused at admission on every surface, EN/AR | In progress |
| Knowledge deviations (KB-S01, KB-E03, KB-E12) | Knowledge tracker | Each accepted with its reason, or the letter implemented | In progress |
| Dropped Chroma collections leave vectors on disk | KB-Q01 harness | Orphan segment folders removed when nothing holds them | In progress |
| Chroma cannot read a small collection after a sweep | KB-Q01 harness | Root cause fixed (a newer chromadb, persistence settings or recovery) | In progress |
| A deleted library's words stay in the FTS index | KB-Q01 harness | Purged after a library delete | In progress |
| Coverage reported before a restored store has vectors | KB-Q01 harness | Health says unknown/rebuilding until measured | In progress |
| Meaning recall at default HNSW settings | KB-Q01 harness | Tuned HNSW parameters, measured | In progress |
| Entities keyed by (tenant_id, id) | M21 | Composite key, every statement, a migration | In progress |
| Tool-written facts still labelled the user's | M03 | Relabel tool; run on live after review | In progress |
| Held-out abstention 0/8 | M24 | One principled signal measured under the trade rule, or the attempt recorded | In progress |
| Arabic-Indic digits under OCR | Q02, D2 | The best local engine measured and integrated, Tesseract as fallback | In progress |
| Web chat has no wall-clock turn budget | Full audit 2026-09-30 | The web honours the same budget setting as the chat apps | Queued |
| Temporal durable path | SQ03, SW-E16, D9 | Time-skipping test server (download approved), Temporal timeouts | Queued (after the handoff/chaos merge) |
| Model-pass extraction recording | M24 | `scripts/memory_bench.py extraction --record`, then `lock` | Queued (after the Memory merge) |

## Measurements (soaks)

| Soak | Started | Ends | Report |
|---|---|---|---|
| Memory (72 h) | 2026-10-14 20:22 UTC | 2026-10-17 | `C:\Users\balfa\kazma-soak\2026-10-14\memory\report.json` |
| Documents Q03 (48 h) | 2026-10-14 20:22 UTC | 2026-10-16 | `...\2026-10-14\documents\report.json` |
| Swarm SQ01 (24 h) | 2026-10-14 21:36 UTC | 2026-10-15 | `...\2026-10-15\swarm\run\report.json` |
| Scheduled Stage 5 (7 fake days) | 2026-10-14 | passed | `...\2026-10-15\scheduled\run\report.json` |
| Scheduled (28 fake days, 8,000 jobs) | 2026-10-14 | running | `...\2026-10-15\scheduled-28d\run\report.json` |
| Time Travel TT-G3 (72 h) | 2026-10-14 22:08 UTC | 2026-10-17 | `...\2026-10-15\time-travel\run\report.json` |
| Knowledge KB-Q01 (100k, 1M, multi-day) | after its defects are fixed | | |

SQ05 (published limits) is tied to the Swarm soak's numbers when it ends.

## Owner-held

| Item | Why it is the owner's |
|---|---|
| Q04 independent security review | The owner reviews it later (decided 2026-10-15) |
| GitHub Actions billing lock | The account's payment; no CI runs until it is fixed |
| Cloudflare Web Analytics switch | The site allows it and the policy says so (kazma.ai `f2d7564`, served 2026-10-15 with a clean console in EN and AR), but Cloudflare injects no beacon today: Web Analytics is off for the project. Turning it on (Cloudflare dashboard: Web Analytics, automatic setup for kazma.ai) is the owner's |

## Done today

- kazma.ai allows Cloudflare Web Analytics; the privacy policy (EN, AR) says
  what it collects (`Mubder/KazmaAI` `f2d7564`).
- The pre-deploy backup copy (176 MB, with a vault copy) moved to the
  Recycle Bin.
