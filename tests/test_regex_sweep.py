r"""Every regex literal in product code stays linear on hostile input, or says why not.

Sonar's S8786 scan (2026-11-16) named 80 patterns with super-linear
backtracking; measured, 25 were quadratic or worse on text from outside
Kazma (a model's reply, a chat message, a fetched page, a document, an
installed skill), some on the event loop. They were fixed one by one
(``tests/test_sonar_regexes_linear.py``). This is the class gate: every
regex literal Kazma passes to ``re`` -- ``re.compile`` and the module
functions, at module level or inside a function, in every product package --
is run against hostile inputs built from its own characters, and one that
grows faster than its input fails unless it is declared below with why it is
contained (its input is Kazma's own, or bounded, or read in a worker process
with a deadline).

The inputs, per pattern: each character the pattern names (literals, the
ends of its classes, a representative of ``\s``, ``\d``, ``\w``), and the
whitespace characters, repeated 3,000 times then a character no pattern
names; the same after the pattern's literal head (``<`` before the spaces of
``<\s*\|?\s*dsml``); and the head itself repeated (``![![![`` for an image
pattern). A pattern is used the way its call uses it: ``match`` and
``fullmatch`` anchored, everything else read to the end (as ``sub`` and
``findall`` do). Each input is run at a quarter of the size, then the whole
size, and one that takes over :data:`SUSPECT_S` there at four times the size:
slow means more than :data:`SLOW_S` at four times and more than eight times
the whole size's time (linear grows four-fold, quadratic sixteen), or more
than :data:`CLEARLY_SLOW_S` at the quarter or the whole size. The first slow
input names the pattern. A quadratic pattern with a small constant
(``<[^>]+>``: 3 ms at 3,000 characters, 0.5 s at 40,000) is caught by the
growth, not by a time at one size.

The patterns run in a child process, one at a time; a pattern that does not
finish within :data:`PATTERN_TIMEOUT_S` is named (the child is stopped and
started again after it), so an exponential pattern cannot hang the suite.
The child's own start (Python, this module, the scan of every product file)
is not a pattern's time: it says it is ready first, and only then does a
pattern's clock run. Counted against the first pattern, a busy CI runner's
start named ``release_signature.py:58``, a linear pattern, as hung
(2026-11-21, two CI runs in four).
"""

from __future__ import annotations

import ast
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from re import _constants as sre_c
from re import _parser as sre_parse

import pytest

REPO = Path(__file__).resolve().parents[1]
N = 3000
SUSPECT_S = 0.0005     # at N characters: most linear patterns take a tenth of that
SLOW_S = 0.008         # at 4 N characters, and more than 8 times the time at N
CLEARLY_SLOW_S = 0.1   # at N/4 or N characters: no linear pattern comes near
PATTERN_TIMEOUT_S = 10.0
TAIL = chr(0x2603)  # a snowman: no product pattern names one
#: A tail that fails late: a word, a stop, a word. The snowman is a non-word
#: character and completes a pattern ending ``\W*$`` at once, so a quadratic
#: head (``no,`` + "-" * n, step 4's delta review N2) answered fast.
LATE_TAIL = "a.a"

#: The packages whose regexes are swept (``kazma-*/kazma_*``, not their tests).
PACKAGES = ("kazma-cli/kazma_cli", "kazma-core/kazma_core", "kazma-gateway/kazma_gateway",
            "kazma-skills/kazma_skills", "kazma-tui/kazma_tui", "kazma-ui/kazma_ui")
_RE_MODULES = {"re", "_re"}
#: Where each function takes its flags (positional index).
_FLAG_INDEX = {"compile": 1, "search": 2, "match": 2, "fullmatch": 2, "findall": 2,
               "finditer": 2, "sub": 4, "subn": 4, "split": 3}


@dataclass(frozen=True)
class Literal:
    path: str      # relative to the repository, with "/"
    line: int
    pattern: str
    flags: int
    op: str        # "match", "fullmatch" or "scan"

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"


