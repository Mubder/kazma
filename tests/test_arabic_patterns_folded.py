"""A pattern that reads FOLDED text is written folded (the memory plan's W6, step 2).

``documents.arabic.fold_for_search`` writes ة as ه, ى as ي, أ إ آ as ا and ئ as
ي before a text is compared. A pattern that holds a word as Arabic spells it
then never matches: "عطني قائمة الخطط" was no list (the pattern held "قائمة"
and the text "قايمه") while "List the plans" was one, "عطني لستة" the same, and
"ومتى؟" went on from nothing while "And when?" did. English has no such pair,
so nobody saw it.

The class: every compiled pattern of the packages that is asked of text its
function folded. A word in it with a letter the fold changes must stand beside
its folded spelling, or the pattern is declared. The live ones are the control.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_ASKS = {"search", "match", "fullmatch", "findall", "finditer", "sub"}
_WORD = re.compile("[ؠ-ۿ]+")


#: A module whose patterns are built of words folded as they are built.
BUILT_FOLDED = {"kazma-core/kazma_core/skills/lesson_signal.py": "_phrases folds every phrase it joins (_fold)"}


def _words(node: ast.AST) -> list[str]:
    return [w for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)
            for w in _WORD.findall(c.value) if any(ch.isalpha() for ch in w)]


def unfolded(source: str) -> list[tuple[str, str]]:
    """(pattern, word) of every compiled pattern in *source* asked of folded
    text that holds a word the fold would change, and not the folded one."""
    from kazma_core.documents.arabic import fold_for_search

    tree = ast.parse(source)
    patterns: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Call) and getattr(value.func, "attr", "") == "compile":
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    patterns[target.id] = _words(value)
    out = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        folded = {t.id for a in ast.walk(func) if isinstance(a, ast.Assign) and "fold" in ast.unparse(a.value)
                  for t in a.targets if isinstance(t, ast.Name)}
        for call in ast.walk(func):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in _ASKS
                    and isinstance(call.func.value, ast.Name) and call.func.value.id in patterns and call.args):
                continue
            asked = ast.unparse(call.args[-1])
            if "fold" in asked or any(re.search(rf"\b{name}\b", asked) for name in folded):
                words = patterns[call.func.value.id]
                out |= {(call.func.value.id, w) for w in words if fold_for_search(w) not in words}
    return sorted(out)


def _files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split() if re.match(r"kazma-[a-z]+/kazma_[a-z]+/", p)]


def test_no_pattern_waits_for_a_spelling_the_fold_took_away():
    found = {}
    asked = 0
    for path in _files():
        source = (ROOT / path).read_text(encoding="utf-8")
        if "fold_for_search" in source:
            asked += 1
            for pattern, word in unfolded(source):
                found[path, pattern, word] = True
    assert asked > 20
    assert {path for path, *_ in found} >= set(BUILT_FOLDED), "declared, and nothing there any more"
    found = {key: True for key in found if key[0] not in BUILT_FOLDED}
    assert sorted(found) == []


@pytest.mark.parametrize(("pattern", "read", "lost"), [
    ("قائمة|كم مره", "fold_for_search(said).lower()", ["قائمة"]),
    ("و(?:وين|متى)", "folded", ["متى"]),
    ("زائد", "text", ["زائد"]),
    ("قائمة|قايمه", "folded", []),  # beside its folded spelling
    ("قايمه|كم مره", "folded", []),
])
def test_control_the_live_ones_are_found(pattern, read, lost):
    source = f'''
import re
_P = re.compile(r"{pattern}")
def asks(said):
    folded = fold_for_search(said).lower()
    text = " ".join(fold_for_search(said).split())
    return bool(_P.search({read}))
'''
    assert [word for _name, word in unfolded(source)] == lost


def test_control_a_pattern_read_as_written_is_fine():
    assert unfolded('import re\n_P = re.compile("قائمة")\ndef asks(said):\n    return _P.search(said)\n') == []


@pytest.mark.parametrize(("english", "arabic"), [
    ("List the plans I told you about", "عطني قائمة الخطط اللي قلت لك عنها"), ("list my books", "عطني لستة كتبي"),
    ("how many times", "كم مرة")])
def test_a_list_is_asked_for_in_either_language(english, arabic):
    from kazma_core.memory.question_shape import asks_for_all

    assert asks_for_all(english) and asks_for_all(arabic)


@pytest.mark.parametrize(("english", "arabic"), [("And when?", "ومتى؟"), ("And when does she leave?", "ومتى تسافر؟")])
def test_a_follow_up_is_one_in_either_language(english, arabic):
    from kazma_core.memory.question_shape import is_follow_up

    assert is_follow_up(english) and is_follow_up(arabic)
