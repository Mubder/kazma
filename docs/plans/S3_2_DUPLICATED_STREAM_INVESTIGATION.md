# S3-2 — the duplicated, truncated stream: REPRODUCED and FIXED

Incident symptom (2026-08-30 19:20): the reply arrived as
`The proposal turn is The proposal turn is` and ended mid-sentence.

Status: **reproduced 2026-08-30 (same day) against the then-current code**
(`tests/test_s32_stream_duplication.py` — two tests failed pre-fix with the
exact incident string), then **fixed at all three sites**. Per the plan's
rule ("a fix lands only once reproduced") the reproduction ran first; the
pre-fix failures are quoted below.

## Root cause class: a second LLM attempt re-emits from token 0 while the
## first attempt's partial deltas are already on the user's screen

The SSE/WS bubble **appends** every `on_chat_model_stream` delta; only
`turn_complete`/`done` has replace semantics. Nothing between the LLM
transport and the bubble discards a dead attempt's partial tokens before a
new attempt begins streaming. When a stream dies mid-generation and any
recovery path restarts generation, the user sees:

```
<partial text of the dead attempt><full text of the surviving attempt>
```

An identical opening fragment ("The proposal turn is …") is exactly what a
model re-emits when it restarts the same narration — hence the doubled
prefix. The mid-sentence END is the surviving attempt dying too, or the turn
failing after partial paint (the `turn_failed` honest-error path closes the
stream where it died).

## The three sites

| # | Site | Path |
|---|------|------|
| R1 | **Supervisor primary retries** — the plainest path; needs no gateway and no failover config. `_call_llm_with_retry` retries `invoke_llm_chat` on transient network errors; attempt 2 re-enters `chat_stream` from token 0. | `agent/graph_supervisor.py` `_call_llm_with_retry` |
| R2 | **Supervisor failover chain** — failover models stream their full answer after the primary's partials. | `agent/graph_supervisor.py` failover loop |
| R3 | **In-provider blocking fallback** — `chat_stream`'s network-error branch (gateway active, `fallback_direct`) awaits blocking `chat()` and re-yielded the complete content as a fresh delta. | `llm_provider.py` `chat_stream` |

## Reproduction (pre-fix failures, verbatim)

- R1: `duplicated prefix reached the delta queue: 'The proposal turn is ready for your review — eight drafts attached.'` — the retry attempt re-streamed the full text after the dead attempt's partials.
- R3: `duplicated prefix in chat_stream deltas: 'The proposal turn is The proposal turn is ready for your review — eight drafts attached.'` — the incident string, character for character, with `LiteLLM stream failed (ReadError) — falling back to blocking chat()` in the captured log proving the branch fired.

Mechanics of the mock: `httpx.MockTransport` serving an SSE body that
yields one content chunk then raises `httpx.ReadError` (R3), and a scripted
`chat_stream` client that dies after a partial delta (R1/R2 shape).

## The fix (three sites, one invariant)

**Invariant: after any delta of a user-visible call has been emitted, no
recovery attempt of that same call may emit content deltas again — the
authoritative full text arrives via the final response + `turn_complete`
backfill, which has replace semantics.**

1. `llm_provider.chat_stream` tracks `_emitted_any`; both blocking-fallback
   branches yield only `StreamDelta(response=resp)` (no content) when the
   dead stream already emitted deltas (R3).
2. `llm_stream.invoke_llm_chat` gains `emit_deltas: bool = True`; when
   False, deltas are consumed but not pushed to the SSE/WS queue (R1/R2).
3. `_call_llm_with_retry` passes `emit_deltas=(attempt == 1)` (R1); the
   failover call passes `emit_deltas=False` (R2).

Live streaming on the healthy path is unchanged (pinned by
`test_default_attempt_still_streams`): first attempts stream exactly as
before; only recovery attempts go quiet.

Locked by `tests/test_s32_stream_duplication.py` (R1 retry, R1 healthy-path
negative control, R3 provider fallback).
