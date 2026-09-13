# Provider layer — one plan, front and back

> **All four phases landed 2026-09-13.** What the work actually found is at
> the bottom, under *What shipped*; the plan below is kept as written so the
> predictions can be read against the outcome.

**Goal.** Adding a provider should be filling in a form, not filling in a form
and then discovering over the following weeks which code paths it breaks.

**Written 2026-09-13**, after two provider bugs in one afternoon, both of which
had already happened once to a different vendor.

---

## 1. What is actually wrong

### The health check tests a path the product does not use

`POST /api/providers/{name}/test` queries the provider's **`/models`**
endpoint. The product sends **`/chat/completions`**. Measured against a live
provider with a base URL the normaliser had broken:

```
PRE-FIX  (…/v4/v1)   /models -> 200   /chat/completions -> 404
POST-FIX (…/v4)      /models -> 200   /chat/completions -> 200
```

So the **Test button reported success while every real chat failed**. An
operator configured a paid provider, tested it, saw green, and it had never
worked. This is the single highest-value fix on this page and it is small.

### The preset cannot express what providers actually differ in

`PROVIDER_PRESETS` carries five fields — `name`, `base_url`, `auth_header`,
`models_endpoint`, `docs`. The UI form exposes four — `name`, `base_url`,
`api_key`, `enabled`.

Nothing in either can express:

| difference | how it is handled today | what it cost |
|---|---|---|
| API version in the path | **inferred** — append `/v1` unless it ends in `/v1` | Z.AI serves `/v4`; every call 404'd. Google's `/v1beta` hit the identical bug earlier and was patched with a *hostname exemption* rather than a field |
| which system role is accepted | assumed | `developer` vs `system` — Z.AI rejects the former with `400 Incorrect role information` |
| tool-calling support and dialect | assumed | a tool-count limit silently dropped **every** tool instead of trimming (fixed 2026-09-12) |
| streaming, JSON mode, vision | assumed | unknown until a user hits it |
| context window | assumed | no way to warn before truncation |

Because the schema cannot say it, the knowledge lives as `if` statements:
**~40 vendor mentions in `llm_provider.py`** (`ollama` ×8, `litellm` ×5,
`groq` ×5, `lm-studio` ×4), plus hardcoded hostname exemptions in
`url_utils.py`.

### Nothing tests a provider's behaviour

There is no suite that says *a provider must be able to do these N things*. A
new provider's breakage is found in production, by a user, weeks later.

---

## 2. The shape of the fix

**Declare capabilities; stop inferring them.** One schema, used by the
backend, the conformance suite and the UI form. The `/v1` class of bug becomes
unrepresentable rather than patched per vendor.

```python
"zai": {
    "label": "Z.AI (GLM)",
    "base_url": "https://api.z.ai/api/paas/v4",   # explicit. Never inferred.
    "api_style": "openai",                        # openai | anthropic | bedrock | google
    "system_role": "system",                      # vs "developer"
    "auth": {"header": "Authorization", "scheme": "Bearer"},
    "models_endpoint": "/models",
    "supports": {
        "tools": True,
        "streaming": True,
        "json_mode": False,
        "vision": False,
        "parallel_tool_calls": False,
    },
    "max_context": 128_000,
    "docs": "https://docs.z.ai/",
}
```

---

## 3. Phases

### Phase 1 — conformance suite (do this first)

`tests/test_provider_conformance.py`: one parametrised suite of small probes
against a provider.

| probe | asserts |
|---|---|
| `chat_minimal` | a 5-token completion returns content |
| `system_turn` | the system role is honoured, using the declared `system_role` |
| `tool_call` | a one-tool request returns a well-formed call |
| `streaming` | at least two chunks arrive, if declared |
| `json_mode` | valid JSON returned, if declared |
| `models_list` | the models endpoint parses |

Two modes: **offline** against recorded fixtures (runs in CI, no key), and
**live** with `--provider X` (opt-in, costs pennies).

*Why first:* both of today's bugs would have failed `chat_minimal` at the
moment the provider was added. It is cheap, it is independently useful, and it
tells you whether phases 2 and 3 actually fixed anything.

### Phase 2 — the schema, and deleting the inference

1. Extend `PROVIDER_PRESETS` to the shape above; backfill all 19.
2. `url_utils.normalize_provider_url` stops guessing: if the preset declares a
   `base_url`, it is used verbatim. Inference remains **only** for
   user-entered custom URLs, where guessing is genuinely all there is.
