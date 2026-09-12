# Known gaps

What is weak, unproven, or unfinished in Kazma right now.

`CHANGELOG.md` records what was fixed. This page records what has not been, and
it exists because a security claim is only worth what its author is willing to
say against it. Every entry names the evidence, so a reader can check it rather
than take our word — and so the gap stops being invisible when the person who
found it forgets.

**Reviewed 2026-09-12.** An entry with no date has not been re-checked since.

---

## Prompt injection

**The social-framing defense is written down, not proven.**
The fence's own text now refuses authority claimed from inside the data block —
a system message, the operator, an internal pipeline — because
`live_polite_social` (a note signed "the engineering team" asking for a
verification token in the output) beat the structural defenses entirely, having
nothing to forge. That wording has **not** been shown to change a model's
behaviour. The A/B that motivated it ran on `ollama/qwen2.5:7b`, which complies
with 100% of payloads unfenced and so has almost no instruction hierarchy for a
prose defense to appeal to; the payload still landed. The structural half of the
same change (redacting foreign chat-template tokens) *is* proven, and the two
should not be quoted as one result.
→ `docs/INJECTION.md`, section "2026-09-12b".

**The `poolside/laguna-s-2.1` A/B is unfinished — blocked on quota, not on
code.** A single hardened-fence run measured 83% unfenced against 17% fenced
(delta 67, zero errors), with both payloads the 2026-09-12b hardening targets
defended. That number is **not published**, because there is no matching
pre-hardening run to attribute it to: the model may simply be resistant. The
baseline run returned 84 consecutive `Rate limit exceeded: free` errors and the
harness refused to report anything, which is the NO RESULT guard behaving
correctly — a run of errors is not a run of defended attacks. Re-run both
conditions when the free-tier quota resets.

**The `groq/compound-mini` row predates the current fence.**
42% → 8% was measured before the 2026-09-12b hardening and has not been
re-measured; the key is not on the machine that runs these. The row is labelled
in the table rather than quietly reused, but it is stale.

**One payload beats the fence on every model tested.**
`live_direct_override` still succeeds against `mistral:7b` in both conditions.
It is printed in every run rather than summarised away.

**The live corpus is 14 cases.** Enough to show a delta, not enough to claim
coverage. The offline corpus is 56.

**Nothing here proves a model *obeys* the fence in production.** The benchmark
uses a synthetic task per case. Containment (56/56) is a property of the code;
the live tier measures compliance on a corpus we wrote.

---

## The MCP bridge

**A bus-less approval has no session grant and no YOLO.** One decision, one
tool call — those are properties of a chat thread, and a separate process has
no thread whose later calls could be re-checked against a grant. Working as
intended, but it means an MCP client approving twenty file writes asks twenty
times.

**The watcher heartbeat proves a process is alive, not that a human is.**
A running Kazma instance with nobody at the keyboard still heartbeats, so
danger tools are published and the approval simply times out (and denies). That
is the safe direction, but "someone is watching" is a weaker claim than the
name suggests.

**Verification needs `scripts/mcp_probe.py`.** Asking an agent to describe its
own tool surface does not work — it reports the function list in its prompt,
which is a different thing from the server's `tools/list` response. Three
attempts produced three different wrong numbers before the probe settled it.

---

## The commitment / date guard

**Relative timings are only guarded when the text names a known subject.**
`validate_timing_against_memory(..., require_subject_match=True)` returns
`no_memory` for a reminder that names no subject Kazma has a belief about, so
"remind me in 10 minutes" is unchecked. Deliberate: `memory_beliefs` is every
functional belief the tenant has, unfiltered by topic, so guarding every
relative offset against all of them would refuse ordinary short reminders.
Narrowing the belief set by topic would let this tighten.

**Subject matching is alias-based and will miss.** A belief predicate is
matched by a canonical alias table, a spelled-out form, and a distinctive head
token. `supergrok_heavy_reset` was unmatchable until 2026-09-12 because it ends
in none of the known suffixes. Others like it are presumably still unmatched,
and an unmatched subject silently weakens the scoping.

**Contentless text falls back to comparing against every belief.** "yes" names
no subject but the conversation may still be about one, so the conservative
comparison is kept — which means a genuinely new date far from every stored
date can still be refused after a bare confirmation.

---

## Test baseline

**0 failures** (2026-09-12, `pytest tests/ -n 4`). Was 21 failures + 1
collection error that morning.

Twenty-one were stale tests pinning code that had moved, each verified against
the product before being touched. Four were real product bugs, every one of
them found by chasing a test that looked merely stale:

| Bug | Consequence |
|---|---|
| `kazma mcp` resolved its data dir from the client's CWD | the MCP bridge silently withheld all 55 danger tools, and Kazma's entire data dir could anchor beside an unrelated project |
| `CircuitBreaker.from_dict` clamped a reloaded breaker's age at one cooldown | a tripped breaker could never reach half-open, so it never recovered |
| the cron scheduler never installed the job's tenant | every scheduled turn ran context-less and could not read tenant-scoped secrets — two 09:00 reminders failed with "no usable API key" |
| a Playwright fixture slept 1.5s instead of polling | one slow test left uvicorn unbound and broke two neighbours |

The four e2e failures were **not** environmental, which is what they had been
written off as. Besides the fixture race above: `test_smoke` used Puppeteer's
`arguments[0]` inside a Playwright `page.evaluate` (raises
`ReferenceError` in the page, so the session id was never stored); and
`test_delivery_v2_e2e` had two distinct faults — a fixed session id that
persisted to the real `chat_sessions.db` and accumulated state across runs, and
a pre-set `session.thread_id` that pytest's autouse singleton swaps could leave
stale, so the WS handler minted a random uuid thread and the test emitted into
a thread nobody was listening on. It now discovers the live thread from the
broker, which is what it was always trying to assert.

The four UI-JavaScript tests were investigated rather than left: every
invariant they guard was intact.

A noisy baseline has a cost beyond the failures themselves: proving a *new*
failure is not yours takes a stash-and-compare against the previous commit
every time. That happened three times on 2026-09-12 alone.

---

## Scope

**Single-operator trusted host.** Multi-user, network-exposed and multi-tenant
deployments need work that is listed in `SECURITY.md` and not all done.
Approval is consent, not containment: `shell_exec` after approval is host
power, and `python_exec` is sandboxed only when `KAZMA_CODE_EXEC_DOCKER=force`.

---

## Adding to this page

Add an entry when you find a weakness you are not fixing in the same change,
and delete it when the fix lands — with the evidence that it landed. An entry
with no evidence behind it is worse than no entry.
