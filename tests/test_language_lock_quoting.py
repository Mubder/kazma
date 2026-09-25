"""The language lock governs Kazma's own words, never the content it quotes.

2026-09-25, an English "list me what posts we still did not send": the
answer named three Arabic drafts and refused to show them — "I'm not
printing the Arabic script this turn because the language lock for this
turn is English-only". The per-turn lock said "Do NOT use Arabic script",
and the model applied it to the operator's own stored drafts.

The lock exists to stop the REPLY drifting into the session's first
language (Arabic greetings in an English answer). Stored content — saved
drafts, file contents, tool results, names — is data, and is shown exactly
as stored in either direction.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

from kazma_core.language_lock import detect_user_language, language_lock_message

ROOT = Path(__file__).resolve().parents[1]

SAMPLES = {
    "en": "list me what posts we still did not send",
    "ar": "اعرض لي المنشورات التي لم ترسل بعد",
    "mixed": "show my drafts المسودات كلها",
    "unknown": "👍 123",
}


@pytest.mark.parametrize("lang, text", sorted(SAMPLES.items()))
def test_every_lock_exempts_quoted_material(lang, text):
    assert detect_user_language(text) == lang
    msg = language_lock_message(text)
    assert "QUOTED MATERIAL IS EXEMPT" in msg, msg
    assert "exactly as stored" in msg


def test_the_english_lock_still_keeps_the_reply_english():
    """The exemption must not reopen the drift the lock exists to stop."""
    msg = language_lock_message(SAMPLES["en"])
    assert "English only" in msg
    assert "no Arabic greetings" in msg
    # ...but it no longer bans the script outright — that ban is what hid
    # the operator's drafts.
    assert "Do NOT use Arabic script" not in msg


def test_the_base_language_rule_carries_the_exemption_too():
    src = (ROOT / "kazma-core/kazma_core/agent_runner.py").read_text(encoding="utf-8")
    assert src.count("CRITICAL LANGUAGE RULE") == src.count("exactly as stored, in its original script"), (
        "every copy of the base language rule in agent_runner must carry the "
        "quoted-material exemption"
    )


# ── class gate: no prompt forbids a script outright ──────────────────────

_SCRIPT_BAN = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not)\s+(?:use|write|output|print|show)\s+"
    r"(?:any\s+)?(?:arabic|latin|english)\s+(?:script|text|characters|letters)\b",
    re.IGNORECASE,
)
_EXEMPTION = ("QUOTED MATERIAL IS EXEMPT", "exactly as stored")


def _outright_script_bans(sources: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _SCRIPT_BAN.search(node.value) and not any(e in node.value for e in _EXEMPTION):
                    problems.append(f"{rel}:{node.lineno}")
    return problems


def _prompt_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for p in (ROOT / root).rglob("*.py"):
            if "tests" in p.parts:
                continue
            out[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_prompt_bans_a_script_outright():
    problems = _outright_script_bans(_prompt_sources())
    assert not problems, (
        "A prompt string forbids a script outright. The model applies that to "
        "quoted content and withholds the operator's own data; scope the rule "
        "to the reply's own words and state the quoted-material exemption:\n  "
        + "\n  ".join(problems)
    )


def test_an_outright_script_ban_is_caught():
    """Negative control: the pre-2026-09-25 English lock, and a scoped rule."""
    planted = {
        "x.py": textwrap.dedent(
            '''
            OLD = "You MUST reply in English only. Do NOT use Arabic script."
            NEW = ("Write in English only. QUOTED MATERIAL IS EXEMPT: drafts are "
                   "shown exactly as stored. Do not use Arabic script in your words.")
            '''
        )
    }
    assert _outright_script_bans(planted) == ["x.py:2"]
