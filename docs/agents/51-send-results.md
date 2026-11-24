<!-- AGENTS.md, piece 51-send-results. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 51. A send says whether it was delivered (`kazma_core/tools/send_message.py`, 2026-10-02)

The gateway's send backend (`make_gateway_send_handler`) answered
`sent:<target>` whatever `GatewayManager.send` said, and the callers of the
send API mostly did not read the answer at all. A reminder a platform refused
was logged as delivered, a refused scheduled-post notice skipped the fan-out
that would have reached another chat app, `send_file` told the model "File
sent: ... -> Error: ...", and the document pipeline logged "✓ Delivered".
§33 makes the bus senders say the same thing.

- **The answer is `sent:<target>` or `Error: ...`**: the backend returns the
  error when the platform did not take the message (no adapter, a refusal, a
  timeout). `send_failed(result)` is the one reader. A caller either returns
  the answer (a tool) or reads it with `send_failed`: the cron outbox
  acknowledges a message only on `sent:` and otherwise retries it, then ends
  it `undeliverable` with an alert (§16E; the job was finalized DONE whatever
  the answer until 2026-10-14), the scheduled-post notice falls
  through to the fan-out and says when no chat app took it, the cron denial
  notice warns, `send_file` and the dispatcher tools return the `Error:` (a
  failed tool call, §37).
- **Files are read off the loop and carry their bytes.**
  `send_file_message` resolves the path and reads the file (up to 50 MB) in a
  thread (`_file_attachment`); the backend reads the Telegram auto-attach
  files in one (`_outbound_attachments`). An attachment without `data` is
  dropped with a warning: the backend read a `path` entry with no access
  check, and nothing passed one.
- Gate: `tests/test_send_results_read.py` (every call of the send API in the
  packages returns its answer or reads it with `send_failed`; the HEAD of
  2026-10-02 fails it at seven sites. Behaviour: the backend's answer, the
  reminder, the post notice, the dispatcher tools, `send_file` with no bound
  chat, reads off the loop).
- **A chat-app reply is kept until the platform takes it**
  (`kazma_gateway/chat_outbox.py`, resilience plan F3). Live, 57 Slack
  timeouts, about 65 Discord drops and 15 Telegram failures each lost a reply.
  `GatewayManager.send` records every durable reply (`OutboundMessage.durable`,
  the default) in `chat_outbox.db` as `sending`, tries it at once, and reads
  the adapter's report (`kazma_gateway/send_report.py`): the adapters' send
  loops call `part_delivered()` per accepted part and `part_failed(exc)` per
  failed one, which decides from the exception whether it may have arrived
  (a read timeout, a dropped connection: yes; a refused connection or a
  write that failed: no). Taken -> `sent`. Nothing arrived -> `pending`,
  tried again by the gateway's outbox loop after 30 s doubling to 15 minutes
  for two hours, then `undeliverable` with one alert
  (`chat.reply_undeliverable.<platform>`, never the text). Part arrived
  (`partial`) or may have (`unknown`) -> never sent again: the next reply in
  that chat opens with "an earlier message may not have reached you in full",
  in their language. A row a dead process left `sending` is `unknown` at the
  next start (`recover`). A reply sent more than a minute late says so. A
  settled row keeps no text and is purged after seven days (maintenance
  sweep "chat outbox retention"). **Not durable here:** an interaction's
  answer (`native_reply_id`: its token expires) and
  `make_gateway_send_handler`'s sends (`durable=False`): their callers read
  the answer and keep their own retries (the scheduled jobs' outbox, §16E),
  and two outboxes would deliver twice. Gate: `tests/test_chat_outbox.py` (a
  real process killed before, during and after a send: one delivery or one
  honest state; each adapter's report; the manager without an outbox as the
  negative control).

