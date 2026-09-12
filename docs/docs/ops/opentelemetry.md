# OpenTelemetry — Kazma in the dashboard you already have

Kazma emits [OpenTelemetry GenAI spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
for every LLM call and every tool execution. If your organisation already runs
an OTel collector, Kazma's agent traces appear in it with no integration work
and no Kazma-specific exporter, because the attribute names are the
industry-standard ones rather than names we invented.

## Turning it on

Two steps. Install the packages — they are not Kazma dependencies:

```bash
uv pip install opentelemetry-sdk opentelemetry-exporter-otlp
```

Then point it at your collector using the standard OTLP variables:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.internal:4317
export OTEL_SERVICE_NAME=kazma
```

That is all. Kazma installs the exporter itself on the first span, so you do
not need `opentelemetry-instrument` or a changed start command.

`OTEL_EXPORTER_OTLP_PROTOCOL` selects `grpc` (the OTLP default) or
`http/protobuf`. `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and
`OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` take precedence if you set them.

Two rules keep this from being rude:

- **No endpoint, nothing happens.** Kazma will not start exporting merely
  because a package is installed. Setting the endpoint is the opt-in.
- **A provider you installed is never replaced.** If your application already
  configured OpenTelemetry, Kazma detects that and leaves your provider alone —
  its spans join your traces instead of hijacking them.

With neither the packages nor the endpoint, every span helper is a no-op
context manager. The dependency-free stdlib tracer in `swarm/tracing.py` is
unaffected and remains the default path.

:::note Why Kazma configures the exporter itself
Installing the SDK and setting `OTEL_EXPORTER_OTLP_ENDPOINT` does **not**, on
its own, produce spans in any library — until something installs a
`TracerProvider`, the global provider stays `ProxyTracerProvider` and every span
is a `NonRecordingSpan` that goes nowhere. The usual answer is to relaunch under
`opentelemetry-instrument`, which means another package and a different start
command. An earlier draft of this page told you to set the two variables and
stop there; checking it against a real collector showed that would have sent you
to an empty dashboard, so Kazma does the wiring instead.
:::

## What you get

Two span kinds, named per the conventions so they group correctly in a UI that
has never heard of Kazma:

| Span | Kind | Name | Key attributes |
|---|---|---|---|
| LLM call | `CLIENT` | `chat {model}` | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons` |
| Tool execution | `INTERNAL` | `execute_tool {name}` | `gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id` |

A real trace from a live turn:

```
span: 'chat deepseek-flash'        kind=CLIENT
    gen_ai.system            = 'deepseek'
    gen_ai.operation.name    = 'chat'
    gen_ai.usage.input_tokens  = 41370
    gen_ai.usage.output_tokens = 512
span: 'execute_tool shell_exec'    kind=INTERNAL
span: 'chat compound-mini'         kind=CLIENT  status=ERROR  error.type='ValueError'
```

`gen_ai.system` is the **provider**, not the hostname — `deepseek`, `groq`,
`anthropic`, `openrouter`, `ollama`. An unrecognised gateway reports `openai`,
because that is the wire protocol it speaks and that is what the attribute
describes.

A chat span covers the **whole** call including provider-side retries. A caller
who waited forty seconds across three attempts waited forty seconds; a span per
attempt would report three fast calls and hide the latency that was actually
experienced.

Anything outside the specification is namespaced under `kazma.` so it can never
collide with a future convention attribute: `kazma.llm.duration_ms`,
`kazma.llm.tool_calls`.

## What you deliberately do not get

**No prompts. No completions. No tool arguments. No tool results.**

The conventions make message capture opt-in precisely because it is a
data-exfiltration surface. A product whose pitch is *your agent, your box*
should not stream your conversations to a collector by default, so
`gen_ai.input.messages` and `gen_ai.output.messages` are absent — and **there is
no flag that turns them on.** Adding one is a decision with a privacy argument
attached, not a config default, and a test fails if the strings reappear in the
module.

What ships is metadata: model, token counts, finish reasons, latency, tool
names, error types. That is enough for cost attribution, latency budgets and
failure rates, which is what the dashboard is for.

## It cannot break a turn

Telemetry that can fail a request is worse than no telemetry. Span setup is
guarded, annotation is guarded, and a broken exporter costs you a span rather
than an answer.

The one thing the guard must **not** swallow is your own exception. An early
draft wrapped the span body in a broad `except Exception` intending to absorb
failures in the span machinery, and it caught the caller's re-raised error
instead — which would have reported every failed LLM call and every failed tool
as a success. Two tests caught it before it shipped, and four more now pin the
behaviour on both the instrumented and the no-OpenTelemetry path, including
`KeyboardInterrupt`, so it cannot come back quietly.

## Where it is wired

Both chokepoints, so coverage does not depend on call sites remembering:

- `LLMProvider.chat()` → wraps `_chat_inner()`
- `LocalToolRegistry.execute()` → wraps `_execute_inner()`

Every LLM call and every tool execution in Kazma passes through one of those
two, which is the same property the approval gate relies on (see
[Threat model](https://github.com/Mubder/kazma/blob/main/docs/THREAT_MODEL.md)).
