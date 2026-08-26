"""Conservative ToU / spam fail-safes evaluated *before* any X API write.

These are Kazma's own caps — they do not replace X's rate limits or the
operator's HITL approval. Denies are fail-closed (no post).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from kazma_core.x_api.config import XConfig, get_x_config
from kazma_core.x_api.ledger import get_ledger

__all__ = ["PolicyDecision", "evaluate_post", "evaluate_delete"]

_MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{1,15})\b")
_CASHTAG_RE = re.compile(r"(?<!\w)\$([A-Za-z]{1,6})\b")
_HASHTAG_RE = re.compile(r"(?<!\w)#(\w{1,50})\b")


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    reason: str
    mentions: tuple[str, ...] = ()
    cashtags: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()


def evaluate_post(
    text: str,
    *,
    cfg: XConfig | None = None,
    reply_to_id: str = "",
) -> PolicyDecision:
    """Return allow/deny for a candidate tweet. No network."""
    cfg = cfg or get_x_config()
    if cfg.kill_switch:
        return PolicyDecision(False, "X posting is disabled (KAZMA_X_POST=0).")
    if not cfg.enabled:
        return PolicyDecision(
            False,
            "X connector is off. Enable it in Settings → X after storing the "
            "four OAuth 1.0a keys (Read + Write app). Do not paste keys in chat.",
        )
    if not cfg.credentials.complete():
        return PolicyDecision(
            False,
            "X credentials incomplete. Settings → X: API Key, API Key Secret, "
            "Access Token, Access Token Secret. App-only Bearer tokens cannot post.",
        )
    body = (text or "").strip()
    if not body:
        return PolicyDecision(False, "Tweet text is empty.")
    if len(body) > cfg.max_chars:
        return PolicyDecision(
            False,
            f"Tweet is {len(body)} characters; cap is {cfg.max_chars} "
            "(Free-tier text tweets are 280). Shorten it.",
        )

    mentions = tuple(_MENTION_RE.findall(body))
    cashtags = tuple(_CASHTAG_RE.findall(body))
    hashtags = tuple(_HASHTAG_RE.findall(body))
    handle = (cfg.handle or "").lstrip("@").lower()
    other_mentions = [m for m in mentions if m.lower() != handle]
    if len(other_mentions) > cfg.max_mentions:
        return PolicyDecision(
            False,
            f"Too many @mentions ({len(other_mentions)}). Cap is {cfg.max_mentions} "
            "to stay off X's unsolicited-mention spam rules. Ask the operator "
            "to name who to tag, then they approve the draft.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )
    if len(cashtags) > cfg.max_cashtags:
        return PolicyDecision(
            False,
            f"Too many $cashtags ({len(cashtags)}). Cap is {cfg.max_cashtags}.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )
    if len(hashtags) > cfg.max_hashtags:
        return PolicyDecision(
            False,
            f"Too many hashtags ({len(hashtags)}). Cap is {cfg.max_hashtags}.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )

    ledger = get_ledger()
    if ledger.has_duplicate(body, window_days=cfg.duplicate_window_days):
        return PolicyDecision(
            False,
            f"Duplicate of a post in the last {cfg.duplicate_window_days} days. "
            "X rules forbid identical/near-identical automated posts.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )
    now = time.time()
    day_count = ledger.count_since(now - 86400)
    if day_count >= cfg.max_posts_per_day:
        return PolicyDecision(
            False,
            f"Daily cap reached ({cfg.max_posts_per_day}/day). "
            "This is a Kazma fail-safe well under X Free-tier quota.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )
    month_count = ledger.count_since(now - 30 * 86400)
    if month_count >= cfg.max_posts_per_month:
        return PolicyDecision(
            False,
            f"Monthly cap reached ({cfg.max_posts_per_month}/30d). "
            "Raise connectors.x.max_posts_per_month only if your X dashboard quota allows it.",
            mentions=mentions,
            cashtags=cashtags,
            hashtags=hashtags,
        )
    extra = ""
    if reply_to_id:
        extra = f" Thread reply to `{reply_to_id}`."
    return PolicyDecision(
        True,
        f"Policy ok.{extra}",
        mentions=mentions,
        cashtags=cashtags,
        hashtags=hashtags,
    )


def evaluate_delete(*, cfg: XConfig | None = None) -> PolicyDecision:
    cfg = cfg or get_x_config()
    if cfg.kill_switch:
        return PolicyDecision(False, "X posting is disabled (KAZMA_X_POST=0).")
    if not cfg.enabled or not cfg.credentials.complete():
        return PolicyDecision(False, "X connector is not configured.")
    return PolicyDecision(True, "Policy ok.")
