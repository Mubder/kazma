"""A pattern that reads FOLDED text is written folded (the memory plan's W6, step 2).

``documents.arabic.fold_for_search`` writes ة as ه, ى as ي, أ إ آ as ا and ئ as
ي before a text is compared. A pattern that holds a word as Arabic spells it
then never matches: "عطني قائمة الخطط" was no list (the pattern held "قائمة"
and the text "قايمه") while "List the plans" was one, "عطني لستة" the same, and
"ومتى؟" went on from nothing while "And when?" did. English has no such pair,
so nobody saw it.

The class: every Arabic word of a regular expression, and of a comparison with
folded text, in every module that folds -- however the expression is written
(compiled or not, at the top or in a function, joined from pieces or from a
table, an f-string, a class's attribute). A word the fold changes must stand
beside its folded spelling, or the expression is declared. The live ones, and
eighteen planted ways of writing one, are the control.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_RE = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"}
_COMPARES = {"startswith", "endswith", "removeprefix", "removesuffix", "replace", "split", "partition", "count", "find", "index"}
#: A table asked whether it holds a folded word: ``_ORG_HEADS.__contains__(word)``.
_HOLDS = {"__contains__", "get", "index", "count", "issuperset", "intersection", "isdisjoint"}
_WORD = re.compile("[\u0621-\u0652\u0670-\u06d3]+")

#: (file, words that are not folded): why they may stand as they are written.
DECLARED = {
    ("memory/arabic_facts.py", frozenset({"يٱ"})): "a range of letters",
    ("memory/arabic_facts.py", frozenset({"أ"})): "_says_i asks the word as written (w.text): folded, أ is no 'I'",
    ("memory/clause_subject.py", frozenset({"أ"})): "the word as written: folded, أ is no 'I'",
    ("memory/speakers.py", frozenset({"آ", "آخر", "آدم", "آسف", "آلة", "آمن", "آية"})): "refers_to_asker reads the word as "
                                                                                 "written (unjoined)",
    ("memory/held_facts.py", frozenset({"ة"})): "_Said reads whether the word was written with a taa marbuta",
    ("memory/query_terms.py", frozenset({"إلى", "الى", "على", "متى"})): "_word_pattern's terms are folded already "
                                                                       "(content_terms); a name it shares is another function's",
    ("memory/fact_names.py", "ASKED_WITH"): "_all_mine joins the English words of the table alone (isascii)",
    ("memory/forget_request.py", frozenset({"انسَ", "إنسَ"})): "_FORGET_AR_RAW is asked of the text as written",
    ("memory/own_words.py", frozenset({"ةه", "إعادة", "اعادة"})): "_FORWARD_LINE_RE reads a pasted line as written",
    ("memory/time_words.py", frozenset({"سأ"})): "_WILL_AR is asked of the text as written: folded, it begins many nouns",
    ("skills/lesson_signal.py", "_phrases"): "_phrases folds every phrase it joins (_fold)",
}


def _arabic(node: ast.AST) -> list[str]:
    return [w for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)
            for w in _WORD.findall(c.value) if any(_LETTER.match(ch) for ch in w)]


#: A letter, not a mark or the stretch: a range of marks is no word.
_LETTER = re.compile("[\\u0621-\\u063a\\u0641-\\u064a\\u0671-\\u06d3]")


def _named(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """What each name and attribute of a module is given, wherever."""
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for t in ast.walk(target):
                    name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else None
                    if name:
                        out.setdefault(name, []).append(node.value)
        elif isinstance(node, (ast.For, ast.comprehension)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, []).append(node.iter)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).extend(r.value for r in ast.walk(node) if isinstance(r, ast.Return) and r.value)
    return out


def unfolded(source: str) -> list[tuple[int, frozenset[str]]]:
    """(line, words the fold changes) of every regular expression of *source*,
    and of every comparison with a folded text, that holds such a word and not
    its folded spelling."""
    from kazma_core.documents.arabic import fold_for_search

    tree = ast.parse(source)
    named = _named(tree)

    words_of: dict[str, set[str]] = {}

    def reach(node: ast.AST) -> set[str]:
        """The Arabic words of *node* and of every name it uses, wherever given."""
        words = set(_arabic(node))
        for x in ast.walk(node):
            name = x.id if isinstance(x, ast.Name) else x.attr if isinstance(x, ast.Attribute) else None
            if name in named:
                if name not in words_of:
                    words_of[name] = set()  # a name given itself
                    words_of[name] = {w for value in named[name] if not is_folded(value) for w in reach(value)}
                words |= words_of[name]
        return words

    known: dict[str, bool] = {}

    def is_folded(node: ast.AST) -> bool:
        text = ast.unparse(node)
        if "fold" in text.replace("casefold", ""):
            return True
        return any(name_is_folded(x.id) for x in ast.walk(node) if isinstance(x, ast.Name) and x.id in named)

    def name_is_folded(name: str) -> bool:
        if name not in known:
            known[name] = False  # a name given itself: not by that
            known[name] = any(is_folded(v) for v in named[name])
        return known[name]

    out: dict[int, set[str]] = {}

    def look(line: int, words: set[str] | list[str]) -> None:
        bad = {w for w in words if fold_for_search(w) != w and fold_for_search(w) not in words}
        if bad:
            out.setdefault(line, set()).update(bad)

    regex = {"re", "regex", "_re"} | {a.asname for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
                                     if a.name in ("re", "regex") and a.asname}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            on = node.func.value if isinstance(node.func, ast.Attribute) else None
            if name in _RE and node.args and (on is None or getattr(on, "id", "") in regex):
                if "fold_for_search" not in ast.unparse(node.args[0]):
                    look(node.lineno, reach(node.args[0]))
            elif name == "partial" and len(node.args) > 1 and getattr(node.args[0], "attr", "") in _RE:
                look(node.lineno, reach(node.args[1]))
            elif name in _COMPARES and on is not None and is_folded(on):
                look(node.lineno, {w for a in node.args if not is_folded(a) for w in reach(a)})
            elif name in _HOLDS and on is not None and any(is_folded(a) for a in node.args) and not is_folded(on):
                look(node.lineno, reach(on))
        elif isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            for one, other in zip(sides, sides[1:], strict=False):
                for text, words in ((one, other), (other, one)):
                    if is_folded(text) and not is_folded(words):
                        look(node.lineno, reach(words))
    return sorted((line, frozenset(words)) for line, words in out.items())


def _files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split() if re.match(r"kazma-[a-z]+/kazma_[a-z]+/", p)]


def _declared(path: str, words: frozenset[str], source: str, line: int) -> tuple | None:
    for (name, what) in DECLARED:
        if path.endswith(name) and (words == what if isinstance(what, frozenset) else what in source):
            return (name, what)
    return None


def test_no_pattern_waits_for_a_spelling_the_fold_took_away():
    asked, wrong, used = 0, [], set()
    for path in _files():
        source = (ROOT / path).read_text(encoding="utf-8")
        if "fold" not in source:
            continue
        asked += 1
        for line, words in unfolded(source):
            why = _declared(path, words, source, line)
            if why:
                used.add(why)
            else:
                wrong.append((path, line, sorted(words)))
    assert asked > 40
    assert wrong == []
    assert used == set(DECLARED), "declared, and nothing there any more"


@pytest.mark.parametrize(("pattern", "read", "lost"), [
    ("قائمة|كم مره", "fold_for_search(said).lower()", {"قائمة"}),
    ("و(?:وين|متى)", "folded", {"متى"}),
    ("زائد", "text", {"زائد"}),
    ("قائمة|قايمه", "folded", set()),  # beside its folded spelling
    ("قايمه|كم مره", "folded", set()),
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
    assert set().union(*(words for _line, words in unfolded(source))) == lost


#: The ways of writing one that a gate of compiled names alone does not see.
PLANTED = [
    'def f(s):\n    return re.search(r"قائمة", fold(s))',
    'def f(s):\n    p = re.compile("قائمة")\n    return p.search(fold(s))',
    'def f(s):\n    return re.compile(rf"(?:{X})").search(fold(s))\nX = "قائمة"',
    'P = re.compile("(?:" + "|".join(W) + ")")\nW = ("قائمة", "كم")\ndef f(s):\n    return P.search(fold(s))',
    'class C:\n    P = re.compile("قائمة")\n    def f(self, s):\n        return self.P.search(fold(s))',
    'PS = [re.compile("قائمة"), re.compile("كم")]\ndef f(s):\n    return any(p.search(fold(s)) for p in PS)',
    'PS = {"a": re.compile("قائمة")}\ndef f(s):\n    return PS["a"].search(fold(s))',
    'def build():\n    return re.compile("(?:في|الى)")\ndef f(s):\n    return build().search(fold(s))',
    'P: re.Pattern[str] = re.compile("قائمة")\ndef f(s):\n    t = fold(s)\n    return P.search(t)',
    'P = re.compile("قائمة")\ndef f(folded):\n    return P.search(folded)',
    'P = re.compile("قائمة")\ndef g(t):\n    return P.search(t)\ndef f(s):\n    return g(fold(s))',
    'async def f(s):\n    return re.sub("قائمة", " ", fold(s))',
    'def names():\n    return "قائمة|كم"\ndef f(s):\n    return re.search(names(), fold(s))',
    'def f(s):\n    return re.split("متى", fold(s))',
    'def f(s):\n    return "قائمة" in fold(s)',
    'def f(s):\n    t = fold(s)\n    return t.startswith(("قائمة", "كم"))',
    'def f(s):\n    return [w for w in fold(s).split() if w == "قائمة"]',
    'def f(s):\n    words = fold(s).split()\n    return any(w in ("متى", "كم") for w in words)',
    # The delta check of the review: a table named in a comparison, an alias of re, partial, __contains__.
    'HEADS = {"مؤسسه", "بنك"}\ndef f(s):\n    return fold(s) in HEADS',
    'HEADS = frozenset({"هيئه"})\ndef f(s):\n    w = fold(s)\n    return w not in HEADS',
    'WORDS = {"متى": 1}\ndef f(s):\n    return WORDS.get(fold(s))',
    'import re as r2\ndef f(s):\n    return r2.search("قائمة", fold(s))',
    'import functools\nP = functools.partial(re.search, "قائمة")\ndef f(s):\n    return P(fold(s))',
    'HEADS = {"مؤسسه"}\ndef f(s):\n    return HEADS.__contains__(fold(s))',
    'class W:\n    head = ""\nHEADS = ("هيئه",)\ndef f(w):\n    return w.folded in HEADS',
]


@pytest.mark.parametrize("planted", PLANTED)
def test_control_each_way_of_writing_one_is_found(planted):
    source = "import re\nfold = fold_for_search\n" + planted
    assert unfolded(source), planted
    for wrong, right in (("قائمة", "قايمه"), ("متى", "متي"), ("الى", "الي"), ("مؤسسه", "موسسه"), ("هيئه", "هييه")):
        source = source.replace(wrong, right)
    assert unfolded(source) == [], "control: folded, it is fine"


def test_control_a_pattern_read_as_written_is_declared():
    """Nothing in the source says whether a text is folded far away: such a pattern is named above."""
    assert unfolded('import re\n_P = re.compile("قائمة")\ndef asks(said):\n    return _P.search(said)\n')


@pytest.mark.parametrize(("english", "gulf", "msa"), [("Where was I living until March?", "وين كنت ساكن لين مارس؟", "أين كنت أسكن إلى مارس؟")])
def test_until_a_month_is_a_time_in_every_language(english, gulf, msa):
    """The fifth live one (the review of step 2): "الى" never matched folded text."""
    from datetime import UTC, datetime

    from kazma_core.memory.time_words import read_time

    now = datetime(2026, 11, 25, tzinfo=UTC).timestamp()
    asks = [read_time(q, now=now, zone=UTC) for q in (english, gulf, msa, "وين كنت ساكن إلى مارس؟")]
    assert all(a is not None and a.period for a in asks), asks
    assert len({(a.start, a.end) for a in asks}) == 1


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


@pytest.mark.parametrize(("gulf", "msa", "employer"), [
    ("أشتغل في مؤسسة البترول الكويتية", "أعمل في مؤسسة البترول الكويتية", "مؤسسة البترول الكويتية"),
    ("أشتغل في هيئة الاستثمار", "أعمل في هيئة الاستثمار", "هيئة الاستثمار")])
def test_an_employer_with_a_hamza_is_an_employer(gulf, msa, employer):
    """The delta check of the review: "مؤسسة" and "هيئة" were read folded and held unfolded; "شركة" was found."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    for said in (gulf, msa):
        assert ("works_at", employer) in {(b["predicate"], b["object"]) for b in extract_beliefs_heuristic(said)}, said


@pytest.mark.parametrize("asked", ["When do I move to Dubai?", "متى أسكن في دبي؟", "متى سأسكن في دبي؟", "متى أعمل في زين؟"])
def test_a_question_is_never_filed_as_the_users_statement(asked):
    """"متى" was held unfolded and read folded: "متى أسكن في دبي؟" filed the user's home (§15 H)."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    assert extract_beliefs_heuristic(asked) == []