#: Slow on generated input, and contained: (path, pattern) -> why (its input is
#: Kazma's own, bounded, or read in a worker or child process with a deadline).
#: A pattern on outside text that is not contained is fixed, never declared.
DECLARED: dict[tuple[str, str], str] = {
    ('kazma-cli/kazma_cli/setup.py',
     '^[\\s▀-▟\\-\\\\|/.]*(\\d+(\\.\\d+)?\\s*[KMGT]?i?B\\s*/\\s*\\d+(\\.\\d+)?\\s*[KMGT]?i?B|\\d{1,3}(\\.\\d+)?\\s*%)?[\\s▀-▟]*$'):
        "A line of an installer's own progress output (winget, uv) on this machine.",
    ('kazma-cli/kazma_cli/uninstall.py',
     '<WorkingDirectory>(.*?)</WorkingDirectory>'):
        "The XML schtasks prints for Kazma's own scheduled task.",
    ('kazma-cli/kazma_cli/uninstall.py',
     '<Arguments>"?(.*?)[\\\\/]scripts[\\\\/]service'):
        "The XML schtasks prints for Kazma's own scheduled task.",
    ('kazma-cli/kazma_cli/uninstall.py',
     '(\\S+?)[\\\\/]scripts[\\\\/]service[\\\\/]kazma_guard\\.py'):
        'The service definition setup wrote (a systemd unit or a launchd plist).',
    ('kazma-core/kazma_core/documents/glyph_fallback.py',
     '<[^>]*>|&[#A-Za-z0-9]+;'):
        'ReportLab paragraph markup, in the document worker process, under its time limit.',
    ('kazma-core/kazma_core/documents/heading_text.py',
     '\\([^)]*\\)'):
        'Document headings, in the renderer and its engines (the document worker process).',
    ('kazma-core/kazma_core/documents/heading_text.py',
     '\\s+[—–·|/-]\\s+'):
        "Split on heading_key's output, which folds every whitespace run to one space first (0.13 ms at 100,000 through heading_key).",
    ('kazma-core/kazma_core/documents/rich_render.py',
     '\\*\\*(.+?)\\*\\*|__(.+?)__|(?<!\\*)\\*(?!\\*)(.+?)(?<!\\*)\\*(?!\\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|`([^`]+)`|\\[([^\\]]+)\\]\\(([^)]+)\\)'):
        'Document Markdown, rendered by the engines inside the document worker process, under its time limit.',
    ('kazma-core/kazma_core/memory/backfill_v2.py',
     '\\s*```$'):
        "A model's JSON reply in the one-off V1 backfill, bounded by its output limit (the fix belief_extractor.py now has; this file is not changed here).",
    ('kazma-core/kazma_core/memory/legacy_tables.py',
     'User:\\s*(.*?)\\s*\\nAssistant:\\s*(.*)\\Z'):
        'V1 migration copies only (legacy-* sessions): fixed historical rows, no new input.',
    ('kazma-core/kazma_core/observability/daily_digest.py',
     '\\[[^\\]\\s]+\\] LLM call attempt \\d+/\\d+ failed'):
        "Kazma's own log lines.",
    ('kazma-core/kazma_core/security/service_exposure.py',
     '(?P<host>\\[[0-9a-fA-F:.]*\\]|[0-9.]+):(?P<port>\\d+)->(?P<inner>\\d+)/(?P<proto>tcp|udp)'):
        "The Ports column of docker ps (Docker's own output).",
    ('kazma-core/kazma_core/stores/knowledge_ingest.py',
     '<[^>]+>'):
        'Read only when the page is under 500 characters.',
    ('kazma-core/kazma_core/tools/research_planner.py',
     '\\{[\\s\\S]*\\}'):
        "The planner's reply, max_tokens 800-1,200 (0.03 s at 4,096).",
    ('kazma-core/kazma_core/tools/research_planner.py',
     '```(?:json)?\\s*([\\s\\S]*?)```'):
        "The planner's reply, max_tokens 800-1,200 (a few thousand characters: 0.06 s at 4,096).",
    ('kazma-core/kazma_core/tools/research_session.py',
     '(\\d+)\\s+sources'):
        "Kazma's own progress line ('Digesting N sources').",
    ('kazma-core/kazma_core/web_acquire/html_extract.py',
     '(?is)<body[^>]*>(.*)</body>'):
        'A fetched page, read in the parser child process (a 15 s deadline): only that page fails.',
    ('kazma-core/kazma_core/web_acquire/html_extract.py',
     '<[^>]+>'):
        'A fetched page, read in the parser child process (a 15 s deadline): only that page fails.',
    # Seen once the sweep read a head through an alternation ("<script", step 4's review).
    ('kazma-core/kazma_core/web_acquire/html_extract.py',
     r'(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>'):
        'A fetched page, read in the parser child process (a 15 s deadline): only that page fails.',
    ('kazma-core/kazma_core/web_acquire/search_http.py',
     '<[^>]*>'):
        'Search results, parsed in the search child process (a fixed deadline).',
    ('kazma-core/kazma_core/x_api/_text/regexp/valid_ascii_domain.py',
     '(?:(?:[\\-a-z0-9#{latin_accent_chars}]+)\\.)+(?:#{valid_gtld}|#{valid_cctld}|#{valid_punycode})'):
        'A template: regex_supplant fills its #{...} names, so this literal is never matched as written.',
    ('kazma-gateway/kazma_gateway/adapters/telegram_send.py',
     '<[^>]+>'):
        "Telegram HTML Kazma built: md_to_tg_html escapes every '<' of the reply itself, so only Kazma's own tags are read.",
    ('kazma-gateway/kazma_gateway/routers/github_client.py',
     '<([^>]+)>'):
        "A segment of GitHub's own Link header.",
    ('kazma-skills/kazma_skills/native/email_manager/analyze.py',
     '\\s*```$'):
        'The e-mail analysis reply, max_tokens=800.',
    ('kazma-skills/kazma_skills/native/email_manager/analyze.py',
     '\\{[\\s\\S]*\\}'):
        'The e-mail analysis reply, max_tokens=800.',
}


