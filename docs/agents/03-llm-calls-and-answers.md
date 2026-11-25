<!-- AGENTS.md, piece 03-llm-calls-and-answers. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 3. LLM Tool Fallback (`kazma-core/kazma_core/llm_provider.py`)
- Some providers (NVIDIA NIM) reject tool definitions with 404 "Function not found"
- The code retries without tools automatically when this is detected
- Never remove the `status_code == 404 and "function" in detail.lower()` branch

**LLM error classification — `transient` flag (do not flatten):**
- `LLMError(..., transient=True/False)` classifies every failure raised by
  `LLMProvider.chat()`. Transient = network (Connect/Timeout/**ReadError**/
  RemoteProtocol) + 429; permanent = 4xx content/schema + UnicodeEncode.
- The supervisor retry loop (`graph_supervisor.py:_call_llm_with_retry`) ONLY
  retries `LLMError` when `transient` is True — permanent errors fail fast.
  `httpx.ReadError` (mid-stream drops) MUST stay transient, or
  "stopped-thinking" forced-finalization returns. The graph is split:
  `graph_builder.py` wires nodes; `graph_supervisor.py` is the supervisor
  LLM call; `graph_tool_worker.py` is HITL + commitment; `graph_respond.py`
  is `respond_node`. Do not "fix retry" inside `graph_builder.py`.
- `friendly_llm_error()` prefixes all messages with `⚠️` and gives an
  actionable hint — never change it to return a bare string, or failures
  get mistaken for normal model replies again.

**Turn-failure surfacing — never synthesize over a broken turn:**
- When the supervisor's LLM call fails after retries, it sets
  `SupervisorState.turn_failed=True` + `error_message` (NOT a fake answer).
- `respond_node` (`graph_respond.py`) checks `state.get("turn_failed")` and
  MUST skip its synthesis LLM call when True — synthesizing a plausible
  answer over a failed turn was the root cause of the "model stopped thinking"
  symptom.
- Keep the `turn_failed` guard in `respond_node` and the `transient` flag
  on `LLMError`; removing either reintroduces silent forced-finalization.

**Bare tool-argument text is ambiguous, never executable** (2026-10-09):
`agent/answer_quality.py` detects a small literal object matching an available
tool's schema for ONE model recheck within the turn budget. The declared
`SupervisorState.tool_argument_rechecks` resets on a fresh turn, not an approval
resume. The reminder is call-local system guidance; it must not replace the
user's active goal/language or accumulate in checkpoints. Retry the original
conversation, discarding the rejected candidate: replaying it as a synthetic
assistant turn loses thinking metadata and strict providers reject it. Never guess a tool
name or execute the parsed text: it may be requested JSON or a dictionary
example. Actual calls still go through the normal tool worker and HITL.
Answer guidance joins source grounding at the LLM boundary (synthesis too).
Gate: `tests/test_answer_quality.py`, including the original fragment without
recovery as a negative control, bilingual reads, requested data and auto-denial.
The terminal reply also wins over a longer prefix-matching stream in
`turn_document.split_stream_and_final` and its JavaScript mirror. A recheck
can stream a JSON object twice; choosing the longer text corrupts the saved
answer even though `done.content` is correct. Gate: terminal reply authority,
durable presentation and browser TurnDocument tests. No final text means a
partial turn keeps its stream; paused narration stays in its own part.
Gateway completion delivers this finalized text without cultural prefix/suffix
rewriting: it can corrupt requested JSON, one-sentence replies and language.
The shared turn runner owns terminal persistence and post-turn memory; do not
re-close or re-merge the completed checkpoint in the gateway. Cultural prompt
guidance and pure-greeting fast paths remain. Gate: `tests/test_gateway_terminal_answer.py`.
The HTTP subscriber persists terminal text before yielding `done` and must
not flush its accumulated tokens on a later cancellation. The pump's final
write alone is insufficient: subscriber flushes can race it. Gate:
`test_sse_chat.test_disconnect_flush_cannot_overwrite_observed_terminal_reply`
(cancellation while awaiting another frame, before and after done).
Active attachment extraction and its working-memory anchor preserve full
paths (`turn_input.py`): a basename is a display name, not a second path to
read. Same-named files in different folders remain separate. Gate:
`tests/test_attachment_path_identity.py` (old basename matcher as control).

**One-paragraph output requests** (`agent/answer_format.py`, 2026-10-09):
`respond_node` applies the check after plan-fence normalization and before
terminal delivery/post-turn memory. Only explicit latest-user EN/AR requests
count; quoted, fenced, negated, conflicting and historical instructions do
not. Plain prose changes paragraph separators only. Code, lists, tables,
blockquotes, JSON and multiline quoted literals remain intact. Headings may
get ONE quiet, tool-free formatting call (15 s, 4096 output tokens, 16000 input
characters), on the turn's resolved provider. Accept only unchanged wording
and order, with exact quoted/inline-code spans; never execute returned tool
calls. Account the call through the existing LLM ledger. Preserve the original
on failed/unsupported repair, and skip ALL formatting on `turn_failed`.
This is a conservative layout safeguard, not arbitrary format compliance.
Gate: `tests/test_answer_format.py` (real graph, old boundary as negative
control, bilingual prose, protected blocks, no extra prose model call,
tool-free retry, streaming silence, deadline, cancellation and ledger).

