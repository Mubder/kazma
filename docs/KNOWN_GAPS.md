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

**9 failures, 0 collection errors** (2026-09-12). Was 21 + 1 collection
error that morning. Twelve were stale tests pinning code that had moved, each
verified against the product before being touched — the product was correct in
all ten and the tests were repaired, not relaxed:

- a stub with the wrong arity (3 tests), a deleted dead symbol still imported
  (2, one of which stopped a whole module collecting), a digest header that
  stopped saying "Kazma", a page message that stopped shouting in caps, an
  image limit that grew a second threshold, an MCP result that is now fenced,
  and a keyword rename the gate still honours as an alias.

The remaining 9 are **triaged but not fixed**, and deliberately so — several
pin real invariants, and relaxing them to get a green board would hide exactly
what they exist to catch:

| Failure | Class | Why it is still open |
|---|---|---|
| 3 × Playwright, 1 × delivery e2e | environmental | needs browser deps in CI |
| `test_chat_steer_composer` (2), `test_turn_delivery_cqrs`, `test_turn_ledger_abc` | brittle by construction | they grep the UI **JavaScript source** for identifiers. The JS was refactored. Whether each is stale or catching a real regression needs someone who knows the current UI — `_awaitingApproval must not appear in first paint` may well be a live invariant |
| `test_audit_wave6::test_half_open_probe_lease_blocks_second_replica` | unknown | circuit breaker opens; fails in isolation too, so not test-order pollution |
| `test_audit_wave7::test_slack_prefetch_fills_bytes_without_token_in_meta` | unknown | attachment arrives with a URL and `data=None` — prefetch not happening, or no token in the test env |

`test_detached_reply_persist` was flagged here as a possible real bug and was
not one. Investigated: the test called the persist helper without
`interrupted=True`, so a cancelled turn looked completed, terminal authority
applied, and the checkpoint won. The real caller has always passed the flag.
Repaired, and a third case added that actually separates the two rules -- a
completed turn whose narration is *longer* than its synthesis -- because both
original cases had the winner also being the longer text and so could not tell
length-wins from terminal-authority.

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