def _flags(node: ast.AST | None) -> int | None:
    if node is None:
        return 0
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in _RE_MODULES:
        value = getattr(re, node.attr, None)
        return int(value) if isinstance(value, re.RegexFlag) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flags(node.left), _flags(node.right)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def product_files(packages: tuple[str, ...] = PACKAGES, root: Path = REPO) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        for path in sorted((root / package).rglob("*.py")):
            parts = path.relative_to(root).parts
            if any(part in {"tests", "__pycache__"} or part.endswith("_tests") for part in parts):
                continue
            files.append(path)
    return files


def _anchored_names(tree: ast.AST) -> dict[str, str]:
    """Names bound to a compiled pattern that the module only ever anchors.

    A private ``_NAME = re.compile(...)`` used only as ``_NAME.match(...)`` or
    ``_NAME.fullmatch(...)`` is measured that way: scanned from every position
    it would be judged on starts the code never tries.
    """
    uses: dict[str, set[str]] = {}
    bases: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            uses.setdefault(node.value.id, set()).add(node.attr)
            bases.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and id(node) not in bases:
            uses.setdefault(node.id, set()).add("")  # passed on whole: any use
    anchored: dict[str, str] = {}
    for name, attrs in uses.items():
        # A public name may be used another way in another module.
        if name.startswith("_") and attrs and attrs <= {"match", "fullmatch"}:
            anchored[name] = "match" if "match" in attrs else "fullmatch"
    return anchored


def regex_literals(files: list[Path], root: Path = REPO) -> list[Literal]:
    """Every ``re.<function>("literal", ...)`` call in *files*."""
    found: list[Literal] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        anchored = _anchored_names(tree)
        bound: dict[int, str] = {}  # id of a re.compile call -> the name it is assigned to
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                bound[id(node.value)] = node.targets[0].id
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in _RE_MODULES
                    and node.func.attr in _FLAG_INDEX and node.args):
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            index = _FLAG_INDEX[node.func.attr]
            flag_node = node.args[index] if len(node.args) > index else next(
                (k.value for k in node.keywords if k.arg == "flags"), None)
            flags = _flags(flag_node)
            if flags is None:
                flags = 0
            op = node.func.attr if node.func.attr in {"match", "fullmatch"} else "scan"
            if node.func.attr == "compile" and bound.get(id(node)) in anchored:
                op = anchored[bound[id(node)]]
            found.append(Literal(path.relative_to(root).as_posix(), node.lineno, first.value, flags, op))
    return found