3. Replace the vendor branches in `llm_provider.py` with capability lookups.
4. Delete the Ollama / LiteLLM / Google hostname exemptions — they become data.

Conformance must stay green across all 19 throughout.

### Phase 3 — the UI

**The Test button sends a real completion.** One token, the declared
`system_role`, the model actually selected. Show latency, model echoed back,
and token usage. It must exercise the same path a chat does, or it is theatre.

**Show capabilities, do not hide them.** Tools / streaming / JSON / vision as
badges, context window, API style. A provider that cannot do tools should say
so in the UI rather than failing mid-task.

**Distinguish "reachable" from "usable".** Three states, not two:
`● untested` · `● reachable, chat failed` · `● working`. The middle state is
exactly the one that was invisible.

**Add-provider flow:** pick a preset (capabilities prefilled, read-only) or
choose *Custom* and fill them in, with the conformance probes runnable from
the dialog before saving.

**Error surfaces that name the fix.** `400 Incorrect role information` should
render as *"this provider rejects the `developer` role — set `system_role` to
`system`"*, not as a raw upstream string.

### Phase 4 — consolidate the API surface

`providers.py` serves `/api/providers/*`; the frontend calls
`/api/settings/providers`. Two surfaces for one concept is how they drift.
Pick one, redirect the other, and give the UI a single typed client.

---

## 4. Sizing and order

| phase | size | value |
|---|---|---|
| 1 — conformance suite | half a session | **highest** — catches this bug class at add time |
| 2 — schema + delete inference | one session | removes the root cause |
| 3 — UI | one session | fixes the false-green, which is what an operator actually sees |
| 4 — API consolidation | small | prevents drift |

Do **1** before **2**, so the refactor has a failing test to satisfy rather
than a hope. **3** can land independently of **2** and is the one an operator
will notice.

---

## 5. What this is not

This does not make every provider work. It makes every provider's
*capabilities and failures legible* — declared in one place, verified by one
suite, and shown in the UI as what they are. A provider that genuinely cannot
do tool calls will still not do tool calls; it will simply say so before you
build a workflow on it.

---

## What shipped

| phase | commit | outcome |
|---|---|---|
| 1 — static conformance | `8f6a86fa` | Its first assertion found **Perplexity** broken the same way Google and Z.AI had been: a declared base URL silently extended to `/v1`. Fixed the rule instead of adding a third vendor exemption — a URL Kazma ships is now used verbatim. |
| 1b — live probes | `30aca560` | Four probes per provider. Found **three bugs in the harness itself**, including a 16-token budget that reported a working provider as broken because `glm-5.3` spends reasoning tokens before emitting content. |
| 2 — capability schema | `c9a41fa9` | `api_style`, `system_role`, `supports{}`, `max_context`. `None` means *not verified* and is a first-class value; a `None` becomes a `bool` only by running the live probes. Z.AI promoted from a hand-typed custom entry to a preset. |
| 3 — the UI | `d59b9458` | Test sends a real completion and reports three states. The frontend can finally express *reachable but chat failing*. |
| 4 — consolidation | `a02e3fa1` | One shared probe. **Phase 3's fix had landed in the endpoint the UI does not call** — the duplication cost exactly what this plan predicted, during the refactor meant to fix it. |

### What the plan got right

Ordering conformance first. Phase 1 paid for itself on its first assertion,
before any refactor existed to justify, and every later phase had a failing
test to satisfy rather than a hope.

### What it missed

That the duplicate route would bite *during* the work rather than after it.
The plan listed consolidation last as drift prevention; it was actually a
correctness bug already in flight, and Phase 3 shipped into the wrong endpoint
because of it. Checking which route the frontend calls belonged in Phase 0.

### Still open

- **The ~40 vendor branches in `llm_provider.py` are not deleted yet.** The
  schema that replaces them exists and the URL inference is gone; the
  remaining branches are behavioural and want the live probes run against
  each provider first, so the replacements are measured rather than assumed.
- **Most capabilities are still `None`.** That is the honest state: they
  become booleans by running `scripts/provider_conformance.py --live`
  per provider, which costs pennies and a key. Only `zai` has measured values.
- **The UI does not yet render capability badges.** The data is there;
  the mockup shows the target.
