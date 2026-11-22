"""Finding 12: the intent classifier's refinement on Qwen, with and without the switch.

Run from a detached checkout (PYTHONPATH at it), DASHSCOPE_API_KEY in the
environment (never printed). Asks ``_refine_acts_llm`` exactly as a turn
does, through a real LLMProvider at DashScope, with reasoning off (today's
fix) and as it was (no switch), on the same commit. Writes ids, timings and
token counts only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from kazma_core.agent.intent import classify
from kazma_core.agent.intent.types import IntentAct
from kazma_core.llm_provider import LLMConfig, LLMProvider

REQUESTS = {
    "en1": "Can you look into the quarterly numbers and put something together for the board?",
    "en2": "I need the contract cleaned up and maybe a summary, not sure which first.",
    "en3": "Check the logs from yesterday and tell me if anything needs a fix.",
    "en4": "Pull together what we know about the Gulf logistics market.",
    "ar1": "ممكن تشوف أرقام الربع وتسوي شي للمجلس؟",
    "ar2": "أبي العقد مرتب ويمكن ملخص، مو متأكد أيهم أول.",
    "ar3": "شيك على سجلات أمس وقولي إذا فيه شي يبيله تصليح.",
    "ar4": "جمّع لي اللي نعرفه عن سوق اللوجستيات في الخليج.",
}


class _Counting:
    """The provider, counting what each call cost and how long it took."""

    def __init__(self, inner: LLMProvider, switch: bool) -> None:
        self.inner, self.switch = inner, switch
        self.last: dict = {}

    def can_turn_off_reasoning(self) -> bool:
        return self.switch and self.inner.can_turn_off_reasoning()

    async def chat(self, messages, **kwargs):
        start = time.monotonic()
        self.last = {"started": True}
        try:
            resp = await self.inner.chat(messages, **kwargs)
        except BaseException:
            self.last["seconds"] = round(time.monotonic() - start, 2)
            raise
        usage = resp.usage or {}
        details = usage.get("completion_tokens_details") or {}
        self.last = {"seconds": round(time.monotonic() - start, 2),
                     "prompt_tokens": usage.get("prompt_tokens", 0),
                     "completion_tokens": usage.get("completion_tokens", 0),
                     "reasoning_tokens": details.get("reasoning_tokens", 0)}
        return resp


async def one(provider: LLMProvider, text: str, switch: bool) -> dict:
    client = _Counting(provider, switch)
    heuristics = (IntentAct(kind="general", confidence=0.4, slots={}, source="heuristic"),)
    start = time.monotonic()
    acts = await classify._refine_acts_llm(text, client, heuristics)
    waited = round(time.monotonic() - start, 2)
    return {"answered": any(a.source == "llm" for a in acts), "waited": waited,
            "acts": [a.kind for a in acts], **client.last}


async def main() -> None:
    key = os.environ["DASHSCOPE_API_KEY"]
    provider = LLMProvider(LLMConfig(base_url=os.environ["F12_BASE_URL"],
                                     api_key=key, model="qwen3.7-plus", max_tokens=2048, temperature=0.0))
    out = {"model": "qwen3.7-plus", "rows": []}
    for rid, text in REQUESTS.items():
        for switch in (True, False):
            row = await one(provider, text, switch)
            out["rows"].append({"id": rid, "switch": switch, **row})
            print(json.dumps(out["rows"][-1], ensure_ascii=True), flush=True)
    path = sys.argv[1]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=True, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
