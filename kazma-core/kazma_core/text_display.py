"""Display helpers for mixed operator text (cron prompts, X drafts, audit).

Batch-job prompts wrap the tweet the operator actually wants to read::

    POST ONE TWEET ONLY. Call x_post with EXACTLY this text: "كاظمه…"

The English wrapper is LTR-first, so a naive ``dir=auto`` block stays LTR
and the Arabic tweet renders backwards on the page. Extract the body, then
set direction from the *body*, not the wrapper.
"""

from __future__ import annotations

import re

_ARABIC_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)
_LATIN_RE = re.compile(r"[A-Za-z]")
_CLOSED_QUOTE_RE = re.compile(r'["“]([^"”]{4,})["”]')
_UNCLOSED_QUOTE_RE = re.compile(r':\s*["“]([^"”]{4,})\s*$')
_BLOCKQUOTE_RE = re.compile(r">\s*(.+)$")
_BATCH_RE = re.compile(r"batch\s+job\s+(\d+\s*/\s*\d+)", re.IGNORECASE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

# A quoted fragment is the post only when it could be the whole message:
# at least this many characters, or most of the text. A reminder that says
# 'Reply "done" to stop these daily reminders' is not a post whose text is
# "done" -- the Scheduled page listed both of the owner's CoPilot renewal
# reminders as "done" (live, 2026-09-28) -- and a tweet quoting a short
# phrase is the tweet, not the phrase.
_MIN_BODY_CHARS = 60
_MIN_BODY_SHARE = 0.5


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _arabic_count(text: str) -> int:
    return len(_ARABIC_RE.findall(text or ""))


def is_arabic_dominant(text: str) -> bool:
    s = str(text or "")
    ar = _arabic_count(s)
    en = len(_LATIN_RE.findall(s))
    if ar == 0:
        return False
    return ar >= en or (ar > 20 and ar >= en * 0.4)


def text_dir(text: str) -> str:
    """CSS ``dir`` for a tweet/prompt body.

    Any Arabic → ``rtl``. ``auto`` (first-strong) is what made English
    wrappers and Latin prefixes paint the whole block LTR on ``/x``.
    """
    sample = extract_post_body(text) or str(text or "")
    if _arabic_count(sample) or _arabic_count(text):
        return "rtl"
    return "ltr"


def extract_post_body(text: str) -> str:
    """Return the tweet/body the operator should read, or the cleaned original."""
    raw = _collapse(text)
    if not raw:
        return ""
    # (fragment, introduced): introduced = the wrapper hands the post over
    # with a colon and ends with it ('EXACTLY this text: "..."', "...: >
    # ..."). Anything else is the post only when it is substantial.
    found: list[tuple[str, bool]] = []
    for match in _CLOSED_QUOTE_RE.finditer(raw):
        introduced = raw[: match.start()].rstrip().endswith(":") and len(raw[match.end():].strip()) <= 2
        found.append((match.group(1).strip(), introduced))
    unclosed = _UNCLOSED_QUOTE_RE.search(raw)
    if unclosed:
        found.append((unclosed.group(1).strip(), True))
    quoted = _BLOCKQUOTE_RE.search(raw)
    # A ">" mid-sentence is an arrow ("researcher -> writer"), not a quote:
    # a blockquote counts only where it is handed over, whatever its size.
    if quoted and (raw[: quoted.start()].rstrip().endswith(":") or not raw[: quoted.start()].strip()):
        found.append((quoted.group(1).strip(" \"“”"), True))
    candidates = [
        c for c, introduced in found
        if introduced or len(c) >= _MIN_BODY_CHARS or len(c) >= _MIN_BODY_SHARE * len(raw)
    ]
    arabic = [c for c in candidates if _arabic_count(c)]
    pool = arabic or candidates
    if pool:
        return max(pool, key=len)
    return raw


def display_kicker(text: str, body: str | None = None) -> str:
    """Short label for the wrapper around an extracted body (batch 2/8, …)."""
    raw = _collapse(text)
    body = (body if body is not None else extract_post_body(raw)).strip()
    if not raw:
        return ""
    batch = _BATCH_RE.search(raw)
    if batch:
        return f"Batch {batch.group(1).replace(' ', '')}"
    if not body or body == raw:
        return ""
    kicker = raw.replace(body, " ")
    kicker = re.sub(r'["“”]', "", kicker)
    kicker = _collapse(kicker).strip(" —-:;")
    if " — " in kicker:
        kicker = kicker.split(" — ", 1)[0]
    elif ". " in kicker:
        kicker = kicker.split(". ", 1)[0]
    return kicker[:80]


def shorten_outcome(text: str) -> str:
    """One-line result for a finished cron job — not the full agent essay."""
    raw = _collapse(text)
    if not raw:
        return ""
    bold = _BOLD_RE.search(raw)
    if bold:
        return bold.group(1).strip()[:120]
    head = re.split(r'[:：]\s*["“>]', raw, maxsplit=1)[0]
    head = re.sub(r"^\s*Status for\s+", "", head, flags=re.IGNORECASE)
    return head.strip(" —-")[:120]
