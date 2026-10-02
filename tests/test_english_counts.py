"""English-only surfaces say a count's noun in the form the number takes.

The web UI's counts are the catalog's plural forms (``tests/test_count_labels.py``).
The terminal UI, the CLI and the chat-app replies speak English only, and
printed a count beside a plural whatever the number: the TUI's "(1 msgs)"
after loading a one-message chat, "1 new chunks" after a ``/kb`` ingest,
"Slow down — 0/1 requests available" (2026-10-03). ``count_noun``
(``kazma_core/english_count.py``) is the one way to say one; this gate reads
every f-string in those packages for a value followed by a plural noun.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from kazma_core.english_count import count_noun

ROOT = Path(__file__).resolve().parent.parent
#: The packages whose words reach a person in English only.
PACKAGES = ("kazma-tui/kazma_tui", "kazma-cli/kazma_cli", "kazma-gateway/kazma_gateway")


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((1, "message"), "1 message"),
        ((0, "message"), "0 messages"),
        ((2, "message"), "2 messages"),
        ((12345, "token"), "12,345 tokens"),
        ((1, "match", "matches"), "1 match"),
        ((3, "match", "matches"), "3 matches"),
        ((2.5, "minute"), "2.5 minutes"),
    ],
)
def test_count_noun_says_the_noun_in_the_number_s_form(args, expected):
    assert count_noun(*args) == expected


def test_shown_changes_how_the_number_reads_not_the_form():
    assert count_noun(1, "token", shown="one") == "one token"
    assert count_noun(1200, "token", shown="1.2K") == "1.2K tokens"


# ── Gate: no f-string prints a count beside a plural in one form ───────────

#: A value, up to two words that do not end in "s", then a word that does.
_NOUN = re.compile(r"^\s((?:[A-Za-z-]+(?<!s)\s){0,2})([A-Za-z]+s)\b")
_NOT_A_NOUN = frozenset({"is", "was", "as", "has", "does", "its", "this", "us"})
#: Log lines are diagnostics, not words a person reads in the product.
_LOGGING = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})

#: What the rule reads as a count but is not, by file and text, with why.
DECLARED = {
    "kazma-cli/kazma_cli/doctor.py: {pname} in Settings": "a provider's name before a page's",
    "kazma-cli/kazma_cli/doctor.py: {_cat_owner} offers": "a provider's name, then a verb",
    "kazma-cli/kazma_cli/doctor.py: {_host(base)} serves": "a host, then a verb",
    "kazma-cli/kazma_cli/update.py: {origin_url or '(unreadable)'} kazma update pulls": "a URL, then a verb",
    "kazma-cli/kazma_cli/update.py: {_IDLE_TIMEOUT_S / 60} minutes": (
        "a constant (15); the updater must run while kazma_core may not import, "
        "so it cannot call count_noun"
    ),
    "kazma-gateway/kazma_gateway/agent_handler/commands.py: "
    "{'running' if worker.get('running') else 'stopped'} parsers": "a word, not a number",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py: {tool} Args": "a tool's name before a heading",
    "kazma-gateway/kazma_gateway/agent_handler/session_commands.py: "
    "{msg.platform.capitalize()} chat now continues": "a platform's name, then a verb",
    "kazma-gateway/kazma_gateway/mcp_server.py: {p} escapes": "a path, then a verb",
    "kazma-gateway/kazma_gateway/slash_commands.py: {iter_a} vs": "an iteration number before 'vs'",
    "kazma-tui/kazma_tui/traces.py: {entry.duration_ms} ms": "a unit's abbreviation",
}


def _in_a_log_call(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    while parent is not None and not isinstance(parent, ast.Call):
        parent = parents.get(parent)
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Attribute)
        and parent.func.attr in _LOGGING
    )


def one_form_counts(source: str, rel: str) -> list[str]:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or _in_a_log_call(node, parents):
            continue
        for value, after in zip(node.values, node.values[1:]):
            if not (isinstance(value, ast.FormattedValue) and isinstance(after, ast.Constant)):
                continue
            match = _NOUN.match(str(after.value))
            noun = match.group(2).lower() if match else ""
            if match and noun not in _NOT_A_NOUN and not noun.endswith(("ss", "us", "is")):
                found.append(f"{rel}: {{{ast.unparse(value.value)}}} {match.group(1)}{match.group(2)}")
    return found


def _package_sources() -> list[str]:
    """Tracked and new files alike: a new module is read before it is committed."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "--", *PACKAGES],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(rel for rel in listed if rel.endswith(".py") and (ROOT / rel).is_file())


def _all_found() -> list[str]:
    return [
        hit
        for rel in _package_sources()
        for hit in one_form_counts((ROOT / rel).read_text(encoding="utf-8"), rel)
    ]


def test_no_english_count_is_in_one_form():
    found = [hit for hit in _all_found() if hit not in DECLARED]
    assert not found, (
        "A count beside a plural reads wrong for 1 (\"(1 msgs)\"); say it with "
        "kazma_core.english_count.count_noun(n, 'message'), or declare a value "
        "that is not a count in DECLARED with why:\n  " + "\n  ".join(found)
    )


def test_every_declaration_still_names_an_f_string():
    found = set(_all_found())
    stale = sorted(key for key in DECLARED if key not in found)
    assert not stale, "Declared but no longer in the code; remove:\n  " + "\n  ".join(stale)


def test_negative_control_the_shipped_shapes_are_caught():
    source = (
        'a = f"Loaded #{hit.short_id}  ({n} msgs). "\n'
        'b = f"{result.chunks_new} new chunks (+{skipped} deduped)."\n'
        'c = f"Slow down — {remaining}/{limit} requests available."\n'
        "d = f\"{count_noun(n, 'message')}\"\n"
        'logger.info(f"{n} rows written")\n'
        'e = f"{n} in progress"\n'
    )
    assert one_form_counts(source, "synthetic.py") == [
        "synthetic.py: {n} msgs",
        "synthetic.py: {result.chunks_new} new chunks",
        "synthetic.py: {limit} requests",
    ]
