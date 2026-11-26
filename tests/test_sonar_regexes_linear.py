r"""The patterns Sonar's S8786 scan named that were quadratic or worse on outside text.

Measured on 2026-11-16 (the scan named 52 Python patterns; 22 were real,
the rest linear on the input the code gives them or bounded): each was slow
on text from outside Kazma -- a model's reply or tool argument, a chat
message, a fetched page, a document, an installed skill -- and CPython's
``re`` holds the GIL while it matches, so even the ones run in a thread
stalled the server. Worst first: a line of backticks in a ``generate_pdf``
body (about n**3.6, on the event loop), a fork-bomb test before every
``shell_exec`` card (about n**4), a WhatsApp heading (cubic, on the event
loop), a skill manifest, a config line in the security report, a document's
resources, a belief's date.

Each new pattern is held to the old one's answers on random inputs (an
intended difference is said and tested), and to a time on hostile input,
through the product function where one can be called; the old patterns'
growth is the negative control. The class gate is
``tests/test_regex_sweep.py``.
"""

from __future__ import annotations

import inspect
import random
import re
import time

from kazma_core import text_display
from kazma_core.agent import answer_format, graph_helpers, plan_fence, research_policy, semantic_compact, turn_input
from kazma_core.agent.tool_builtins import filesystem
from kazma_core.agent.turn_input import extract_active_attachments
from kazma_core.agent_skills import parser as skill_parser
from kazma_core.code_index import symbols
from kazma_core.documents import image_refs, renderer_worker, resources
from kazma_core.documents.errors import DocumentSecurityError
from kazma_core.memory import belief_extractor, current_facts, episode_text
from kazma_core.safety import shell_paths
from kazma_core.safety.commitment import authorize, relative_time
from kazma_core.security import dependency_scanner, secret_scan
from kazma_core.stores import knowledge_chunker, knowledge_ingest
from kazma_core.tools import research_eval
from kazma_core.web_acquire import html_extract
from kazma_gateway import telegram_format
from kazma_gateway.adapters import telegram as telegram_adapter
from kazma_gateway.adapters import voice_helpers, whatsapp_format
from kazma_gateway.agent_handler import swarm_dispatch

RNG = random.Random(20261116)

# ── the old patterns, as they were ────────────────────────────────────────

OLD_FENCED = re.compile(r"(?ms)^\s{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^\s{0,3}\1[^\n]*(?:\n|$)|\Z)")
OLD_ONE_PARAGRAPH = re.compile(r"\s*(?:one paragraph(?: only)?|single paragraph|فقرة واحدة(?: فقط)?)\s*[.!؟]?\s*", re.I)
OLD_DSML = re.compile(r"<\s*\|?\s*dsml")
OLD_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
OLD_STUB_SPLIT = re.compile(r"\s+[—\-]\s+")
OLD_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
OLD_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
OLD_ANY_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
OLD_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
OLD_RESOURCE = re.compile(
    r"""(?ix)
    (?:href|src|srcset)\s*=\s*["']?\s*(?P<html>[^"'>]+)
    |@import\s+(?P<import>[^;\s]+)
    |url\s*\(\s*["']?(?P<css>[^)"']+)
    |!\[[^\]]*\]\(\s*(?P<markdown>[^)\s]+)
    """
)
OLD_FILLER_WORDS = re.compile(r"^\s*(okay|ok|got it|noted|thanks|thank you|sure|yes|no|cool|nice|great)\s*[.!?]*\s*$", re.I)
OLD_FILLER_SOUNDS = re.compile(r"^\s*(lol|haha|hm+|uh+h*|um+)\s*$", re.I)
OLD_RESET_LINE = re.compile(
    r"(?:my\s+)?(?P<svc>grok(?:\s+next)?|supergrok|zcode|z\s*code|claude|cursor|"
    r"copilot|openai|chatgpt|gpt)"
    r"(?:\s+(?:weekly|pool|quota))?"
    r"(?:\s+next)?\s+reset"
    r"(?:\s*(?:time|at|is|:))?\s*"
    r"(?P<val>.+?)(?=(?:\.?\s*(?:and|my)\s+(?:grok|zcode|claude|cursor))|$)",
    re.IGNORECASE | re.DOTALL,
)
OLD_AND_MY = re.compile(r"\s+And\s+my\s+", re.I)
OLD_FORK_BOMB = re.compile(r":\s*\(\)\s*\{.*:.*\|.*&.*\}", re.IGNORECASE)
OLD_DD = re.compile(r"\bdd\b\s+.*of\s*=\s*/dev/", re.IGNORECASE)
OLD_TZ_QUALIFIER = re.compile(
    r"\s+(?:kuwait|asia/kuwait|gmt|utc|local|local time)\s*"
    r"(\((?:utc\s*)?[+-]\d{1,2}(?::\d{2})?\))?$",
    re.IGNORECASE,
)
OLD_TZ_PAREN_ONLY = re.compile(r"\s*\((?:utc\s*)?[+-]\d{1,2}(?::\d{2})?\)\s*$", re.IGNORECASE)
OLD_LOCAL_SUFFIX = re.compile(r"\s+(local|local time|utc|gmt)$", re.I)
OLD_SHELL_PROGRAM = re.compile(
    r"(?im)\bcommand\s*:\s*\[?\s*[\"']?(?:[^\s\"'\],]*[/\\])?(?:sh|bash|zsh|dash|cmd|powershell|pwsh)(?:\.exe)?[\"']?(?=[\s,\]]|$)"
)
OLD_BARE_ASSIGNMENT = re.compile(
    r"""(?ix)
    ^\s*(?:export\s+)?-?\s*
    (?P<name>[a-z0-9_.-]{0,40}?
        (?:api[_.-]?key|apikey|secret|token|passw(?:or)?d|passwd|pwd
          |private[_.-]?key|access[_.-]?key|auth[_.-]?key|credentials?)
        [a-z0-9_.-]{0,40})
    \s*[:=]\s*
    (?P<value>[A-Za-z0-9_+/=.@!%^*~-]{8,512})
    \s*(?:[#;].*)?$
    """
)
OLD_TRAILING_BLANKS = re.compile(r"[ \t]+\n")
OLD_WA_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
OLD_WA_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
OLD_WA_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
OLD_TARGET_SUFFIX = re.compile(r"\s*->\s*(\S+)\s*$")
# Found by the class gate (tests/test_regex_sweep.py) in the same files.
OLD_WA_BULLET = re.compile(r"(?m)^(\s*)\*\s+")
OLD_ATTACHMENT_PATH = re.compile(
    r"(?<![\w.:/\\-])((?:[A-Za-z]:)?[~\w./\\-]+\.(?:docx|pdf|pptx|xlsx|doc|txt|md|html))(?!\w|\.\w)", re.I)
