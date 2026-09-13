#!/usr/bin/env python
"""Live provider conformance — does this provider actually do what we assume?

`tests/test_provider_conformance.py` is the static half: it catches what *we*
declared wrong, offline, in CI, on every commit. This is the other half, and it
catches what the *provider* does differently from what we assumed. Both have
bitten:

* **The URL class.** Google, Z.AI and Perplexity each had a version segment
  silently extended to ``/v1``. Every call 404'd. Caught statically now.
* **The behaviour class.** Z.AI rejects the ``developer`` role with
  ``400 Incorrect role information``. Nothing declared it, nothing checked it,
  and it surfaced as "every run failed" for a reason that looked like a key or
  URL problem and was neither.

The probe that matters most is the cheapest one. Kazma's Settings page tested a
provider by querying its **model list**, and the product sends
**chat completions**. Measured on a real provider while the URL bug was live::

    /models            -> 200
    /chat/completions  -> 404

So the UI showed a paid provider as healthy while not one message ever reached
it. A check that does not exercise the path the product uses is not a check.

Usage::

    python scripts/provider_conformance.py                    # plan only, no calls
    python scripts/provider_conformance.py --live             # every provider with a key
    python scripts/provider_conformance.py --live -p deepseek # one provider
    python scripts/provider_conformance.py --live --json out.json

Exits non-zero if any required probe fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for pkg in ("kazma-core", "kazma-ui"):
    sys.path.insert(0, str(REPO / pkg))

PASS, FAIL, SKIP = "pass", "fail", "skip"

#: Token budget for the chat probes. Generous on purpose: a reasoning model
#: spends tokens thinking before it emits any content, so a tight budget
#: returns an EMPTY completion and the probe blames the provider for the
#: probe's own mistake. Measured: glm-5.3 used 16 reasoning tokens before its
#: first content token, so a 16-token cap failed while the provider was fine.
_PROBE_MAX_TOKENS = 160

#: provider -> where its key came from. Printed, because "which key did it
#: actually send" is the question behind every failed run.
KEY_SOURCE: dict[str, str] = {}


@dataclass
class ProbeResult:
    name: str
    status: str
    detail: str = ""
    ms: int | None = None
    required: bool = True


@dataclass
class ProviderReport:
    provider: str
    model: str = ""
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def failed(self) -> list[ProbeResult]:
        return [p for p in self.probes if p.status == FAIL and p.required]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "ok": not self.failed,
            "probes": [vars(p) for p in self.probes],
        }


def _load_env_keys() -> None:
    """Read ``*_API_KEY`` entries from the repo ``.env``.

    Only those keys, and only when this module is run as a script -- an earlier
    harness loaded the whole file at import time and dropped the operator's real
    ``KAZMA_DATABASE_URL`` into the environment of its own test suite.
    """
    import os

    path = REPO / ".env"
    if not path.exists():
        return
    try:
        raw = path.read_bytes()
    except OSError:
        return
    for encoding in ("utf-8", "utf-16", "utf-8-sig"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        return
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key.endswith("_API_KEY") and key not in os.environ and "\x00" not in value:
            os.environ[key] = value


def env_key_for(provider: str) -> str:
    """``Z.AI`` -> ``Z_AI_API_KEY``. Every non-alphanumeric becomes ``_``.

    Replacing only hyphens left a dot in the name, producing ``Z.AI_API_KEY``,
    which no shell can export -- so the environment fallback was unreachable for
    any provider with a dot in its name.
    """
    import re

    return f"{re.sub(r'[^A-Z0-9]', '_', provider.upper())}_API_KEY"


def _client_for(provider: str, model: str | None):
    import os

    from kazma_core.model_registry import get_model_registry
    from kazma_core.runtime.live_llm import key_is_usable

    registry = get_model_registry()
    try:
        client = registry.get_client_by_provider(provider, model=model) if model else \
                 registry.get_client_by_provider(provider)
    except Exception:
        client = None

    config = getattr(client, "config", None) if client else None
    if config is not None and key_is_usable(getattr(config, "api_key", "")):
        KEY_SOURCE[provider] = "registry"
        return client

    # Fall back to the environment by BUILDING a client, not by mutating the
    # registry's. Setting `config.api_key` on a registry client makes the raw
    # HTTP probe pass and `chat()` still raise "no usable API key" -- the
    # transport does not read the key back from that object. Constructing one
    # from the declared base URL is also the more faithful test: it is what a
    # fresh process with that key in its environment would do.
    env_name = env_key_for(provider)
    env_value = os.getenv(env_name, "")
    if key_is_usable(env_value):
        base = _preset_base_url(provider) or getattr(config, "base_url", "") or ""
        if base:
            from kazma_core.llm_provider import LLMConfig, LLMProvider

            chosen = model or getattr(config, "model", "") or ""
            KEY_SOURCE[provider] = f"env:{env_name}"
            return LLMProvider(LLMConfig(base_url=base, api_key=env_value, model=chosen))
    KEY_SOURCE[provider] = f"none (looked for {env_name})"
    return None


def _preset_base_url(provider: str) -> str:
    """The base URL Kazma ships for this provider, matched case-insensitively."""
    try:
        from kazma_core.providers import PROVIDER_PRESETS
    except Exception:
        return ""
    for name, entry in PROVIDER_PRESETS.items():
        if name.lower() == provider.lower():
            return str(entry.get("base_url") or "")
    return ""


# ── the probes ──────────────────────────────────────────────────────────────


async def probe_models(client) -> ProbeResult:
    """The old Test button, kept — but never on its own.

    Queried over raw HTTP exactly as the Settings page does, so that its result
    can be compared against `chat_minimal` below. When those two disagree, the
    provider is reachable and not working, and that combination is the whole
    reason this script exists.
    """
    import httpx

    cfg = client.config
    base = (cfg.base_url or "").rstrip("/")
    key = cfg.api_key or ""
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
        ms = int((time.monotonic() - t) * 1000)
        if resp.status_code != 200:
            return ProbeResult("models_list", FAIL, f"HTTP {resp.status_code}", ms,
                               required=False)
        payload = resp.json()
        n = len(payload.get("data", payload) or [])
        return ProbeResult("models_list", PASS, f"{n} models", ms, required=False)
    except Exception as exc:
        return ProbeResult("models_list", FAIL, _short(exc),
                           int((time.monotonic() - t) * 1000), required=False)


async def probe_chat(client) -> ProbeResult:
    """The one that matters: a real completion on the path the product uses."""
    t = time.monotonic()
    try:
        r = await client.chat(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=_PROBE_MAX_TOKENS,
        )
        text = (getattr(r, "content", "") or "").strip()
        ms = int((time.monotonic() - t) * 1000)
        if not text:
            return ProbeResult("chat_minimal", FAIL, "empty completion", ms)
        return ProbeResult("chat_minimal", PASS, text[:40], ms)
    except Exception as exc:
        return ProbeResult("chat_minimal", FAIL, _short(exc),
                           int((time.monotonic() - t) * 1000))


async def probe_system_turn(client) -> ProbeResult:
    """Is the system turn honoured, and is the role name accepted at all?

    Z.AI rejects ``developer`` outright. Kazma sends ``system``, but anything
    embedding Kazma may not, so the failure mode is worth naming.
    """
    t = time.monotonic()
    try:
        r = await client.chat(
            [
                {"role": "system", "content": "You always answer with exactly: ACK"},
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=_PROBE_MAX_TOKENS,
        )
        text = (getattr(r, "content", "") or "").strip()
        ms = int((time.monotonic() - t) * 1000)
        if "ACK" in text.upper():
            return ProbeResult("system_turn", PASS, text[:40], ms)
        return ProbeResult("system_turn", FAIL,
                           f"system turn ignored (said {text[:30]!r})", ms)
    except Exception as exc:
        return ProbeResult("system_turn", FAIL, _short(exc),
                           int((time.monotonic() - t) * 1000))


async def probe_tools(client) -> ProbeResult:
    """A tool-calling agent that cannot call tools is not usable, whatever the
    chat endpoint says."""
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Return the current time in a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]
    t = time.monotonic()
    try:
        r = await client.chat(
            [{"role": "user", "content": "What time is it in Kuwait City? Use the tool."}],
            tools=tools, max_tokens=_PROBE_MAX_TOKENS,
        )
        ms = int((time.monotonic() - t) * 1000)
        calls = getattr(r, "tool_calls", None) or []
        if calls:
            return ProbeResult("tool_call", PASS, f"{len(calls)} call(s)", ms)
        return ProbeResult("tool_call", FAIL, "no tool_calls returned", ms, required=False)
    except Exception as exc:
        return ProbeResult("tool_call", FAIL, _short(exc),
                           int((time.monotonic() - t) * 1000), required=False)


def _short(exc: Exception, n: int = 90) -> str:
    text = " ".join(str(exc).split())
    return text[:n] + ("…" if len(text) > n else "")


PROBES = (probe_models, probe_chat, probe_system_turn, probe_tools)


async def run_provider(provider: str, model: str | None) -> ProviderReport:
    client = _client_for(provider, model)
    report = ProviderReport(provider=provider)
    if client is None:
        report.probes.append(ProbeResult(
            "resolve_client", FAIL,
            f"no usable key (set {env_key_for(provider)})"))
        return report
    report.model = str(getattr(getattr(client, "config", None), "model", "") or "?")
    for probe in PROBES:
        report.probes.append(await probe(client))
    return report


def usable_providers() -> list[str]:
    from kazma_core.model_registry import get_model_registry

    registry = get_model_registry()
    entries = registry.list_providers() if hasattr(registry, "list_providers") else []
    return [str(e.get("name")) for e in entries if e.get("name") and e.get("enabled")]


_MARK = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true", help="make real API calls")
    ap.add_argument("-p", "--providers", default="", help="comma-separated subset")
    ap.add_argument("--model", default=None, help="override the model")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    _load_env_keys()
    wanted = [p.strip() for p in args.providers.split(",") if p.strip()] or usable_providers()

    if not args.live:
        print(f"providers  {', '.join(wanted) or '(none enabled)'}")
        print(f"probes     {', '.join(p.__name__.removeprefix('probe_') for p in PROBES)}")
        print(f"plan       ~{len(wanted) * len(PROBES)} API calls")
        print("\nDry run. Add --live to execute. No calls were made.")
        return 0

    reports = [asyncio.run(run_provider(p, args.model)) for p in wanted]

    print(f"\n{'provider':<16} {'model':<26} probes")
    print("-" * 78)
    for r in reports:
        line = "  ".join(f"{_MARK[p.status]} {p.name}" for p in r.probes)
        print(f"{r.provider:<16} {r.model:<26} {line}")
        print(f"{'':<16} key: {KEY_SOURCE.get(r.provider, '?')}")
        for p in r.probes:
            if p.status == FAIL:
                tag = "required" if p.required else "optional"
                print(f"{'':<16} └─ {p.name} ({tag}): {p.detail}")

    broken = [r for r in reports if r.failed]
    print()
    if broken:
        print(f"{len(broken)} provider(s) failed a required probe: "
              + ", ".join(r.provider for r in broken))
        print("A provider whose model list answers but whose chat does not is "
              "NOT working, however green the Settings page looks.")
    else:
        print(f"all {len(reports)} provider(s) passed every required probe")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([r.to_dict() for r in reports], indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
