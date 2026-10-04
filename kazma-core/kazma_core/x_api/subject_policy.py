"""Versioned operator subject cards with conservative legacy permissions."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

SCHEMA_VERSION = 2
RESERVED_IDS = frozenset({"voice", "post", "none", "__voice__", "__post__"})
CHECKS = frozenset({"context", "target", "stance", "evidence", "safety"})
MOODS = frozenset({"roast", "angry", "dry", "deadpan", "supportive"})
_ARABIC_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u0640]")


def normalize_match(text: str) -> str:
    """Keep distinct Arabic letters; remove vocalization/tatweel for matching only."""
    return _ARABIC_MARKS.sub("", unicodedata.normalize("NFC", str(text or ""))).casefold()


def literal_spans(text: str, alias: str) -> tuple[dict[str, Any], ...]:
    """Match normalized tokens while returning offsets into the original text."""
    needle = normalize_match(alias).strip()
    if not needle or needle == "*" or (not needle.isascii() and len(needle) < 3):
        return ()
    # Map each normalized character to its original base/combining cluster.
    clusters: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        if unicodedata.combining(char) and clusters:
            start, _ = clusters[-1]
            clusters[-1] = (start, index + 1)
        else:
            clusters.append((index, index + 1))
    haystack, positions = [], []
    for start, end in clusters:
        folded = normalize_match(text[start:end])
        haystack.append(folded)
        positions.extend([(start, end)] * len(folded))
    article = "(?:ال)?" if "\u0621" <= needle[0] <= "\u064a" and not needle.startswith("ال") else ""
    pattern = re.compile(rf"(?<!\w){article}{re.escape(needle)}(?!\w)")
    spans = []
    for hit in pattern.finditer("".join(haystack)):
        start, end = positions[hit.start()][0], positions[hit.end() - 1][1]
        spans.append({"start": start, "end": end, "text": text[start:end]})
    return tuple(spans)


def literal_match(text: str, alias: str) -> bool:
    return bool(literal_spans(text, alias))


def _card_fields(source: dict[str, Any], version: int, label: str, errors: list[str]) -> dict[str, Any]:
    """Bound text and list fields before checking relationships between cards."""
    card: dict[str, Any] = {}
    for key, limit in (("id", 80), ("target", 200), ("view", 4000), ("scope", 2000),
                       ("register", 500), ("owner", 200), ("change_reason", 1000)):
        value = source.get(key, "")
        if not isinstance(value, str):
            errors.append(f"{label}: {key} must be text.")
            value = ""
        card[key] = value.strip()
        if len(card[key]) > limit:
            errors.append(f"{label}: {key} exceeds {limit} characters.")
    for key in ("match", "aliases", "exclusions", "exceptions", "hard_lines", "examples", "counterexamples", "allowed_moods", "required_checks"):
        default = sorted(CHECKS) if key == "required_checks" else []
        value = source.get(key, default)
        if isinstance(value, str) and version == 1:
            value = [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
            errors.append(f"{label}: {key} must be a list of text.")
            value = []
        items = [item.strip() for item in value if item.strip()]
        limit = 160 if key in ("match", "aliases", "exclusions") else 1000
        if len(items) > 40 or any(len(item) > limit for item in items):
            errors.append(f"{label}: {key} exceeds its size limit.")
        card[key] = items
    return card


def normalize_cards(raw: Any) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """One validator for Settings, startup and legacy migration; no writes here."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return [], ("Subjects contain invalid JSON.",)
    if not isinstance(raw, list):
        return [], ("Subjects must be a list.",)
    errors: list[str] = []
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    aliases: dict[str, tuple[str, str]] = {}
    catchalls = 0
    if len(raw) > 64:
        errors.append("At most 64 subject cards are supported.")
    for pos, source in enumerate(raw[:64]):
        label = f"Subject {pos + 1}"
        if not isinstance(source, dict):
            errors.append(f"{label} is invalid.")
            continue
        version = source.get("schema_version", 1)
        if type(version) is not int or version not in (1, SCHEMA_VERSION):
            errors.append(f"{label} uses an unsupported schema version.")
        card = _card_fields(source, version, label, errors)
        if version == 1 and not card["target"]:
            card["target"] = card["id"]
        ident = card["id"].casefold()
        if not ident or re.search(r"\s|[<>]", ident) or ident in RESERVED_IDS:
            errors.append(f"{label} needs an id (valid, non-reserved).")
        if ident in seen:
            errors.append(f"{label} has a duplicate id.")
        seen.add(ident)
        if not card["target"]:
            errors.append(f"{label} needs an actual target separate from its ID.")
        if not card["match"] and not card["aliases"]:
            errors.append(f"{label} needs at least one keyword or alias; otherwise it can never match.")
        catchall = "*" in card["match"]
        if catchall:
            catchalls += 1
            if len(card["match"]) != 1 or card["aliases"]:
                errors.append(f"{label}: catch-all must use '*' alone.")
        card["side"] = str(source.get("side") or "").strip().lower()
        card["mood"] = str(source.get("mood") or "dry").strip().lower()
        if card["side"] not in ("", "against", "support"):
            errors.append(f"{label} has an invalid side.")
        if not catchall and not card["side"] and not card["view"]:
            errors.append(f"{label} needs a position: set against or support, or a view.")
        if card["mood"] not in MOODS or set(card["allowed_moods"]) - MOODS:
            errors.append(f"{label} has an unknown mood or allowed tone.")
        for key, default in (("allow_draft", True), ("allow_auto", False)):
            card[key] = source.get(key, default)
            if type(card[key]) is not bool:
                errors.append(f"{label}: {key} must be true or false.")
        card["evidence_policy"] = source.get("evidence_policy", "required")
        card["evidence_max_age_days"] = source.get("evidence_max_age_days", 30)
        if type(card["evidence_max_age_days"]) is not int or not 1 <= card["evidence_max_age_days"] <= 3650:
            errors.append(f"{label}: evidence age must be 1–3650 days.")
        if card["evidence_policy"] not in ("required", "opinion_only"):
            errors.append(f"{label} has an invalid evidence policy.")
        if set(card["required_checks"]) - CHECKS:
            errors.append(f"{label} names an unknown verification check.")
        if card["allow_auto"] and (not card["allow_draft"] or not CHECKS.issubset(card["required_checks"])):
            errors.append(f"{label}: auto requires drafting and all mandatory checks.")
        revision = source.get("revision", 1)
        if type(revision) is not int or revision < 1:
            errors.append(f"{label} has an invalid revision.")
            revision = 1
        card.update(schema_version=SCHEMA_VERSION, revision=revision)
        for alias in card["match"] + card["aliases"]:
            normalized = normalize_match(alias)
            if normalized == "*":
                continue
            previous = aliases.get(normalized)
            if previous and previous[1] and card["side"] and previous[1] != card["side"]:
                errors.append(f"{label}: alias {alias!r} conflicts with {previous[0]}'s side.")
            aliases[normalized] = (card["id"], card["side"])
        if set(map(normalize_match, card["match"] + card["aliases"])) & set(map(normalize_match, card["exclusions"])):
            errors.append(f"{label} includes and excludes the same alias.")
        cards.append(card)
    if catchalls > 1:
        errors.append("Only one catch-all is allowed; card order cannot choose a policy.")
    return cards, tuple(errors)


def revise_cards(previous: Any, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Server assigns revisions from substantive content, never a client increment."""
    prior, _ = normalize_cards(previous)
    by_id = {card["id"].casefold(): card for card in prior}
    revised = []
    for card in cards:
        before = by_id.get(card["id"].casefold())
        updated = dict(card)
        content = {key: value for key, value in card.items() if key != "revision"}
        old_content = {key: value for key, value in (before or {}).items() if key != "revision"}
        updated["revision"] = (before["revision"] + int(content != old_content)) if before else 1
        revised.append(updated)
    return revised