OLD_N_UNITS = re.compile(r"(\d+)\s+(min(?:ute)?s?|hrs?|hours?|days?|weeks?|months?)", re.IGNORECASE)
OLD_AR_UNIT = re.compile(
    r"(?P<num>\d+|[٠-٩]+)?\s*"
    r"(?<![ء-غف-ي])(?P<unit>يومين|ساعتين|أسبوعين|اسبوعين|دقيقتين|"
    r"يوم|ايام|أيام|ساعة|ساعات|أسبوع|اسبوع|أسابيع|اسابيع|دقيقة|دقائق|شهر|أشهر|اشهر)"
    r"(?![ء-غف-ي])"
)
OLD_FENCE_TAIL = re.compile(r"\s*```$")
# Declared "not contained" by the first sweep, fixed the same day.
OLD_TG_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
OLD_TTS_LINK_TELEGRAM = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
OLD_TTS_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
OLD_TAG = re.compile(r"<[^>]+>")
OLD_PLAN = re.compile(r"```plan\b[^\n]*\n?(.*?)```", re.IGNORECASE | re.DOTALL)
OLD_ITEMS = re.compile(r"^\s*(?:\d{1,2}[.)]|[-•*])\s+\S", re.MULTILINE)
OLD_RESEARCH_LINE = re.compile(r"(?m)^\s*/research(\s+deep)?\b", re.I)
# Found by the second sweep (growth from 3,000 to 12,000 characters), fixed the same day.
OLD_QUOTED = re.compile(r'`+[^`]*`+|"[^"]*"|“[^”]*”|«[^»]*»|‘[^’]*’|(?<!\w)\'[^\']*\'(?!\w)')
OLD_KB_FENCE = re.compile(r"(?:```|~~~)[^\n]*\n.*?\n(?:```|~~~)[ \t]*$", re.DOTALL | re.MULTILINE)
OLD_KB_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
OLD_FILE_REF = re.compile(r"\bresearch/[\w./-]+\.md\b")
OLD_POST_QUOTE = re.compile(r'["“]([^"”]{4,})["”]')
OLD_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>`]+")
OLD_SYMBOLS = {
    "_PY_DEF": re.compile(r"^(\s*)(async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.M),
    "_PY_CLASS": re.compile(r"^(\s*)class\s+(\w+)", re.M),
    "_JS_FN": re.compile(r"^(\s*)(export\s+)?(async\s+)?function\s+(\w+)\s*\(", re.M),
    "_JS_CLASS": re.compile(r"^(\s*)(export\s+)?class\s+(\w+)", re.M),
    "_JS_ARROW": re.compile(r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s*)?\(", re.M),
}

# ── the new ones the product holds inline (each checked to be in its source) ─

NEW_ONE_PARAGRAPH = r"\s*+(?:one paragraph(?: only)?|single paragraph|فقرة واحدة(?: فقط)?)\s*+[.!؟]?\s*+"
NEW_DSML = r"<\s*+\|?\s*+dsml"
NEW_BRACES = r"\{([^{},]*,[^{}]*)\}"
NEW_STUB_SPLIT = r"(?<!\s)\s++[—\-]\s+"
NEW_AND_MY = r"(?<!\s)\s++And\s+my\s+"
NEW_LOCAL_SUFFIX = r"(?<!\s)\s++(local|local time|utc|gmt)$"
NEW_TRAILING_BLANKS = r"(?<![ \t])[ \t]++\n"
NEW_WA_HEADING = r"(?m)^\s{0,3}#{1,6}\s++(.+?)(?<!\s)[^\S\n]*+#*+[^\S\n]*+$"
NEW_WA_IMAGE = r"!\[([^\][]*)\]\(([^)\s[]+)\)"
NEW_WA_LINK = r"\[([^\][]+)\]\(([^)\s[]+)\)"
NEW_TARGET_SUFFIX = r"(?<!\s)\s*+->\s*+(\S+)\s*+$"
NEW_WA_BULLET = r"(?m)^([^\S\n]*)\*\s+"
NEW_ATTACHMENT_PATH = r"(?<![~\w.:/\\-])((?:[A-Za-z]:)?[~\w./\\-]+\.(?:docx|pdf|pptx|xlsx|doc|txt|md|html))(?!\w|\.\w)"
NEW_FENCE_TAIL = r"(?<!\s)\s*+```$"
NEW_TTS_LINK = r"\[([^\][]+)\]\([^)[]+\)"
NEW_TAG = r"<[^<>]+>"
NEW_RESEARCH_LINE = r"(?m)^[^\S\n]*/research(\s+deep)?\b"
NEW_KB_LINK = r"\[[^\][]*\]\(([^)\s[]+)\)"
#: The CommonMark code span ``image_refs._without_code_spans`` reads in one pass.
COMMONMARK_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _source_holds(module, pattern: str) -> None:
    source = inspect.getsource(module)
    assert ('r"' + pattern + '"') in source, f"{module.__name__} no longer holds {pattern!r}"


def _timed(fn, *args) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


def _random(alphabet, longest: int) -> str:
    return "".join(RNG.choice(alphabet) for _ in range(RNG.randint(0, longest)))


def _words(words: list[str], longest: int = 8) -> str:
    return "".join(RNG.choice(words) for _ in range(RNG.randint(0, longest)))


def _view(rx, s: str, op: str):
    if op == "search":
        m = rx.search(s)
        return m and (m.span(), m.groups())
    if op == "fullmatch":
        m = rx.fullmatch(s)
        return m and (m.span(), m.groups())
    if op == "split1":
        return rx.split(s, maxsplit=1)
    if op == "findall":
        return rx.findall(s)
    if op == "finditer":
        return [(m.span(), m.groups()) for m in rx.finditer(s)]
    if op == "bullet":
        return rx.sub(r"\1- ", s)
    return rx.sub("X", s)


def _same(old, new, op: str, inputs) -> None:
    new = re.compile(new, old.flags) if isinstance(new, str) else new
    for s in inputs:
        assert _view(old, s, op) == _view(new, s, op), s


def _grows_faster_than_linear(fn, make, small: int, factor: int = 4) -> bool:
    """True when ``factor`` times the input takes more than 1.5 x ``factor`` times as long.

    The majority of three measurements (two that agree end it): one noisy
    measurement on a busy machine must decide neither way (2026-11-21)."""
    fn(make(small))

    def once() -> bool:
        t1 = min(_timed(fn, make(small)) for _ in range(3))
        t2 = min(_timed(fn, make(factor * small)) for _ in range(3))
        return t2 > 1.5 * factor * t1

    first, second = once(), once()
    return first if first == second else once()


SPACE = " \t\n"


# ── the inline patterns are the product's ─────────────────────────────────

def test_the_product_holds_the_new_inline_patterns():
    _source_holds(answer_format, NEW_ONE_PARAGRAPH)
    _source_holds(graph_helpers, NEW_DSML)
    _source_holds(filesystem, NEW_BRACES)
    _source_holds(shell_paths, NEW_BRACES)
    _source_holds(turn_input, NEW_STUB_SPLIT)
    _source_holds(current_facts, NEW_AND_MY)
    _source_holds(relative_time, NEW_LOCAL_SUFFIX)
    _source_holds(html_extract, NEW_TRAILING_BLANKS)
    _source_holds(whatsapp_format, NEW_WA_HEADING)
    _source_holds(whatsapp_format, NEW_WA_IMAGE)
    _source_holds(whatsapp_format, NEW_WA_LINK)
    _source_holds(swarm_dispatch, NEW_TARGET_SUFFIX)
    _source_holds(whatsapp_format, NEW_WA_BULLET)
    _source_holds(turn_input, NEW_ATTACHMENT_PATH)
    _source_holds(belief_extractor, NEW_FENCE_TAIL)
    for module in (telegram_adapter, voice_helpers):
        _source_holds(module, NEW_TTS_LINK)
        _source_holds(module, NEW_TAG)
    _source_holds(research_policy, NEW_RESEARCH_LINE)
    _source_holds(knowledge_ingest, NEW_KB_LINK)
    assert not hasattr(knowledge_chunker, "_FENCE_RE")  # replaced by the one-pass _fence_spans
    assert not hasattr(relative_time, "_RE_BEFORE_THE_EN")  # read by nothing, quadratic: removed