# ── hostile inputs ────────────────────────────────────────────────────────

def _characters(pattern: str, flags: int) -> tuple[list[str], str]:
    """The characters *pattern* names, and its literal head."""
    names: set[str] = set()
    category = {sre_c.CATEGORY_DIGIT: "0", sre_c.CATEGORY_SPACE: " ", sre_c.CATEGORY_WORD: "a",
                sre_c.CATEGORY_NOT_DIGIT: "a", sre_c.CATEGORY_NOT_SPACE: "a", sre_c.CATEGORY_NOT_WORD: " ",
                sre_c.CATEGORY_LINEBREAK: "\n", sre_c.CATEGORY_NOT_LINEBREAK: "a"}

    def walk(items) -> None:
        for op, av in items:
            if op in (sre_c.LITERAL, sre_c.NOT_LITERAL):
                names.add(chr(av))
            elif op is sre_c.IN:
                for sub_op, sub_av in av:
                    if sub_op is sre_c.LITERAL:
                        names.add(chr(sub_av))
                    elif sub_op is sre_c.RANGE:
                        names.update((chr(sub_av[0]), chr(sub_av[1])))
                    elif sub_op is sre_c.CATEGORY and sub_av in category:
                        names.add(category[sub_av])
            elif op is sre_c.ANY:
                names.add("a")
            elif op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT, sre_c.POSSESSIVE_REPEAT):
                walk(av[2])
            elif op is sre_c.SUBPATTERN:
                walk(av[3])
            elif op is sre_c.ATOMIC_GROUP:
                walk(av)
            elif op in (sre_c.ASSERT, sre_c.ASSERT_NOT):
                walk(av[1])
            elif op is sre_c.BRANCH:
                for branch in av[1]:
                    walk(branch)
            elif op is sre_c.GROUPREF_EXISTS:
                walk(av[1])
                if av[2]:
                    walk(av[2])

    parsed = sre_parse.parse(pattern, flags)
    walk(parsed)
    printable = sorted(c for c in names if c.isprintable() or c in " \t\n")
    return printable[:48], _literal_head(list(parsed))[0]


def _literal_head(items: list) -> tuple[str, bool]:
    """The literal text a pattern's match begins with, and whether it ran to
    the end of *items*. An anchor and a leading run that may be empty
    (``^\\W*``) are stepped over, and an alternation gives its first branch's
    head (``(?:no|nope|لا)``: "no"): a quadratic tail behind ``^\\W*(?:no|..)``
    was out of the sweep's sight (step 4's review, M2)."""
    head = ""
    for op, av in items:
        if op is sre_c.LITERAL:
            head += chr(av)
        elif op is sre_c.AT:
            continue
        elif op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT, sre_c.POSSESSIVE_REPEAT) and av[0] == 0 and not head:
            continue
        elif op is sre_c.SUBPATTERN:
            more, whole = _literal_head(list(av[3]))
            head += more
            if not whole:
                return head, False
        elif op is sre_c.BRANCH and av[1]:
            more, _whole = _literal_head(list(av[1][0]))
            return head + more, False
        else:
            return head, False
    return head, True


