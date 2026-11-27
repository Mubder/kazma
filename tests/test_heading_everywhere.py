"""Kazma's one heading is the same everywhere and names every chat app it ships.

2026-11-27: WhatsApp went live on 16 Nov, but the README's head, its
"Talk to it from" row, its diagram and the GitHub About still listed
Telegram, Discord and Slack only. Each surface kept its own hand-written
description, and only the guide was updated when the adapter shipped. The
heading now lives once, in ``docs/brand/heading.json`` (English and Arabic),
the READMEs carry it verbatim, and this gate reads the chat apps from the
gateway's own list (``CHAT_PLATFORMS``), so the next one cannot ship unnamed.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HEADING = json.loads((REPO / "docs" / "brand" / "heading.json").read_text(encoding="utf-8"))
README = {"en": REPO / "README.md", "ar": REPO / "README.ar.md"}
BIO_LIMIT = 160  # an X or Instagram bio; LinkedIn and the GitHub About allow more


def chat_platforms(source: str) -> tuple[str, ...]:
    """``CHAT_PLATFORMS`` as the gateway's source declares it (read, not imported)."""
    for node in ast.parse(source).body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == "CHAT_PLATFORMS":
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("CHAT_PLATFORMS moved out of kazma_gateway/chat_adapters.py")


PLATFORMS = chat_platforms(
    (REPO / "kazma-gateway" / "kazma_gateway" / "chat_adapters.py").read_text(encoding="utf-8"))


def unnamed(text: str, platforms: tuple[str, ...], lang: str, names: dict = HEADING["channel_names"]) -> list[str]:
    """The platforms *text* does not name in *lang* (a platform with no name counts as unnamed)."""
    return [p for p in platforms if p not in names or names[p][lang] not in text]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_gateways_list_is_read():
    assert "whatsapp" in PLATFORMS and len(PLATFORMS) >= 4, PLATFORMS


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_heading_fits_a_bio_and_names_every_chat_app(lang):
    heading = HEADING[lang]["heading"]
    assert len(heading) <= BIO_LIMIT, (lang, len(heading))
    assert len(HEADING[lang]["short"]) <= 60, lang
    assert unnamed(heading, PLATFORMS, lang) == []


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_readme_carries_the_heading_and_lede_verbatim(lang):
    text = README[lang].read_text(encoding="utf-8")
    assert f"<!-- kazma-heading:{lang} (docs/brand/heading.json) -->" in text, lang
    assert f"**{HEADING[lang]['heading']}**" in text, f"{README[lang].name}: the heading differs from heading.json"
    assert _flat(HEADING[lang]["lede"]) in _flat(text), f"{README[lang].name}: the lede differs from heading.json"


def test_the_readme_lists_every_chat_app_where_it_lists_them():
    en = README["en"].read_text(encoding="utf-8")
    talk = next(line for line in en.splitlines() if line.startswith("| **~") and "·" in line)
    assert unnamed(talk.rsplit("|", 2)[-2], PLATFORMS, "en", _LATIN) == [], "the 'Talk to it from' cell"
    for lang, path in README.items():
        diagram = next(line for line in path.read_text(encoding="utf-8").splitlines() if 'U["' in line)
        assert unnamed(diagram, PLATFORMS, lang, _LATIN) == [], f"{path.name}: the diagram's channels"


#: The READMEs' tables and diagrams write each app's own (Latin) name in both languages.
_LATIN = {p: {"en": v["en"], "ar": v["en"]} for p, v in HEADING["channel_names"].items()}


def test_control_a_new_chat_app_left_out_is_named():
    planted = PLATFORMS + ("signal",)
    assert unnamed(HEADING["en"]["heading"], planted, "en") == ["signal"]
    names = dict(HEADING["channel_names"], signal={"en": "Signal", "ar": "سيجنال"})
    assert unnamed(HEADING["ar"]["heading"], planted, "ar", names) == ["signal"]
    old = "The self-hosted AI agent that asks before it acts, on Telegram, Discord or Slack."
    assert unnamed(old, PLATFORMS, "en") == ["whatsapp"]


def test_control_the_platform_list_is_read_from_source():
    assert chat_platforms('CHAT_PLATFORMS: tuple[str, ...] = ("a", "b")\n') == ("a", "b")
    with pytest.raises(AssertionError):
        chat_platforms("PLATFORMS = ('a',)\n")