# ── the same answers ──────────────────────────────────────────────────────

def test_answer_format_patterns_answer_as_before():
    _same(OLD_FENCED, answer_format._FENCED, "sub", (_random("`~ a\n", 18) for _ in range(20000)))
    words = ["one paragraph", "single paragraph", "فقرة واحدة", " only", " فقط", " ", "  ", "\t", "\n", ".", "!", "؟", "x"]
    _same(OLD_ONE_PARAGRAPH, NEW_ONE_PARAGRAPH, "fullmatch", (_words(words) for _ in range(20000)))


def test_dsml_brace_and_stub_patterns_answer_as_before():
    _same(OLD_DSML, NEW_DSML, "search", (_random("< |dsml", 12) for _ in range(20000)))
    _same(OLD_BRACES, NEW_BRACES, "search", (_random("{},a", 12) for _ in range(20000)))
    _same(OLD_STUB_SPLIT, NEW_STUB_SPLIT, "split1", (_random(" -—a\t", 12) for _ in range(20000)))
    assert filesystem._expand_braces("*.{js,ts}") == ["*.js", "*.ts"]
    assert shell_paths._expand_braces("a{b,c}d") == ["abd", "acd"]
    found = extract_active_attachments(None, user_text="see [Attached: report.pdf — use file_read]")
    assert [a["filename"] for a in found] == ["report.pdf"]


def test_skill_frontmatter_reads_as_before():
    """Same frontmatter and body, except where the old pattern had to give back
    a blank line after the opening "---" to find any closing one: then its
    frontmatter was blank and the skill unusable either way."""
    for _ in range(30000):
        s = _random(["---", "\n", " ", "\r", "a: b", "x"], 10)
        old, new = OLD_FRONTMATTER.match(s), skill_parser._FRONTMATTER_RE.match(s)
        if old and old.group(1).strip():
            assert new and (new.group(1), new.group(2)) == (old.group(1), old.group(2)), s
        elif new:
            assert not new.group(1).strip(), s
    skill = skill_parser.parse_skill_md("---\nname: demo\ndescription: A demo skill\n---\n\nBody text\n")
    assert skill is not None and skill.name == "demo" and skill.body == "Body text"


def test_code_spans_are_removed_as_commonmark_reads_them():
    for _ in range(30000):
        s = _random("`a !", 16)
        assert image_refs._without_code_spans(s) == COMMONMARK_CODE_SPAN.sub("", s), s
    # Where every backtick run is one long, the old pattern read the same spans.
    for _ in range(20000):
        s = _random("`ab !", 16)
        if "``" not in s:
            assert image_refs._without_code_spans(s) == OLD_CODE_SPAN.sub("", s), s
    # The intended difference: a run is never split (a run of three is not
    # closed by a run of one).
    assert image_refs._without_code_spans("```a`") == "```a`"
    assert OLD_CODE_SPAN.sub("", "```a`") == "``"


def test_inline_images_are_found_as_before():
    alphabet = ["![", "]", "(", ")", "a", " ", "x.png", "](", "!", "["]
    for _ in range(30000):
        s = _words(alphabet, 12)
        assert image_refs._images_in(s) == OLD_ANY_IMAGE.findall(s), s
    assert image_refs.inline_images("see ![a](p.png) and `![b](q.png)`") == ["![a](p.png)"]
    assert image_refs.inline_images("![Caption](path/to/picture.png)") == []  # a figure line


def test_css_rules_read_as_before():
    _same(OLD_CSS_RULE, renderer_worker._CSS_RULE, "findall", (_random("{}a ;", 16) for _ in range(30000)))


def _old_resources_pass(text: str, approved: frozenset[str]) -> bool:
    for match in OLD_RESOURCE.finditer(text):
        raw = next((value for value in match.groupdict().values() if value), "")
        value = raw.strip().strip("\"'")
        if value.lower().startswith("data:") or value.startswith("#"):
            continue
        normalized = value.replace("\\", "/").lstrip("./")
        if normalized and "/" not in normalized and normalized in approved:
            continue
        return False
    return True


def _new_resources_pass(text: str, approved: frozenset[str]) -> bool:
    try:
        resources.validate_restricted_render_resources(text, approved_asset_names=approved)
    except DocumentSecurityError:
        return False
    return True


def test_resource_checks_never_pass_what_they_refused():
    approved = frozenset({"x.png"})
    tokens = ["![", "](", "a", ")", "]", "!", "[", " ", "src=", "url(", "x.png", "data:z", "http://e/p", ">", '"',
              "#f", "@import "]
    for _ in range(50000):
        s = _words(tokens, 10)
        if not _old_resources_pass(s, approved):
            assert not _new_resources_pass(s, approved), s
    # Stricter where the old alternation hid a reference inside another's value.
    bypass = "src=data:x ![a](http://evil.example/p.png)"
    assert _old_resources_pass(bypass, frozenset()) and not _new_resources_pass(bypass, frozenset())
    assert _new_resources_pass('<img src="x.png"> ![a](x.png) url(data:abc)', approved)


def test_filler_patterns_answer_as_before():
    """The filler patterns became a rule over words (2026-11, the memory
    framework audit's F8: ``episode_text.is_small_talk_words``). Every
    message the old whole-message patterns called filler is still small
    talk; the rule only adds messages made of several small-talk words."""
    words = ["okay", "ok", "got it", "thanks", "no", "lol", "haha", "hm", "hmm", "uh", "uhh", "um", " ", "  ", "\n",
             "\t", ".", "!", "?", "x", "h"]
    for _ in range(30000):
        s = _words(words)
        if OLD_FILLER_WORDS.search(s) or OLD_FILLER_SOUNDS.search(s):
            assert episode_text.is_small_talk_words(s), s
    assert belief_extractor.is_filler_turn("okay!")


def test_current_facts_read_as_before():
    words = ["grok", "zcode", "claude", " ", "  ", "\n", "reset", " next", " weekly", ":", "my ", "and ", "And my ",
             ".", "x", "Aug 10", "02:48"]
    for _ in range(30000):
        s = _words(words, 12)
        old = [(m.span(), m.groupdict()) for m in OLD_RESET_LINE.finditer(s)]
        new = [(m.span(), m.groupdict()) for m in current_facts._RESET_LINE.finditer(s)]
        assert old == new, s
    _same(OLD_AND_MY, NEW_AND_MY, "split1", (_words(["a", " ", "  ", "\t", "And", "my", "AND", "\n", "b"], 10)
                                             for _ in range(20000)))
    facts = current_facts.parse_current_facts("My Grok weekly next reset: August 10, 02:48 local and my ZCode reset 13:47")
    assert {f["predicate"] for f in facts} >= {"grok_next_reset"}


def test_exec_denylist_entries_decide_as_before():
    fork = next(p for p in authorize._EXEC_DENYLIST if "\\(\\)" in p.pattern)
    dd = next(p for p in authorize._EXEC_DENYLIST if "bdd" in p.pattern)
    for _ in range(30000):
        s = _random(":(){}|& x\n", 18)
        assert bool(fork.search(s)) == bool(OLD_FORK_BOMB.search(s)), s
        s = _random(["dd", " ", "of", "=", "/dev/", "x", "\n", "\t", "d"], 10)
        assert bool(dd.search(s)) == bool(OLD_DD.search(s)), s
    assert fork.search(":(){ :|:& };:") and dd.search("sudo dd if=x of=/dev/sda bs=1M")


