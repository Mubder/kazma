"""LLM-backed email analysis (summary, actions, security)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ANALYZE_SYSTEM = """You are an email security and productivity analyst for Kazma (كاظمه).
Return ONLY valid JSON with keys:
summary (string),
action_items (array of {text, deadline|null}),
sentiment (neutral|positive|negative|urgent),
security ({risk_level: low|medium|high, phishing_signals: string[], notes: string}).
Be concise. Flag phishing (urgent money, fake banks, credential requests, odd domains).
"""


async def analyze_email_text(
    *,
    subject: str,
    from_addr: str,
    body: str,
    focus: str = "full",
    max_body_chars: int = 32000,
) -> dict[str, Any]:
    """Analyze email via active LLM; heuristic fallback if LLM unavailable."""
    body = (body or "")[: max(1000, min(100_000, int(max_body_chars or 32000)))]
    text = f"From: {from_addr}\nSubject: {subject}\n\n{body}"
    if focus == "security":
        instruction = "Focus on phishing/security risk."
    elif focus == "actions":
        instruction = "Focus on action items and deadlines."
    else:
        instruction = "Provide full analysis."

    try:
        result = await _llm_analyze(text, instruction)
        if result:
            return result
    except Exception as exc:
        logger.debug("[email_analyze] LLM failed: %s", exc)

    return _heuristic_analyze(subject, from_addr, body)


async def _llm_analyze(text: str, instruction: str) -> dict[str, Any] | None:
    """Call active OpenAI-compatible client if available."""
    # Prefer model registry active profile
    try:
        from kazma_core.model_registry import get_model_registry
        from kazma_core.llm_provider import LLMConfig, LLMProvider

        reg = get_model_registry()
        profile = reg.get_active_profile() if hasattr(reg, "get_active_profile") else None
        if not profile:
            return None
        cfg = LLMConfig(
            base_url=profile.get("base_url") or "",
            api_key=profile.get("api_key") or "none",
            model=profile.get("model") or "gpt-4o-mini",
        )
        if not cfg.base_url:
            return None
        provider = LLMProvider(cfg)
        resp = await provider.chat(
            messages=[
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {
                    "role": "user",
                    "content": f"{instruction}\n\nEMAIL:\n{text[:24000]}",
                },
            ],
            temperature=0.2,
            max_tokens=800,
        )
        content = (resp.content or "").strip()
        return _parse_json(content)
    except Exception as exc:
        logger.debug("[email_analyze] registry path failed: %s", exc)
        return None


def _parse_json(content: str) -> dict[str, Any] | None:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "summary" in data:
            return data
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    return None


def _heuristic_analyze(subject: str, from_addr: str, body: str) -> dict[str, Any]:
    blob = f"{subject}\n{from_addr}\n{body}".lower()
    signals: list[str] = []
    risk = "low"
    phish_kw = (
        "password",
        "verify your account",
        "wire",
        "urgent",
        "click here",
        "lottery",
        "won $",
        "ssn",
        "bank",
        "bit.ly",
        "act within",
        "claim now",
    )
    for kw in phish_kw:
        if kw in blob:
            signals.append(kw)
    if len(signals) >= 3:
        risk = "high"
    elif signals:
        risk = "medium"
    # Odd TLD
    if re.search(r"@[\w.-]+\.(ru|tk|top|xyz)\b", from_addr.lower()):
        signals.append("suspicious_tld")
        risk = "high" if risk != "high" else risk

    actions = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(("-", "*", "•")) or "please " in s.lower() or "need you" in s.lower():
            if len(s) > 8:
                actions.append({"text": s.lstrip("-*• ").strip()[:200], "deadline": None})
        if len(actions) >= 5:
            break

    sentiment = "urgent" if "urgent" in blob or "asap" in blob else "neutral"
    summary = (body or subject or "")[:280].replace("\n", " ")
    return {
        "summary": summary or "(empty)",
        "action_items": actions,
        "sentiment": sentiment,
        "security": {
            "risk_level": risk,
            "phishing_signals": signals,
            "notes": "Heuristic analysis (LLM unavailable)." if not signals else "Review carefully.",
        },
    }