@dataclass(frozen=True)
class Recipe:
    """A hostile input: *prefix*, then *unit* repeated to about n characters, then :data:`TAIL`."""

    prefix: str
    unit: str
    tail: str = TAIL

    def text(self, n: int) -> str:
        return self.prefix + self.unit * max(1, n // len(self.unit)) + self.tail

    def __str__(self) -> str:
        return ((f"{self.prefix!r} + " if self.prefix else "") + f"{self.unit!r} * n"
                + (f" + {self.tail!r}" if self.tail != TAIL else ""))


_CATEGORY_TEST = {
    sre_c.CATEGORY_DIGIT: str.isdigit, sre_c.CATEGORY_NOT_DIGIT: lambda c: not c.isdigit(),
    sre_c.CATEGORY_SPACE: str.isspace, sre_c.CATEGORY_NOT_SPACE: lambda c: not c.isspace(),
    sre_c.CATEGORY_WORD: lambda c: c.isalnum() or c == "_",
    sre_c.CATEGORY_NOT_WORD: lambda c: not (c.isalnum() or c == "_"),
    sre_c.CATEGORY_LINEBREAK: lambda c: c == "\n", sre_c.CATEGORY_NOT_LINEBREAK: lambda c: c != "\n",
}
_SAMPLE_CANDIDATES = "a x ,-.0\tا"


def _in_class(items: list, ch: str) -> bool | None:
    """Whether a parsed character class holds *ch* (None: one it cannot read)."""
    negate, hit = False, False
    for op, av in items:
        if op is sre_c.NEGATE:
            negate = True
        elif op is sre_c.LITERAL:
            hit = hit or ch == chr(av)
        elif op is sre_c.RANGE:
            hit = hit or av[0] <= ord(ch) <= av[1]
        elif op is sre_c.CATEGORY and av in _CATEGORY_TEST:
            hit = hit or _CATEGORY_TEST[av](ch)
        else:
            return None
    return hit != negate


def _sample(items: list, named: set[int]) -> tuple[str, bool]:
    """Text the opening of *items* matches, up to a named group (a value):
    an optional group that begins with a literal is taken once ("يا <name>"
    after "no,"), a class gives a character it holds. Whether it ran to the
    end of *items*."""
    out = ""
    for op, av in items:
        if op in (sre_c.AT, sre_c.ASSERT, sre_c.ASSERT_NOT):
            continue
        if op is sre_c.LITERAL:
            out += chr(av)
        elif op is sre_c.ANY:
            out += "a"
        elif op is sre_c.IN:
            ch = next((c for c in _SAMPLE_CANDIDATES if _in_class(av, c)), None)
            if ch is None:
                return out, False
            out += ch
        elif op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT, sre_c.POSSESSIVE_REPEAT):
            lo, _hi, sub = av
            sub = list(sub)
            opens = sub[:1] and (sub[0][0] is sre_c.LITERAL or (
                sub[0][0] is sre_c.SUBPATTERN and list(sub[0][1][3])[:1]
                and list(sub[0][1][3])[0][0] is sre_c.LITERAL))
            if lo == 0 and not opens:
                continue
            more, whole = _sample(sub, named)
            out += more * max(1, min(lo, 3))
            if not whole:
                return out, False
        elif op is sre_c.SUBPATTERN:
            if av[0] in named:
                return out, False
            more, whole = _sample(list(av[3]), named)
            out += more
            if not whole:
                return out, False
        elif op is sre_c.BRANCH and av[1]:
            more, whole = _sample(list(av[1][0]), named)
            out += more
            if not whole:
                return out, False
        else:
            return out, False
    return out, True


def sample_head(pattern: str, flags: int) -> str:
    """The text a match of *pattern* opens with, through its optional
    literal-led groups, to its first named group: "no,يا a " for
    ``_NO_THEN``, whose vocative ran quadratic behind it (step 4's third
    review, A). "" when it is no longer than the literal head."""
    parsed = sre_parse.parse(pattern, flags)
    named = set(parsed.state.groupdict.values())
    text = _sample(list(parsed), named)[0]
    return text if len(text) > len(_literal_head(list(parsed))[0]) else ""


def hostile_inputs(pattern: str, flags: int) -> list[Recipe]:
    """The hostile inputs built from *pattern*'s own characters."""
    chars, head = _characters(pattern, flags)
    chars = sorted(set(chars) | {" ", "\t", "\n"})
    out = [Recipe("", c) for c in chars]
    if head:
        out += [Recipe(head, c) for c in chars]
        out.append(Recipe("", head))
    sampled = sample_head(pattern, flags)
    if sampled and sampled != head:
        out += [Recipe(sampled, c) for c in chars]
    out += [Recipe(r.prefix, r.unit, LATE_TAIL) for r in list(out)]
    return out