def test_belief_date_patterns_answer_as_before():
    words = ["kuwait", "asia/kuwait", "utc", "gmt", "local", "local time", " ", "  ", "\t", "\n", "(", ")", "+3",
             "-8", ":00", "(utc +3)", "x", "2026-08-17 02:48"]
    for _ in range(30000):
        s = _words(words)
        assert relative_time._RE_TZ_QUALIFIER.sub("", s) == OLD_TZ_QUALIFIER.sub("", s), s
        assert relative_time._RE_TZ_PAREN_ONLY.sub("", s) == OLD_TZ_PAREN_ONLY.sub("", s), s
        assert re.sub(NEW_LOCAL_SUFFIX, "", s, flags=re.I) == OLD_LOCAL_SUFFIX.sub("", s), s
    assert relative_time.parse_belief_date("2026-08-17 02:48 Kuwait (UTC+3)") is not None


def test_shell_program_detection_answers_as_before():
    """Same verdict, and the match ends at the same place; it may start at a
    later "command:" key, which decides the same (its program ends the run)."""
    tokens = ["command", "command:", "Command :", ":", "/", "\\", "sh", "bash", "pwsh", ".exe", "[", '"', "'", " ",
              ",", "]", "\n", "x", "c:", "cmd", "/bin/", "a"]
    for _ in range(50000):
        s = _words(tokens, 12)
        old, new = OLD_SHELL_PROGRAM.search(s), dependency_scanner._SHELL_PROGRAM.search(s)
        assert bool(old) == bool(new) and (old and old.end()) == (new and new.end()), s
    assert dependency_scanner._manifest_issues('command: ["/bin/sh", "-c", "x"]\n') == [
        "the manifest runs its command through a shell"]


def test_bare_assignment_reads_as_before():
    words = ["  ", "\t", "export ", "export", "-", " ", "api_key", "token", "MY_SECRET", "pwd", "=", ":", "abcdefgh12",
             "x", "#", " # note"]
    for _ in range(30000):
        s = _words(words)
        old, new = OLD_BARE_ASSIGNMENT.match(s), secret_scan._BARE_ASSIGNMENT.match(s)
        assert (old and old.groupdict()) == (new and new.groupdict()), s


def test_trailing_blanks_and_target_suffix_read_as_before():
    _same(OLD_TRAILING_BLANKS, NEW_TRAILING_BLANKS, "sub", (_random(" \t\na", 16) for _ in range(20000)))
    _same(OLD_TARGET_SUFFIX, NEW_TARGET_SUFFIX, "search", (_random(" ->ab\n", 14) for _ in range(20000)))
    assert swarm_dispatch._parse_output_target_suffix("summarize x -> telegram:-1001234567890")[0] == "summarize x"


def test_whatsapp_headings_read_as_before_on_one_line():
    """On one line, with something after the hashes, the same heading."""
    keep = lambda m: "\x01" + m.group(1).strip() + "\x01"  # noqa: E731
    new = re.compile(NEW_WA_HEADING)
    for _ in range(30000):
        s = _random("# a#\t\r", 14)
        if not re.search(r"#[^\S\n]*\S", s):
            continue
        before = OLD_WA_HEADING.sub(keep, s)
        if "\x01\x01" in before:  # a whitespace-only heading: see below
            continue
        assert new.sub(keep, s) == before, s


def test_whatsapp_intended_differences():
    # The blank line after a heading is kept (the old end crossed the newline).
    assert whatsapp_format.md_to_whatsapp("# Title\n\nbody") == "*Title*\n\nbody"
    assert OLD_WA_HEADING.sub(lambda m: "\x01" + m.group(1).strip() + "\x01", "# Title\n\nbody") == "\x01Title\x01\nbody"
    # A heading with nothing in it is left as written (it became "**").
    assert whatsapp_format.md_to_whatsapp("intro\n#   ") == "intro\n#   "
    # A CRLF line is still a heading.
    assert whatsapp_format.md_to_whatsapp("# Title\r\nbody") == "*Title*\nbody"
    # Links: the innermost [x](u), as CommonMark.
    assert whatsapp_format.md_to_whatsapp("[x [y](https://a.example)") == "[x y (https://a.example)"
    assert OLD_WA_LINK.sub(r"\1 (\2)", "[x [y](https://a.example)") == "x [y (https://a.example)"


def test_whatsapp_links_read_as_before_without_a_nested_bracket():
    tokens = ["[", "]", "(", ")", "![", "a", " ", "https://e.example/p", "](", "\n"]
    for _ in range(30000):
        s = _words(tokens, 10)
        if re.search(r"\[[^\]]*\[|\]\([^)]*\[", s):
            continue
        assert re.sub(NEW_WA_LINK, r"\1 (\2)", s) == OLD_WA_LINK.sub(r"\1 (\2)", s), s
        assert re.sub(NEW_WA_IMAGE, "I", s) == OLD_WA_IMAGE.sub("I", s), s


# ── found by the class gate in the same files ────────────────────────────

def test_whatsapp_bullets_read_as_before():
    """The indent is the bullet line's own; the blank lines before it are kept by
    the text between the matches rather than by the group: the same reply."""
    _same(OLD_WA_BULLET, NEW_WA_BULLET, "bullet",
          (_words(["*", " ", "\n", "\t", "a", "\r", "- "], 12) for _ in range(30000)))


def test_attachment_paths_read_as_before_outside_a_run_of_tildes():
    """A path is no longer read from just after a "~" (the old pattern started
    there when the run's start was blocked: ":~/notes.md" gave "/notes.md", a
    path the user never wrote). Every other path is found as before."""
    new = re.compile(NEW_ATTACHMENT_PATH, re.I)
    tokens = ["~", "/", "a", ".", "pdf", "md", "C:", ":", " ", "\\", "-", "x.docx", "~/", "notes"]
    for _ in range(30000):
        s = _words(tokens, 9)
        before = [(m.span(), m.group(1)) for m in OLD_ATTACHMENT_PATH.finditer(s) if not s[:m.start()].endswith("~")]
        assert [(m.span(), m.group(1)) for m in new.finditer(s)] == before, s
    assert [m.group(1) for m in OLD_ATTACHMENT_PATH.finditer("see:~/notes.md")] == ["/notes.md"]
    assert not new.search("see:~/notes.md")
    found = extract_active_attachments(None, user_text="open ~/reports/q3.pdf and C:\\docs\\a.docx")
    assert {a["path"] for a in found} >= {"~/reports/q3.pdf", "C:\\docs\\a.docx"}


def test_time_units_read_as_before():
    _same(OLD_N_UNITS, relative_time._RE_N_UNITS_EN, "finditer",
          (_words(["1", "23", " ", "  ", "\t", "days", "min", "minutes", "hrs", "x", "a1"], 8) for _ in range(30000)))
    _same(OLD_AR_UNIT, relative_time._RE_AR_UNIT, "finditer",
          (_words(["5", "٥", "12", " ", "  ", "\t", "يوم", "يومين", "ساعة", "بـ", "ال", "x", "شهر", "ة"], 8)
           for _ in range(30000)))
    exprs = relative_time.parse_time_expressions("remind me 2 days before, قبل يومين")
    assert len(exprs) >= 2


def test_model_fence_tail_reads_as_before():
    _same(OLD_FENCE_TAIL, NEW_FENCE_TAIL, "sub", (_words([" ", "\n", "`", "```", "a", "\t"], 10) for _ in range(20000)))


# ── the first sweep's "not contained" nine, fixed the same day ───────────

