"""The CLI reference and ``kazma help`` name every command the CLI runs.

On 2026-10-02 the reference's command tree lacked ``kazma doctor`` and
``kazma agent-skills`` -- the command that re-signs a refused skill -- and
nothing said so. The commands are read from ``main()``'s dispatch: each
``cmd == "x"`` / ``cmd in ("x", "alias")`` (the first name is the command,
the rest aliases).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "kazma-cli" / "kazma_cli" / "main.py"
REFERENCE = ROOT / "docs" / "docs" / "guide" / "cli-reference.md"
_HELP_WORDS = {"--help", "-h", "help"}


def _main_function() -> ast.FunctionDef:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def _dispatch_tests(fn: ast.FunctionDef) -> list[tuple[list[str], ast.If]]:
    """Every ``if/elif`` testing ``cmd``, with the names it accepts."""
    found: list[tuple[list[str], ast.If]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if not (isinstance(test.left, ast.Name) and test.left.id == "cmd" and len(test.ops) == 1):
            continue
        right = test.comparators[0]
        if isinstance(test.ops[0], ast.Eq) and isinstance(right, ast.Constant):
            found.append(([right.value], node))
        elif isinstance(test.ops[0], ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
            found.append(([e.value for e in right.elts if isinstance(e, ast.Constant)], node))
    return found


def cli_commands() -> list[str]:
    commands: list[str] = []
    for names, _node in _dispatch_tests(_main_function()):
        if set(names) & _HELP_WORDS:
            continue
        for name in names:
            # "agent_skills" is another spelling of "agent-skills"; "acp"
            # shares a branch with "ask" and is a command of its own.
            if name.replace("_", "-") not in commands:
                commands.append(name)
    return commands


def help_text_commands() -> set[str]:
    """First word of each ``print("  <command> ...")`` in the help branch."""
    for names, node in _dispatch_tests(_main_function()):
        if set(names) & _HELP_WORDS:
            words = set()
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"
                        and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    m = re.match(r"^  (\S+)\s", call.args[0].value)
                    if m:
                        words.add(m.group(1))
            return words
    raise AssertionError("main() has no help branch")


def tree_commands(markdown: str) -> set[str]:
    """Commands in the reference's ``kazma`` tree (``├── name`` / ``└── name``)."""
    block = markdown.split("## 2. `kazma`", 1)[1].split("```", 2)[1]
    return set(re.findall(r"[├└]── (\S+)", block))


def missing_from_reference(markdown: str) -> list[str]:
    tree = tree_commands(markdown)
    return [c for c in cli_commands() if c not in tree]


def test_the_dispatch_is_read() -> None:
    commands = cli_commands()
    assert {"status", "doctor", "serve", "agent-skills", "ask", "acp", "mcp", "migrate"} <= set(commands)
    assert "agent_skills" not in commands  # an alias, not a second command


def test_the_reference_tree_names_every_command() -> None:
    assert missing_from_reference(REFERENCE.read_text(encoding="utf-8")) == []


def test_kazma_help_names_every_command() -> None:
    assert [c for c in cli_commands() if c not in help_text_commands()] == []


def test_every_command_has_a_section() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    headings = re.findall(r"^#{2,3} .*?`kazma ([a-z-]+)", text, flags=re.MULTILINE)
    assert [c for c in cli_commands() if c not in headings] == []


def test_negative_control_a_missing_command_is_caught() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    without = re.sub(r"^├── agent-skills.*\n", "", text, flags=re.MULTILINE)
    assert without != text
    assert missing_from_reference(without) == ["agent-skills"]