def _run(compiled: re.Pattern[str], op: str, text: str) -> float:
    start = time.perf_counter()
    if op == "match":
        compiled.match(text)
    elif op == "fullmatch":
        compiled.fullmatch(text)
    else:
        compiled.findall(text)  # every match, read to the end (as sub and finditer do)
    return time.perf_counter() - start


#: How many more runs a slow growth must survive before it names a pattern.
CONFIRMATIONS = 6


def measure(literal: Literal) -> dict | None:
    """The first input on which *literal* grows faster than linear, with its times, or None."""
    try:
        compiled = re.compile(literal.pattern, literal.flags)
    except (re.error, ValueError, TypeError):
        return None
    for recipe in hostile_inputs(literal.pattern, literal.flags):
        # A quarter first: a cubic pattern takes minutes at the full size.
        quarter = _run(compiled, literal.op, recipe.text(N // 4))
        if quarter > CLEARLY_SLOW_S:
            return {"input": str(recipe), "seconds at n/4": round(quarter, 4)}
        whole = _run(compiled, literal.op, recipe.text(N))
        if whole > CLEARLY_SLOW_S:
            return {"input": str(recipe), "seconds at n": round(whole, 4)}
        if whole < SUSPECT_S:
            continue
        four = _run(compiled, literal.op, recipe.text(4 * N))
        # Confirmed, so a late scheduling slice decides nothing: the fastest of
        # up to CONFIRMATIONS more runs. A busy CI runner paused both of two
        # runs of a plain character class (0.5 ms, then 20.6 ms, 2026-11-24).
        for _ in range(CONFIRMATIONS):
            if not (four > SLOW_S and four > 8 * whole):
                break
            whole = min(whole, _run(compiled, literal.op, recipe.text(N)))
            four = min(four, _run(compiled, literal.op, recipe.text(4 * N)))
        else:
            if four > SLOW_S and four > 8 * whole:
                return {"input": str(recipe), "seconds at n": round(whole, 4), "seconds at 4n": round(four, 4)}
    return None


# ── the child, and the watchdog around it ─────────────────────────────────

#: A test's way to make the child's start slow (seconds), as a loaded runner's is.
STARTUP_DELAY_VAR = "KAZMA_TEST_SWEEP_STARTUP_DELAY"


def _child(packages: list[str], start: int) -> None:
    root = Path(os.getcwd())
    literals = regex_literals(product_files(tuple(packages), root), root)
    delay = float(os.environ.get(STARTUP_DELAY_VAR) or 0)
    if delay:
        time.sleep(delay)
    print(json.dumps({"ready": len(literals)}), flush=True)
    for index in range(start, len(literals)):
        print(json.dumps({"begin": index}), flush=True)
        print(json.dumps({"done": index, "slow": measure(literals[index])}), flush=True)
    print(json.dumps({"end": len(literals)}), flush=True)


def sweep(packages: tuple[str, ...] = PACKAGES, root: Path = REPO, pattern_timeout: float = PATTERN_TIMEOUT_S,
          overall_timeout: float = 240.0, *, startup_is_a_pattern: bool = False,
          ) -> tuple[list[Literal], dict[int, dict], list[int]]:
    """Run every literal of *packages* in a child process: (literals, slow ones, hung ones).

    The child's start waits within *overall_timeout* only; a pattern's clock
    starts when the child is ready. *startup_is_a_pattern* is the reading
    before 2026-11-21 (the start counted against the first pattern), kept as
    the negative control."""
    literals = regex_literals(product_files(packages, root), root)
    slow: dict[int, dict] = {}
    hung: list[int] = []
    start = 0
    deadline = time.monotonic() + overall_timeout
    while start < len(literals):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys, json; sys.path.insert(0, sys.argv[1]);"
             " import test_regex_sweep as s; s._child(json.loads(sys.argv[2]), int(sys.argv[3]))",
             str(Path(__file__).parent), json.dumps(list(packages)), str(start)],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def pump(stream=proc.stdout) -> None:
            for raw in stream:
                lines.put(raw)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        current = start
        finished = False
        ready = startup_is_a_pattern
        while True:
            left = max(0.0, deadline - time.monotonic())
            wait = min(pattern_timeout, left) if ready else left
            try:
                raw = lines.get(timeout=wait)
            except queue.Empty:
                proc.kill()
                proc.wait(timeout=10)
                if not ready:
                    raise TimeoutError("the sweep's child did not start in time") from None
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"the sweep ran out of time on {literals[current].where} "
                                       f"{literals[current].pattern!r}") from None
                hung.append(current)
                start = current + 1
                break
            if raw is None:
                proc.wait(timeout=10)
                if not finished:
                    raise RuntimeError(f"the sweep's child ended early at {literals[current].where}: "
                                       f"{proc.stderr.read()[-2000:]}")
                start = len(literals)
                break
            record = json.loads(raw)
            if "ready" in record:
                ready = True
            elif "begin" in record:
                current = record["begin"]
            elif "done" in record:
                if record["slow"]:
                    slow[record["done"]] = record["slow"]
            elif "end" in record:
                finished = True
    return literals, slow, hung