def _nested_bracket(s: str) -> bool:
    """A "[" inside a label, or inside an address: where the new link patterns read differently."""
    return bool(re.search(r"\[[^\]]*\[|\]\([^)]*\[", s))


def test_link_patterns_read_as_before_without_a_nested_bracket():
    tokens = ["[", "]", "(", ")", "a", " ", "https://e.example/p", "http://x", "](", "\n", "!"]
    pairs = [(OLD_TG_LINK, telegram_format._LINK_RE), (OLD_TTS_LINK_TELEGRAM, re.compile(NEW_TTS_LINK)),
             (OLD_TTS_LINK, re.compile(NEW_TTS_LINK))]
    for _ in range(30000):
        s = _words(tokens, 10)
        if _nested_bracket(s):
            continue
        for old, new in pairs:
            assert new.sub(r"<\1>", s) == old.sub(r"<\1>", s), (old.pattern, s)


def test_link_intended_differences():
    # The innermost [x](u) is the link (as CommonMark); an address holding "["
    # (an IPv6 literal) is no longer a labelled link.
    html = telegram_format.md_to_tg_html("[x [y](https://a.example/p)")
    assert '<a href="https://a.example/p">y</a>' in html and "[x " in html
    assert OLD_TG_LINK.sub(r"<\1>", "[x [y](https://a.example/p)") == "<x [y>"
    assert "<a " not in telegram_format.md_to_tg_html("[v6](https://[::1]/)")
    assert voice_helpers.prepare_tts_text("read [the docs](https://a.example) now") == "read the docs now"


def test_tag_stripping_reads_as_before_without_a_nested_opener():
    """A tag holds no "<": "<a <b>" now leaves "<a " and removes "<b>" (the old
    pattern removed it all). Everything else is the same."""
    new = re.compile(NEW_TAG)
    for _ in range(30000):
        s = _words(["<", ">", "b", "/b", " ", "a", "\n", "<br>"], 10)
        if re.search(r"<[^>]*<", s):
            continue
        assert new.sub("", s) == OLD_TAG.sub("", s), s
    assert new.sub("", "<a <b>") == "<a " and OLD_TAG.sub("", "<a <b>") == ""


def test_plan_fence_reads_as_before():
    tokens = ["```plan", "```", "`", "plan", " ", "\t", "\n", "a", "PLAN", "x```", "- step"]
    for _ in range(50000):
        s = _words(tokens, 9)
        old, new = OLD_PLAN.search(s), plan_fence._CLOSED_FENCE_RE.search(s)
        assert (old and (old.span(), old.group(1))) == (new and (new.span(), new.group(1))), s
    assert plan_fence.split_plan_and_prose("```plan\n- [ ] read\n- [ ] write\n```\nDone.") == (
        "- [ ] read\n- [ ] write", "Done.")


def test_enumerated_items_and_research_lines_read_as_before():
    tokens = ["1.", "2)", "-", "•", "*", " ", "  ", "\t", "\n", "\r", "a", "/research", " deep", "x"]
    research = re.compile(NEW_RESEARCH_LINE, re.I)
    for _ in range(30000):
        s = _words(tokens, 10)
        new = semantic_compact._ENUMERATED_ITEM_RE
        assert [m.end() for m in new.finditer(s)] == [m.end() for m in OLD_ITEMS.finditer(s)], s
        assert bool(research.search(s)) == bool(OLD_RESEARCH_LINE.search(s)), s
    assert semantic_compact._enumerated_items("Drafts:\n\n1. one\n2. two\n\n- three") == 3
    assert research_policy.is_deep_research_intent("hi\n\n  /research deep the history of Kuwait")


def test_symbols_are_found_as_before_and_named_on_their_own_line():
    """Every definition is found as before (same end, same name and signature);
    its line and indent are its own line's. "^(\\s*)" started at the first blank
    line before it: the line reported was that blank line's, and four blank
    lines made a top-level def a "method"."""
    tokens = ["def ", "class ", "async ", "export ", "const ", "function ", "f", "(", ")", "=", " ", "    ", "\n",
              "\t", "x"]
    for name, old in OLD_SYMBOLS.items():
        new = getattr(symbols, name)
        for _ in range(20000):
            s = _words(tokens, 10)
            assert [(m.end(), m.groups()[1:]) for m in new.finditer(s)] == \
                [(m.end(), m.groups()[1:]) for m in old.finditer(s)], (name, s)
            for m in new.finditer(s):
                assert "\n" not in m.group(1) and m.start() in (0, s.rfind("\n", 0, m.start()) + 1), (name, s)
    source = "x = 1\n\n\n\n\ndef f(a):\n    return a\n"
    (found,) = symbols._regex_symbols("python", source)
    assert (found.name, found.kind, found.line) == ("f", "function", 6)
    old = OLD_SYMBOLS["_PY_DEF"].search(source)
    assert source.count("\n", 0, old.start()) + 1 == 2 and len(old.group(1)) >= 4  # the old line, the old "method"


# ── the second sweep's four, fixed the same day ──────────────────────────

def test_quoted_spans_read_as_before_without_a_nested_opener():
    """A curly or angle quote holds no opener of its own: in "“a “b”" the
    quote is "“b”" (it was the whole). Everything else is the same."""
    tokens = ["“", "”", "«", "»", "‘", "’", '"', "'", "`", "a", " ", "\n", "b"]
    for _ in range(50000):
        s = _words(tokens, 10)
        if re.search(r"“[^”]*“|«[^»]*«|‘[^’]*‘", s):
            continue
        assert [m.span() for m in answer_format._QUOTED.finditer(s)] == \
            [m.span() for m in OLD_QUOTED.finditer(s)], s
    assert [m.group() for m in answer_format._QUOTED.finditer("“a “b”")] == ["“b”"]
    assert [m.group() for m in OLD_QUOTED.finditer("“a “b”")] == ["“a “b”"]
    assert answer_format._requests_one_paragraph("Answer in one paragraph, please.")


def test_knowledge_fences_are_found_exactly_as_before():
    tokens = ["```", "~~~", "`", "~", "\n", " ", "\t", "py", "x", "````"]
    for _ in range(50000):
        s = _words(tokens, 12)
        assert knowledge_chunker._fence_spans(s) == [m.span() for m in OLD_KB_FENCE.finditer(s)], s
    segments = knowledge_chunker._split_into_segments("Intro\n```py\nprint(1)\n```\nAfter")
    assert [(seg.kind, seg.text) for seg in segments] == [
        ("prose", "Intro\n"), ("code", "```py\nprint(1)\n```"), ("prose", "\nAfter")]


def test_knowledge_links_are_found_as_before_unless_an_address_holds_a_bracket():
    """The innermost [x](u) is the link; the address is the same where the old
    one held no "[" (an address with one -- an IPv6 literal, or text run into
    the next link -- is no longer taken)."""
    new = re.compile(NEW_KB_LINK)
    tokens = ["[", "]", "(", ")", "a", " ", "https://e.example/p", "](", "\n", "/x"]
    for _ in range(30000):
        s = _words(tokens, 10)
        old = OLD_KB_LINK.findall(s)
        if any("[" in url for url in old):
            continue
        assert new.findall(s) == old, s
    assert OLD_KB_LINK.findall("[a](x[b](y)") == ["x[b](y"] and new.findall("[a](x[b](y)") == ["y"]
    found = knowledge_ingest._extract_links_from_text("see [docs](https://e.example/docs) and [b [c](/x)",
                                                      "https://e.example/")
    assert any(link.endswith("/docs") for link in found) and any(link.endswith("/x") for link in found)


