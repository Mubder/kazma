"""The chat apps' "/" menu, ``/help`` and the Slash Commands page agree.

Telegram shows only the commands in ``BOT_MENU_COMMANDS``; ``/help`` is what a
user reads in any chat app; ``docs/docs/reference/slash-commands.md`` is the
reference (and kazma.ai's page). Until 2026-10-01 the three drifted: ``/help``
left out ``/kb``, ``/ide``, ``/hitl``, ``/undo``, ``/edit`` and ``/mission``,
the menu left out ``/plan``, and the reference's ``/help`` block was an older
text than the code sent. The menu's own comment said "keep in sync"; nothing
checked it.
"""

from __future__ import annotations

import re
from pathlib import Path

from kazma_gateway.slash_commands import BOT_MENU_COMMANDS, _cmd_help

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "docs" / "reference" / "slash-commands.md"

_COMMAND = re.compile(r"(?<![\w/])/([a-z_]+)")  # not a path's "a/b" or "//"


def _reference() -> str:
    return REFERENCE.read_text(encoding="utf-8").replace("\r\n", "\n")


def commands_in(text: str) -> set[str]:
    """Every ``/name`` a text names (``/steer!`` names ``steer``)."""
    return set(_COMMAND.findall(text))


def menu_help_problems(menu: list[str], help_text: str) -> list[str]:
    named = commands_in(help_text)
    problems = [f"/{c}: in the menu, missing from /help" for c in sorted(set(menu) - named)]
    problems += [f"/{c}: in /help, missing from the menu" for c in sorted(named - set(menu))]
    return problems


def help_block(reference: str) -> str:
    """The code block under the page's ``/help`` "Response"."""
    section = reference.split("### `/help`", 1)[1].split("\n### ", 1)[0]
    match = re.search(r"\*\*Response:\*\*\n```\n(.*?)\n```", section, re.DOTALL)
    assert match, "the /help section has no Response block"
    return match.group(1)


def test_the_menu_and_help_list_the_same_commands():
    menu = [row["command"] for row in BOT_MENU_COMMANDS]
    problems = menu_help_problems(menu, _cmd_help())
    assert not problems, (
        "BOT_MENU_COMMANDS (the \"/\" menu Telegram shows) and _cmd_help() "
        "must name the same commands:\n  " + "\n  ".join(problems)
    )


def test_the_check_catches_a_command_left_out():
    """Negative control: the drift as it was before 2026-10-01."""
    menu = ["help", "kb", "status"]
    help_text = "• `/help` — this\n• `/status` — health\n• `/plan on` — plan mode\n"
    assert menu_help_problems(menu, help_text) == [
        "/kb: in the menu, missing from /help",
        "/plan: in /help, missing from the menu",
    ]


def test_the_reference_shows_the_help_the_code_sends():
    assert help_block(_reference()) == _cmd_help(), (
        "The /help block in docs/docs/reference/slash-commands.md is not what "
        "_cmd_help() sends. Paste the code's text into the page."
    )


def test_every_menu_command_is_on_the_reference_page():
    reference = _reference()
    missing = [
        row["command"]
        for row in BOT_MENU_COMMANDS
        if f"`/{row['command']}" not in reference
    ]
    assert not missing, f"slash-commands.md never names: {', '.join('/' + c for c in missing)}"


# ── /kb subcommands (Knowledge audit KB-S07) ──────────────────────────────
# The reference's /kb table named add, crawl, refresh and delete while those
# were rewritten into a model turn and never reached the handler. Now the
# table, the handler and the admin rule must agree, subcommand by subcommand.

_KB_ALIASES = {"", "help", "ingest"}  # bare /kb, its help, and /kb add's other name


def kb_handled_subcommands(source: str) -> set[str]:
    """The subcommands ``_try_kb_command`` branches on."""
    import ast

    fn = next(node for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.AsyncFunctionDef) and node.name == "_try_kb_command")
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "sub":
            for comparator in node.comparators:
                values = comparator.elts if isinstance(comparator, (ast.Tuple, ast.List, ast.Set)) else [comparator]
                found |= {v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    return found


def kb_reference_rows(reference: str) -> dict[str, bool]:
    """``{subcommand: admin only}`` from the reference's /kb table."""
    section = reference.split("## `/kb`", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for match in re.finditer(r"^\| `/kb ?([a-z]*)[^`]*` \| (yes|no) \|", section, re.M):
        rows[match.group(1)] = match.group(2) == "yes"
    return rows


def kb_problems(handled: set[str], rows: dict[str, bool], admin) -> list[str]:
    problems = [f"/kb {s}: handled, not on the reference page"
                for s in sorted(handled - _KB_ALIASES - set(rows))]
    problems += [f"/kb {s}: on the reference page, not handled" for s in sorted(set(rows) - handled)]
    for sub, admin_only in sorted(rows.items()):
        if admin(f"/kb {sub} x https://docs.example.test/") != admin_only:
            problems.append(f"/kb {sub}: the page says admin only={admin_only}, the gate disagrees")
    return problems


def test_the_kb_subcommands_agree_with_the_handler_and_the_admin_rule():
    from kazma_gateway.slash_commands import changes_global_config

    source = (ROOT / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "commands.py").read_text(
        encoding="utf-8")
    rows = kb_reference_rows(_reference())
    assert {"add", "crawl", "refresh", "delete", "search", "status", "list"} <= set(rows)
    assert kb_problems(kb_handled_subcommands(source), rows, changes_global_config) == []


def test_the_kb_check_catches_the_old_drift():
    """Negative control: the page's old table (add and delete named) against
    a handler the rewrite never let them reach, and an admin column the gate
    did not honour."""
    rows = {"": False, "list": False, "add": False, "delete": False}
    assert kb_problems({"", "list"}, rows, lambda text: False) == [
        "/kb add: on the reference page, not handled",
        "/kb delete: on the reference page, not handled",
    ]
    assert kb_problems({"", "list", "delete"}, {"": False, "list": False, "delete": True},
                       lambda text: False) == [
        "/kb delete: the page says admin only=True, the gate disagrees"]

