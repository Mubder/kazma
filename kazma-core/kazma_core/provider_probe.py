"""One provider health probe for the one route that offers "Test".

Phase 4 of ``docs/plans/PROVIDER_LAYER_PLAN.md``.

There were two near-identical ``test_provider`` implementations —
``kazma_ui.providers`` behind ``/api/providers/{name}/test`` and
``kazma_core.settings_providers`` behind ``/api/settings/providers/{name}/test``
— and a fix applied to one of them changed nothing an operator would ever
see. Two surfaces for one concept is how they drift, and this is what that
drift costs.

The duplicate is now deleted rather than merely kept in step: there is one
``/api/providers/{name}/test``, and it calls the function below. This module
survives the deletion because the probe is worth naming on its own — what a
real check has to do is a decision, not an implementation detail.

**What a real check has to do.** Querying the model list answers a different
question from the one the product asks. Measured on a live provider whose base
URL had a version segment wrongly appended::

    /models            -> 200
    /chat/completions  -> 404

The page reported a paid provider as healthy and not one message ever reached
it. So a provider is only *working* when a completion comes back on the path the
product uses; anything less is *reachable*, which is a different thing and needs
saying out loud.
"""

from __future__ import annotations

import time
from typing import Any

__all__ = ["ChatProbeResult", "probe_chat_completion"]

#: Generous on purpose. A reasoning model spends tokens thinking before it emits
#: any content, so a tight cap returns an EMPTY completion and the probe blames
#: the provider for its own mistake. Measured: glm-5.3 used 16 reasoning tokens
#: before its first content token, and a 16-token cap reported a working
#: provider as broken.
PROBE_MAX_TOKENS = 160

#: Short, cheap, and unambiguous to score — we only need to know that content
#: came back at all.
PROBE_PROMPT = "Reply with the single word: ready"

ChatProbeResult = dict[str, Any]


async def probe_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 20.0,
) -> ChatProbeResult:
    """Send one small completion. Returns ``{ok, ms, model, error}``.

    Never raises: a health check that throws cannot report a health status.
    """
    import httpx

    if not model:
        return {"ok": False, "ms": None, "model": "", "error": "no model selected"}
    if not base_url:
        return {"ok": False, "ms": None, "model": model, "error": "no base URL configured"}

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": PROBE_MAX_TOKENS,
    }

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(url, headers=headers, json=payload)
        ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            detail = " ".join(resp.text[:200].split())
            return {
                "ok": False, "ms": ms, "model": model,
                "error": f"HTTP {resp.status_code} — {detail}",
            }

        data = resp.json()
        choices = data.get("choices") or []
        content = ""
        if choices:
            content = str((choices[0].get("message") or {}).get("content") or "").strip()
        if not content:
            # A 200 with no content is not a working provider. This is the
            # shape a too-small token budget produces, and also what some
            # providers return when a filter fires.
            return {
                "ok": False, "ms": ms, "model": model,
                "error": "the provider returned an empty completion",
            }
        return {"ok": True, "ms": ms, "model": str(data.get("model") or model), "error": ""}

    except httpx.ConnectError as exc:
        return {"ok": False, "ms": None, "model": model, "error": f"cannot connect — {exc}"}
    except Exception as exc:  # pragma: no cover - a probe must not raise
        return {
            "ok": False, "ms": None, "model": model,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