def test_research_file_references_read_as_before():
    tokens = ["research/", "research", "/", ".md", "md", ".", "-", "a", " ", "x/", "my", "\n", "_"]
    for _ in range(50000):
        s = _words(tokens, 9)
        assert bool(research_eval._FILE_REF.search(s)) == bool(OLD_FILE_REF.search(s)), s
    assert research_eval._FILE_REF.search("see docs/research/kuwait-history.md for the sources")


def test_post_quotes_read_as_before_without_a_nested_opener():
    """A quote holds no "“" of its own: in '“a “bcde”' the quote is "bcde"."""
    for _ in range(50000):
        s = _words(['"', "“", "”", "abcd", "a", " ", "\n", "x"], 10)
        old = [(m.span(), m.group(1)) for m in OLD_POST_QUOTE.finditer(s)]
        if any("“" in quoted for _, quoted in old):
            continue
        assert [(m.span(), m.group(1)) for m in text_display._CLOSED_QUOTE_RE.finditer(s)] == old, s
    assert text_display._CLOSED_QUOTE_RE.search("“a “bcde”").group(1) == "bcde"
    assert OLD_POST_QUOTE.search("“a “bcde”").group(1) == "a “bcde"


def test_url_passwords_are_found_as_before():
    """A scheme is read at most 32 characters back from its "://"; the
    password of each URL, and where each match ends, are the same."""
    tokens = ["https", "://", "a", "1", "+", ".", "-", "user:", "Xk9q2Zp7Lm4r", "@host", " ", '"', "A" * 20]
    for _ in range(50000):
        s = _words(tokens, 9)
        old = [(m.end(), secret_scan._url_password(m.group(0))) for m in OLD_URL.finditer(s)]
        new = [(m.end(), secret_scan._url_password(m.group(0))) for m in secret_scan._URL.finditer(s)]
        assert new == old, s
    assert secret_scan.find_credentials("DB=postgres://kazma:Xk9q2Zp7Lm4rTw8@db:5432/kazma")


# ── linear on hostile input, through the product ─────────────────────────