def _report(literals: list[Literal], slow: dict[int, dict], hung: list[int]) -> list[str]:
    problems = []
    for index, found in sorted(slow.items()):
        lit = literals[index]
        if (lit.path, lit.pattern) not in DECLARED:
            problems.append(f"{lit.where} {lit.pattern!r} grows faster than its input: {found}")
    for index in hung:
        lit = literals[index]
        if (lit.path, lit.pattern) not in DECLARED:
            problems.append(f"{lit.where} {lit.pattern!r} did not finish within {PATTERN_TIMEOUT_S} s")
    return problems


# ── the tests ─────────────────────────────────────────────────────────────

#: Longer than the sweep's own limit (240 s), so a busy runner reads its answer:
#: CI's 120 s a test ended it at no pattern (2026-11-24).
@pytest.mark.timeout(300)
def test_every_product_regex_literal_is_linear_or_declared():
    literals, slow, hung = sweep()
    assert len(literals) > 500, len(literals)  # the scan found the product's patterns
    problems = _report(literals, slow, hung)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(("paused_runs", "named"), [(2, False), (10_000, True)])
def test_a_paused_runner_is_no_patterns_time(monkeypatch, paused_runs, named):
    """Two paused runs of a fast pattern decide nothing (with one
    confirmation they named it); a pattern that stays slow is named."""
    calls = {"four": 0}

    def run(compiled, op, text):
        if len(text) < 3 * N:
            return SUSPECT_S
        calls["four"] += 1
        slow = max(SLOW_S, 8 * SUSPECT_S) * 2
        return slow if calls["four"] <= paused_runs else 4 * SUSPECT_S

    monkeypatch.setattr(sys.modules[__name__], "_run", run)
    literal = Literal(*[{"pattern": "[a-z]", "flags": 0, "op": "sub"}.get(f, "x") for f in Literal.__dataclass_fields__])
    assert (measure(literal) is not None) is named


def test_every_declaration_names_a_pattern_that_exists():
    present = {(lit.path, lit.pattern) for lit in regex_literals(product_files())}
    stale = [key for key in DECLARED if key not in present]
    assert not stale, stale
    # A declaration says why the pattern is contained; an uncontained one is fixed.
    assert not [key for key, why in DECLARED.items() if "not contained" in why.lower()]