**Verbatim JSON copies** (`agent/json_echo_format.py`, 2026-10-09):
At the same terminal boundary, direct latest-user EN/AR requests to return
only a supplied JSON object verbatim may lose an enclosing JSON fence and
surrounding prose. Require an exact textual match to the user's literal: no
serialization, guessed values, tools or extra LLM call. Unsupported requests,
conflicting suffixes, quoted/historical instructions, duplicate keys,
non-JSON constants, multiple blocks and changed object text stay untouched.
The failed-turn and assistant-tool-call guards apply before repair. This
targets literal copies, not arbitrary JSON generation or general compliance.
Gate: `tests/test_json_echo_format.py` (original H04-ar fence/prose failure,
EN/AR, real respond boundary, data fidelity and no execution).

**Telegram code delimiters** (`kazma_gateway/telegram_format.py`, 2026-10-09):
Markdown-to-HTML conversion recognizes line-based fences and matching maximal
backtick runs for inline code. A bare ``` in explanatory prose must not steal
the next inline opener and expose underscored names to italic parsing. Preserve
fenced code whitespace and escape content before styling. The shared converter
serves normal chat and swarm output; Slack/Discord keep their original Markdown.
Gate: `tests/test_telegram_format.py` (old matcher as negative control, EN/AR,
mixed delimiters, real gateway preparation and HTML send path).
Web's separate `streaming.js` renderer uses the same inline-run rule and stashes
code before styles, links and images. Gate: `tests/js/test_markdown_render.js`.

**Vision capability routing (`kazma-core/kazma_core/vision_capability.py`):**
- `is_text_only(model)` / `is_vision_capable(model)` classify by allow/deny
  lists (deny wins; unknown models are NOT downgraded — fail-open).
- `analyze_image` (`tools/vision_analyze.py`) uses the active model if
  vision-capable, else auto-selects a configured vision model via
  `get_vision_client(registry)` (one-off client, no active-profile change),
  else returns a clear actionable error BEFORE any API call.
- `build_user_content()` (`gateway/agent_handler/attachments.py`) takes
  `vision_capable` and, for text-only models, downgrades chat images to the
  `[Attached: … — use file_read to open: …]` stub instead of `image_url`
  (text-only providers like DeepSeek reject `image_url` with a 400).

**Strict local chat templates — system messages MUST stay at the head:**
- Local OpenAI-compatible servers with strict Jinja templates (LM Studio /
  llama.cpp Qwen3) return HTTP 400 `System message must be at the beginning`
  when any `role: system`/`developer` message appears AFTER the first user
  message. Kazma injects such notes mid-stream (INTENT ENGINE plan notes,
  iteration budget nudges, mission patches), so checkpointed history
  naturally contains them — a plain reload+send 400s on every turn.
- `hoist_system_messages()` (`llm_provider.py`) is applied to the messages
  payload inside `LLMProvider.chat()` — the single OpenAI-compatible path
  (LM Studio, Ollama, OpenAI, DeepSeek, …) shared by all transports. It
  moves system/developer messages to the head (order preserved, no-op when
  already ordered) and keeps assistant/tool adjacency intact. NEVER remove
  this call from `chat()`.
- New mid-stream system-note injection sites are covered by the hoist — but
  never add a second LLM-call path that bypasses `LLMProvider.chat()`.
- Anthropic/Gemini/Azure/Bedrock have their own `chat()` implementations
  that handle system messages natively — do NOT hoist there.


- **Thinking text is kept out of the answer when it is certainly thinking**
  (2026-11-25, `kazma_core/reasoning_text.py`). Cutting a real answer is worse
  than showing thinking (the owner's rule, 2026-11-26), so only the shapes its
  docstring lists are taken out, whole (`split_reasoning`) and streamed
  (`ThinkFilter`), by every client class: a block at the start whose opening
  tag is alone on its line (or is glued to its first word, its closing tag
  ending a line with text after it); a `</think>` with no opening tag before
  it, outside code, alone on its line and not introduced by a colon, closing a
  sentence at a line's end, or with the same text on both sides; a content
  list's reasoning parts. Every other text is the answer, tags and all: a
  `<think>...</think>` on one line with the answer is shown as it came.
  **What is not so:** thinking with no opening tag (vLLM with no reasoning
  parser) streams to the page until its closing tag, since nothing says what
  it is before; then the rest is held, the streamed part is taken back
  (`StreamDelta.retract`, `on_chat_model_retract`) and neither the saved row,
  its narration nor the final frame holds it; the running turn's own token
  frames, and a surface that reads tokens alone (the CLI), still do. Gate:
  `tests/test_reasoning_is_never_the_answer.py` (a recorded Qwen-shaped
  stream; the old stream as the control; every client's streamed and whole
  text, in nested files too, positional or named).
- **A content filter's refusal is another vendor's to answer**
  (`agent/content_refusal.py`, `llm_provider.content_refusal`): an
  `LLMError` of kind `content_refused`, permanent, asked once (never again
  with no tools), read from the error's own code, type and message (never the
  whole body, which may echo the person's words) or a finish reason with
  nothing in the answer, by every client class. A vendor is the preset's, in
  whatever region and behind the egress proxy
  (`provider_endpoints.vendor_of`; the backup suggestions read it too). `_try_failover_models` and `resilient_chat` take it to the
  first backup of ANOTHER vendor (the address's host), the chat's answer
  begins with a line saying so in the person's own language, once a turn, and
  no alert is raised: nothing is broken. A backup that refuses too does not
  rest. With no such backup the turn says what refused it and what can be
  done, in the person's language. Every other 4xx still fails fast. The final
  synthesis of a turn that ended with no answer text (`graph_respond`) is
  asked of the turn's own client only. Gate:
  `tests/test_content_refusal_failover.py`.