def test_hostile_inputs_finish_at_once():
    n = 100_000
    checks = [
        ("image body line", lambda: image_refs.inline_images("x" + "`" * n)),
        ("image runs growing", lambda: image_refs.inline_images("".join("`" * k + "a" for k in range(1, 450)))),
        ("image starts", lambda: image_refs.inline_images("![" * (n // 2))),
        ("image openers", lambda: image_refs.inline_images("![a](" * (n // 5))),
        ("one paragraph", lambda: answer_format._requests_one_paragraph("one paragraph" + " " * n + "x")),
        ("fence line", lambda: answer_format._requests_one_paragraph("`" * n + "a")),
        ("dsml", lambda: graph_helpers.is_unusable_assistant_content("x<" + " " * n + "x")),
        ("braces", lambda: filesystem._expand_braces("{" + "," * n)),
        ("shell braces", lambda: shell_paths._expand_braces("{" + "," * n)),
        ("attachment stub", lambda: extract_active_attachments(None, user_text="[Attached: a" + " " * n + "b]")),
        ("frontmatter", lambda: skill_parser.parse_skill_md("---" + "\n" * n)),
        ("css", lambda: renderer_worker._CSS_RULE.findall("a" * n)),
        ("resources", lambda: _new_resources_pass("![" * (5 * n), frozenset())),
        ("filler words", lambda: episode_text.is_small_talk_words("okay" + " " * n + "x")),
        ("filler sounds", lambda: episode_text.is_small_talk_words("u" + "h" * n + "x")),
        ("reset line", lambda: current_facts.parse_current_facts("grok reset 1" + " " * n + "x")),
        ("reset value", lambda: current_facts.parse_current_facts("grok reset x" + " " * n + "y")),
        ("fork bomb", lambda: [p.search(":(){ " + ":|&" * (n // 3)) for p in authorize._EXEC_DENYLIST]),
        ("dd", lambda: [p.search("dd " * (n // 3)) for p in authorize._EXEC_DENYLIST]),
        ("belief date", lambda: relative_time.parse_belief_date("2026" + " " * n + "x")),
        ("manifest keys", lambda: dependency_scanner._manifest_issues("command:" * (n // 8))),
        ("manifest blank", lambda: dependency_scanner._manifest_issues("command:" + " " * n + "x")),
        ("config lines", lambda: secret_scan.find_credentials((" " * 4089 + "token=x\n") * 25, bare_values=True)),
        ("page text", lambda: html_extract.strip_html_fallback(" " * n + "x")),
        ("whatsapp heading", lambda: whatsapp_format.md_to_whatsapp("# a" + " " * n + "b")),
        ("whatsapp blank heading", lambda: whatsapp_format.md_to_whatsapp("# " + " " * n)),
        ("whatsapp links", lambda: whatsapp_format.md_to_whatsapp("[" * n)),
        ("whatsapp images", lambda: whatsapp_format.md_to_whatsapp("![a](" * (n // 5))),
        ("swarm target", lambda: swarm_dispatch._parse_output_target_suffix("a" + " " * n + "x y")),
        ("whatsapp bullets", lambda: whatsapp_format.md_to_whatsapp("\n" * n + "x")),
        ("attachment tildes", lambda: extract_active_attachments(None, user_text="~" * n)),
        ("time units", lambda: relative_time._RE_N_UNITS_EN.findall("0" * n)),
        ("arabic units", lambda: relative_time._RE_AR_UNIT.findall("\t" * n)),
        ("arabic digits", lambda: relative_time._RE_AR_UNIT.findall("0" * n)),
        ("model fence tail", lambda: re.sub(NEW_FENCE_TAIL, "", "```" + " " * n + "x")),
        ("telegram links", lambda: telegram_format.md_to_tg_html("[" * n)),
        ("telegram link openers", lambda: telegram_format.md_to_tg_html("[a](https://x" * (n // 14))),
        ("speech links", lambda: voice_helpers.prepare_tts_text("[" * n)),
        ("speech tags", lambda: voice_helpers.prepare_tts_text("<" * n)),
        ("speech link openers", lambda: voice_helpers.prepare_tts_text("[a](" * (n // 4))),
        ("telegram speech", lambda: [re.sub(p, "", "[" * n + "<" * n) for p in (NEW_TTS_LINK, NEW_TAG)]),
        ("plan fence line", lambda: plan_fence.split_plan_and_prose("```plan" + "\t" * n)),
        ("plan fence fences", lambda: plan_fence.split_plan_and_prose("```plan " + "```" * (n // 3))),
        ("enumerated items", lambda: semantic_compact._enumerated_items("\n" * n + "x")),
        ("research line", lambda: research_policy.is_deep_research_intent("a" + "\n" * n + "b")),
        ("python symbols", lambda: symbols._regex_symbols("python", "\n" * n + "x")),
        ("javascript symbols", lambda: symbols._regex_symbols("javascript", "\n" * n + "x")),
        ("open quotes", lambda: answer_format._requests_one_paragraph("«" * n)),
        ("curly quotes", lambda: [m.span() for m in answer_format._QUOTED.finditer("“" * n + "‘" * n)]),
        ("knowledge backticks", lambda: knowledge_chunker._split_into_segments("`" * n)),
        ("knowledge openers", lambda: knowledge_chunker._split_into_segments("```a" * (n // 4) + "\nx")),
        ("knowledge open fences", lambda: knowledge_chunker._split_into_segments("```x\n" * (n // 5))),
        ("knowledge links", lambda: knowledge_ingest._extract_links_from_text("[" * n, "https://e.example/")),
        ("knowledge link openers", lambda: re.findall(NEW_KB_LINK, "[a](x" * (n // 5))),
        ("research file refs", lambda: research_eval._FILE_REF.search("research/" * (n // 9))),
        ("post quotes", lambda: text_display.extract_post_body("“" * n)),
        ("url schemes", lambda: secret_scan.find_credentials(("A" * 4000 + " x://@\n") * 25)),
    ]
    for name, check in checks:
        assert _timed(check) < 1.0, name


def test_control_the_old_patterns_grow_faster_than_the_input():
    cases = [
        (lambda s: OLD_CODE_SPAN.sub("", s), lambda k: "x" + "`" * k, 100),
        (lambda s: OLD_ANY_IMAGE.findall(s), lambda k: "![" * k, 1500),
        (lambda s: OLD_FENCED.sub("", s), lambda k: "`" * k + "a", 1500),
        (lambda s: OLD_ONE_PARAGRAPH.fullmatch(s), lambda k: "one paragraph" + " " * k + "x", 1500),
        (OLD_DSML.search, lambda k: "x<" + " " * k + "x", 1500),
        (OLD_BRACES.search, lambda k: "{" + "," * k, 1500),
        (lambda s: OLD_STUB_SPLIT.split(s, maxsplit=1), lambda k: "a" + " " * k + "b", 1500),
        (OLD_FRONTMATTER.match, lambda k: "---" + "\n" * k, 1500),
        (OLD_CSS_RULE.findall, lambda k: "a" * k, 1500),
        (lambda s: list(OLD_RESOURCE.finditer(s)), lambda k: "![" * k, 1500),
        (OLD_FILLER_WORDS.search, lambda k: "okay" + " " * k + "x", 1500),
        (OLD_FILLER_SOUNDS.search, lambda k: "u" + "h" * k + "x", 1000),
        (lambda s: list(OLD_RESET_LINE.finditer(s)), lambda k: "grok reset x" + " " * k + "y", 1500),
        (lambda s: OLD_AND_MY.split(s, maxsplit=1), lambda k: "a" + " " * k + "b", 1500),
        (OLD_FORK_BOMB.search, lambda k: ":(){ " + ":|&" * k, 50),
        (OLD_DD.search, lambda k: "dd " * k, 1000),
        (lambda s: OLD_TZ_QUALIFIER.sub("", s), lambda k: " " * k + "x", 1000),
        (lambda s: OLD_TZ_PAREN_ONLY.sub("", s), lambda k: " " * k + "x", 1500),
        (lambda s: OLD_LOCAL_SUFFIX.sub("", s), lambda k: " " * k + "x", 1000),
        (OLD_SHELL_PROGRAM.search, lambda k: "command:" + " " * k + "x", 1000),
        (OLD_BARE_ASSIGNMENT.match, lambda k: " " * k + "token=x", 1000),
        (lambda s: OLD_TRAILING_BLANKS.sub("", s), lambda k: " " * k + "x", 1500),
        (lambda s: OLD_WA_HEADING.sub("", s), lambda k: "# a" + " " * k + "b", 150),
        (lambda s: OLD_WA_LINK.sub("", s), lambda k: "[" * k, 1500),
        (lambda s: OLD_WA_IMAGE.sub("", s), lambda k: "![" * k, 1500),
        (OLD_TARGET_SUFFIX.search, lambda k: "a" + " " * k + "x y", 1500),
        (lambda s: OLD_WA_BULLET.sub("", s), lambda k: "\n" * k + "x", 1500),
        (OLD_ATTACHMENT_PATH.findall, lambda k: "~" * k, 1500),
        (OLD_N_UNITS.findall, lambda k: "0" * k, 1500),
        (OLD_AR_UNIT.findall, lambda k: "\t" * k, 1000),
        (lambda s: OLD_FENCE_TAIL.sub("", s), lambda k: "```" + " " * k + "x", 1500),
        (lambda s: OLD_TG_LINK.sub("", s), lambda k: "[" * k, 1500),
        (lambda s: OLD_TTS_LINK_TELEGRAM.sub("", s), lambda k: "[" * k, 1500),
        (lambda s: OLD_TTS_LINK.sub("", s), lambda k: "[" * k, 1500),
        (lambda s: OLD_TAG.sub("", s), lambda k: "<" * k, 2000),
        (OLD_PLAN.search, lambda k: "```plan" + "\t" * k, 1500),
        (OLD_ITEMS.findall, lambda k: "\n" * k + "x", 1500),
        (OLD_RESEARCH_LINE.search, lambda k: "a" + "\n" * k + "b", 1500),
        *[(old.findall, lambda k: "\n" * k + "x", 1500) for old in OLD_SYMBOLS.values()],
        (OLD_QUOTED.findall, lambda k: "«" * k, 2000),
        (OLD_KB_FENCE.findall, lambda k: "`" * k, 1500),
        (OLD_KB_FENCE.findall, lambda k: "```x\n" * k, 500),
        (OLD_KB_LINK.findall, lambda k: "[" * k, 2000),
        (OLD_FILE_REF.findall, lambda k: "research/" * k, 300),
        (OLD_POST_QUOTE.findall, lambda k: "“" * k, 1500),
        (OLD_URL.findall, lambda k: "A" * k, 1500),
    ]
    for fn, make, small in cases:
        assert _grows_faster_than_linear(fn, make, small), make(3)


# ── the gateway's fenced-block strip (found by the sweep on Linux CI) ─────

OLD_GATEWAY_FENCE = re.compile(r"<kazma:data\b[^>]*>.*?</kazma:data>", re.DOTALL | re.IGNORECASE)


def test_the_gateway_fence_strip_answers_as_the_pattern_did():
    from kazma_gateway.agent_handler.graph import _without_fenced_blocks

    words = ["<kazma:data", "<KAZMA:DATA", "</kazma:data>", "</KAZMA:Data>", ">", "<", " ",
             "x", "a=1", "\n", "<kazma:datax", "data"]
    for _ in range(30000):
        s = _words(words, 10)
        assert _without_fenced_blocks(s) == OLD_GATEWAY_FENCE.sub("", s), s
    sample = 'see <kazma:data source="file">secret</kazma:data> after'
    assert _without_fenced_blocks(sample) == "see  after"


def test_the_gateway_fence_strip_is_linear():
    from kazma_gateway.agent_handler.graph import _without_fenced_blocks

    hostile = "<kazma:data" * 20000
    assert _timed(_without_fenced_blocks, hostile) < 0.5
    assert _timed(_without_fenced_blocks, "<kazma:data>" + "x" * 200000) < 0.5
    small, big = "<kazma:data" * 2000, "<kazma:data" * 8000
    old_small = _timed(OLD_GATEWAY_FENCE.sub, "", small)
    old_big = _timed(OLD_GATEWAY_FENCE.sub, "", big)
    assert old_big > 6 * old_small, "control: the old pattern grows with the square"


# -- docx_fonts: the w:settings opening tag (the regex sweep, CI 2026-11-16) --

OLD_SETTINGS_OPENING = re.compile(r"<w:settings\b[^>]*>")


def test_the_settings_opening_answers_as_the_old_pattern():
    from kazma_core.documents.engines.docx_fonts import _settings_opening_end

    words = ["<w:settings", "<w:settingsX", "<w:settings_", ">", " ", "x", "<", "w:", "\n", "a=1", "/"]
    for _ in range(30000):
        s = _words(words, 10)
        m = OLD_SETTINGS_OPENING.search(s)
        assert _settings_opening_end(s) == (m.end() if m else None), s


def test_the_settings_opening_is_linear():
    from kazma_core.documents.engines.docx_fonts import _settings_opening_end

    hostile = "<w:settings" * 20000
    assert _timed(_settings_opening_end, hostile) < 0.5
    small, big = "<w:settings" * 2000, "<w:settings" * 8000
    old_small = _timed(OLD_SETTINGS_OPENING.search, small)
    old_big = _timed(OLD_SETTINGS_OPENING.search, big)
    assert old_big > 6 * old_small, "control: the old pattern grows with the square"


# -- event_text: "No" and a value, a whole message (step 4's review, M2) --

OLD_NO_THEN = re.compile(r"^\W*(?:no|nope|nah|لا|لاء|كلا)(?:[ \t]*[,،.!]+|[ \t]+(?=يا ))\W*(?:يا [^\W\d_]+\W+)?"
                         r"(?P<value>[^.,،!?؟\n]{1,60}?)"
                         r"(?:\W+(?:i (?:told|said) (?:you|it|that)|i said|قلت ?لك|قلتلك|قلت لكم|قلت))?\W*$")


def test_no_then_answers_as_the_old_pattern_on_one_mark():
    from kazma_core.memory.event_text import _NO_THEN

    for text in ("no, omar", "no! omar, i told you", "لا، عمر", "لا يا كازما، عمر", "nope, omar", "no.  omar",
                 "no,, omar", "no omar", "لا عمر", "no", "no, omar and sara"):
        new, old = _NO_THEN.match(text), OLD_NO_THEN.match(text)
        assert (new and new["value"]) == (old and old["value"]), text
    # The one that differs: the old pattern's value was a space, a value of no words.
    from kazma_core.memory.event_text import no_then

    assert _NO_THEN.match("no, ") is None and not no_then("no, ", "Omar")


def test_no_then_is_linear():
    from kazma_core.memory.event_text import _NO_THEN

    for unit in (",", ".", "!", "،"):
        assert _timed(_NO_THEN.match, "no" + unit * 20000) < 0.1, unit
    small, big = "no" + "," * 3000, "no" + "," * 12000
    old_small = _timed(OLD_NO_THEN.match, small)
    old_big = _timed(OLD_NO_THEN.match, big)
    assert old_big > 6 * old_small, "control: the old pattern grows with the square"


# -- what the sweep found once it read a head behind "^\W*(...|...)" (step 4's review) --

OLD_AUTO_ATTACH = re.compile(r"(?:kazma-data/documents/|reports/|data/)[^\s\"'\(\)\[\]`]+\.(?:pdf|docx|html)",
                             re.IGNORECASE)
OLD_SIGN_OFF_TAIL = re.compile(r"(?:best|thanks)(?:[ \t]\w+)?[ \t]*[,،.!]?[ \t]*")
NEW_SIGN_OFF_TAIL = re.compile(r"(?:best|thanks)(?:[ \t]\w+)?[ \t]*(?:[,،.!][ \t]*)?")
OLD_RUN_CODE = re.compile(r"\b(?:run|execute|exec)\s+(?:this|the|my)?\s*(?:code|script|program|snippet)", re.IGNORECASE)


def test_the_auto_attach_paths_are_the_old_patterns():
    from kazma_gateway.agent_handler.attachments import _auto_attach_matches

    words = ["kazma-data/documents/", "data/", "reports/", "Data/", "a", "b.pdf", ".pdf", ".PDF", ".docx", ".html",
             ".htm", " ", "\n", "(", ")", "'", '"', "`", "[", "]", "x/", ".", "/"]
    for _ in range(40000):
        s = _words(words, 12)
        assert _auto_attach_matches(s) == OLD_AUTO_ATTACH.findall(s), s


def test_the_auto_attach_paths_are_read_in_linear_time():
    from kazma_gateway.agent_handler.attachments import _auto_attach_matches

    for unit in ("data/", "kazma-data/documents/", "data/.pdf", "reports/x"):
        assert _timed(_auto_attach_matches, unit * 20000) < 0.5, unit
    small, big = "data/" * 1500, "data/" * 6000
    assert _timed(OLD_AUTO_ATTACH.findall, big) > 6 * _timed(OLD_AUTO_ATTACH.findall, small), \
        "control: the old pattern grows with the square"


def test_the_sign_off_and_run_code_answer_as_before_and_are_linear():
    from kazma_core.memory.pasted_text import _SIGN_OFF
    from kazma_gateway import suggestions

    run_code = next(rx for rx, _text, tool in suggestions._PATTERNS if tool == "python_exec")
    for _ in range(30000):
        s = _words(["best", "thanks", " ", "\t", ",", ".", "!", "x", "regards", "،"], 8)
        assert bool(NEW_SIGN_OFF_TAIL.fullmatch(s)) == bool(OLD_SIGN_OFF_TAIL.fullmatch(s)), s
        t = _words(["run", "exec", " ", "\t", "this", "the", "code", "script", "x", "thiscode"], 8)
        assert bool(run_code.search(t)) == bool(OLD_RUN_CODE.search(t)), t
    assert _timed(_SIGN_OFF.fullmatch, "best" + "\t" * 40000) < 0.1
    assert _timed(run_code.search, "run" + "\t" * 40000) < 0.1
    small, big = "run" + "\t" * 1500, "run" + "\t" * 6000
    assert _timed(OLD_RUN_CODE.search, big) > 6 * _timed(OLD_RUN_CODE.search, small), "control"


#: ``_NO_THEN`` at e4772d2ac: one mark, then ``\W*`` that hands marks back to the value.
STEP4_NO_THEN = re.compile(r"^\W*(?:(?:no|لا|لاء|كلا)(?:[ \t]*[,،.!-]|[ \t]+(?=يا ))|(?:nope|nah)(?:[ \t]*[,،.!-]|[ \t]))"
                           r"\W*(?:يا [^\W\d_]+\W+)?"
                           r"(?P<value>[^.,،!?؟\n]{1,60}?)"
                           r"(?:\W+(?:i (?:told|said) (?:you|it|that)|i said|قلت ?لك|قلتلك|قلت لكم|قلت))?\W*$")


def test_no_then_reads_a_run_of_marks_after_the_mark_once():
    """Step 4's delta review, N2: "No," + "-" * 6000 + "x.y" took 28 s and
    "Nope " the same 56 s, through ``no_then``. Each mark, each head, and a
    tail that fails late."""
    from kazma_core.memory.event_text import _NO_THEN, no_then

    no_then("No, Omar", "Omar")  # warm the folding
    for head in ("No,", "Nope ", "Nah ", "لا،", "No ~"):
        for unit in ("-", "~", "*", "#", ":", ";", '"', "/", "(", "@", "- ", "—"):
            assert _timed(no_then, head + unit * 16000 + "x.y", "Omar") < 0.2, (head, unit)
    for text in ("no, omar", "no - omar", "nope omar!", "لا، عمر", "no,- omar", "no, -omar", "no.  omar",
                 "لا يا كازما، عمر", "no, omar, i told you"):
        new, old = _NO_THEN.match(text), STEP4_NO_THEN.match(text)
        assert (new and new["value"]) == (old and old["value"]), text
    small, big = "no," + "-" * 500 + "x.y", "no," + "-" * 2000 + "x.y"
    assert _timed(STEP4_NO_THEN.match, big) > 6 * _timed(STEP4_NO_THEN.match, small), \
        "control: step 4's pattern grows with the square"