def test_control_a_planted_quadratic_pattern_fails(tmp_path: Path):
    package = tmp_path / "kazma-planted" / "kazma_planted"
    package.mkdir(parents=True)
    (package / "planted.py").write_text(
        "import re\n"
        "LINEAR = re.compile(r'(?<![ \\t])[ \\t]++\\n')\n"
        "QUADRATIC = re.compile(r'[ \\t]+\\n')\n"
        "SMALL_CONSTANT = re.compile(r'<[^>]+>')\n"  # 3 ms at 3,000 characters, 0.5 s at 40,000
        "TAGS = re.compile(r'<[^<>]+>')\n"
        # Quadratic only behind its head "no" (event_text._NO_THEN before step 4's review, M2).
        "HEADED = re.compile(r'^\\W*(?:no|nope)[ \\t]*[,.]+\\W*x')\n"
        # Quadratic only on a tail that fails late: the snowman completes "\W*$"
        # (event_text._NO_THEN at e4772d2ac, step 4's delta review N2).
        "LATE = re.compile(r'^\\W*(?:no|nope)[,.-]\\W*(?P<v>[^.,\\n]{1,60}?)\\W*$')\n"
        # Quadratic only behind an optional vocative, "no,يا <name>" (_NO_THEN at
        # de794c799, step 4's third review A): the sampled head builds it.
        "VOCATIVE = re.compile(r'^\\W*(?:no|nope),\\W*+(?:يا [^\\W\\d_]+\\W+)?(?P<v>[^.,\\n]{1,60}?)\\W*$')\n"
        "def strip(text):\n"
        "    return re.sub(r'\\s*->\\s*(\\S+)\\s*$', '', text)\n",
        encoding="utf-8",
    )
    literals, slow, hung = sweep(("kazma-planted/kazma_planted",), tmp_path)
    flagged = {literals[i].pattern for i in slow}
    assert flagged == {r"[ \t]+\n", r"<[^>]+>", r"\s*->\s*(\S+)\s*$", r"^\W*(?:no|nope)[ \t]*[,.]+\W*x",
                       r"^\W*(?:no|nope)[,.-]\W*(?P<v>[^.,\n]{1,60}?)\W*$",
                       r"^\W*(?:no|nope),\W*+(?:يا [^\W\d_]+\W+)?(?P<v>[^.,\n]{1,60}?)\W*$"}, (flagged, slow)
    assert not hung
    assert len(_report(literals, slow, hung)) == 6


def _plant_linear(tmp_path: Path) -> None:
    package = tmp_path / "kazma-planted" / "kazma_planted"
    package.mkdir(parents=True)
    (package / "planted.py").write_text(
        "import re\n"
        "FIRST = re.compile(r'https://example\\.com/[a-z]+')\n"
        "SECOND = re.compile(r'b+')\n",
        encoding="utf-8",
    )


def test_a_slow_child_start_is_no_patterns_time(tmp_path: Path, monkeypatch):
    # A loaded CI runner's start took longer than a pattern's allowance, and the
    # first pattern -- linear -- was named as hung (2026-11-21).
    _plant_linear(tmp_path)
    monkeypatch.setenv(STARTUP_DELAY_VAR, "2.5")
    literals, slow, hung = sweep(("kazma-planted/kazma_planted",), tmp_path, pattern_timeout=1.5)
    assert len(literals) == 2 and not slow and not hung, (slow, hung)
    assert not _report(literals, slow, hung)


def test_control_the_start_counted_against_the_first_pattern_names_it(tmp_path: Path, monkeypatch):
    _plant_linear(tmp_path)
    monkeypatch.setenv(STARTUP_DELAY_VAR, "2.5")
    literals, slow, hung = sweep(("kazma-planted/kazma_planted",), tmp_path, pattern_timeout=1.5,
                                 startup_is_a_pattern=True)
    # Every start after a "hung" pattern is counted again: both linear patterns are named.
    assert [literals[i].pattern for i in hung] == [r"https://example\.com/[a-z]+", "b+"]


def test_control_a_pattern_that_never_finishes_is_named(tmp_path: Path):
    package = tmp_path / "kazma-planted" / "kazma_planted"
    package.mkdir(parents=True)
    (package / "planted.py").write_text(
        "import re\n"
        "EXPONENTIAL = re.compile(r'^(a|aa)+$')\n"
        "AFTER = re.compile(r'b+')\n",
        encoding="utf-8",
    )
    literals, slow, hung = sweep(("kazma-planted/kazma_planted",), tmp_path, pattern_timeout=3.0)
    assert [literals[i].pattern for i in hung] == ["^(a|aa)+$"]
    assert [lit.pattern for lit in literals] == ["^(a|aa)+$", "b+"]  # the one after it still ran
    problems = _report(literals, slow, hung)
    assert len(problems) == 1 and "did not finish" in problems[0] and "planted.py:2" in problems[0]
