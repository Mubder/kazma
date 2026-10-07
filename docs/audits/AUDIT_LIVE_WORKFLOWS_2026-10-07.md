# Live workflow qualification — 2026-10-07

The operator authorized disposable coding tasks, file/MCP isolation checks,
approval/deny/cancel exercises, a guarded restart with a pending approval, and
measurements of real task latency, model usage and UI responsiveness. Human
bilingual semantic qualification remains postponed.

## Reproduced failures

The live install was serving `c96d23d2` (main fixes through PR105). A synthetic
invoice project lived under an existing registered ShipX workspace, outside the
Kazma install. Six unittest cases initially produced four failures.

An initial prompt saying “Do not write outside that repository” matched the
structural global read-only parser. Reads succeeded; edits and test commands
were blocked. This is a conservative parser limitation. The parser and its
audit-only protections remain unchanged; a fresh prompt expressed the allowed
boundary positively and reached the ordinary patch approval card.

Approving that absolute, owned invoice patch through the web UI failed. The
file remained byte-for-byte unchanged. The initial request had pinned the
ShipX workspace in a ContextVar, but the independent approval request did not
restore that context. No workspace was saved in SupervisorState, so a restart
could not recover it either. The tool failure was surfaced rather than replaced
with a fabricated success.

## Fix and regression evidence

The shared turn workspace helper captures the trusted execution root at a new
turn's entry and saves it in the graph checkpoint. UI streaming, UI invoke and
core invoke use it. A Command resume restores the saved root for the execution
scope and resets the caller's context afterwards. New turns capture a fresh
root. Invalid/missing roots and legacy checkpoints without a saved binding
refuse to resume with an actionable public error; no root is guessed from tool
arguments. Manual compaction preserves the checkpoint binding rather than
capturing the caller's current root; it is maintenance, not a fresh user turn.

The regression suite uses the declared SupervisorState and a real SQLite
checkpointer reopened between pause and resume. It exercises streaming and
invoke entries, UI and core resumes, a different caller workspace, replay of a
completed command, concurrent scopes and exception cleanup. The negative
control runs the former unscoped resume and actually writes the decoy workspace
instead of the intended one. Acceptance evidence from the deployed retest is
kept in the operator's ignored `reports/live-workflows-20261007` directory.

## Install limitation

Production creation/activation of a new workspace refused because the live
environment lacks `KAZMA_WORKSPACE_ROOT`. Its confinement check was retained.
The trials use an owned UUID subtree beneath an existing registered workspace
without changing the global active workspace or any live environment file.
This qualifies per-task binding; new workspace provisioning requires the
operator to configure the production root separately.
