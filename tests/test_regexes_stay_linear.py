r"""Five patterns that read untrusted text stay linear, and still say the same.

CodeQL (py/redos, py/polynomial-redos, 2026-11-14) named them; each was
measured before it was changed:

* the memory graph's version test (``ego_anchor._VERSION_RE``) was
  exponential: ``([._-]?[a-z0-9]+)*`` could split a run of letters every way,
  and a belief subject of "0.0", thirty letters and "!" took minutes;
* the forged-fence filter (``prompt_fence._FENCE_TAG_RE``), applied to every
  fetched page, read to the end of the text from each "<kazma:data": a page of
  repeated openers cost seconds a megabyte, squared;
* the provider address's version test and its "/v1/v1" trim
  (``url_utils.normalize_provider_url``), the attachment-name scan of a chat
  message (``turn_input.extract_active_attachments``) and the problem report's
  e-mail scrubber (``problem_report.redacted``) were quadratic;
* the fence sentinel (``prompt_fence._SENTINEL_RE``, 2026-11-15) was
  quadratic on a run of dashes, and the fence's markers read on a folded copy
  (``prompt_fence._FOLDED_MARKER_RE``) are held to the same time;
* the whole-fence reader (``prompt_fence.outside_fences``, 2026-11, the review
  of the memory framework audit's F3) read to the end of the text from every
  opening with no closing after it: a message of repeated openings cost the
  square of its length. It is a scan now (``prompt_fence._fenced_blocks``).

Each new pattern is held to the old one's answers on random inputs, and to a
time on hostile input that the old one could not meet; the old patterns'
growth is the negative control.
"""

from __future__ import annotations

import random
import re
import time

from kazma_core.agent.turn_input import extract_active_attachments
from kazma_core.memory import ego_anchor
from kazma_core.safety import prompt_fence
from kazma_core.security import token_shapes
from kazma_core.support import problem_report
from kazma_core.url_utils import normalize_provider_url

OLD_VERSION = re.compile(r"^v?\d+([._]\d+)+([._-]?[a-z0-9]+)*$", re.I)
OLD_VERSIONED = re.compile(r"/v\d+[a-z0-9]*(?:/|$)")
OLD_FENCE = re.compile(r"</?kazma:data[^>]*>", re.I)
OLD_SENTINEL = re.compile(r"-{2,}\s*(BEGIN|END)\s+OBSERVATION\s*-{2,}", re.I)
OLD_FENCED_BLOCK = re.compile(
    r"<kazma:data source=\"[^\"]*\" untrusted=\"true\">\n"
    r".*?--- BEGIN OBSERVATION ---\n(.*?)\n--- END OBSERVATION ---\n</kazma:data>",
    re.DOTALL,
)
OLD_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NEW_VERSIONED = re.compile(r"/v\d++[a-z0-9]*+(?:/|$)")

RNG = random.Random(20261114)


def _timed(fn, *args) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


def _random(alphabet: str, longest: int) -> str:
    return "".join(RNG.choice(alphabet) for _ in range(RNG.randint(0, longest)))


# ── the same answers ──────────────────────────────────────────────────────

def test_the_version_test_answers_as_before():
    for _ in range(20000):
        s = _random("v0123._-abZ!", 12)
        assert bool(ego_anchor._VERSION_RE.match(s)) == bool(OLD_VERSION.match(s)), s
    assert ego_anchor._VERSION_RE.match("v1.2.3-beta") and ego_anchor._VERSION_RE.match("2.0_rc1")
    assert not ego_anchor._VERSION_RE.match("1.2.") and not ego_anchor._VERSION_RE.match("12")


def test_the_versioned_path_test_answers_as_before():
    for _ in range(20000):
        s = _random("/v01ab-x", 14)
        assert bool(NEW_VERSIONED.search(s)) == bool(OLD_VERSIONED.search(s)), s
    assert normalize_provider_url("https://api.z.ai/api/paas/v4") == "https://api.z.ai/api/paas/v4"
    assert normalize_provider_url("https://h.example/v1/v1/v1") == "https://h.example/v1"
    assert normalize_provider_url("https://h.example/openai") == "https://h.example/openai/v1"


def test_the_fence_filter_leaves_no_fence_tag_and_changes_nothing_else():
    for _ in range(20000):
        s = _random("<>/ kazm:dt", 24)
        new = prompt_fence._FENCE_TAG_RE.sub("[redacted-tag]", s)
        assert not re.search(r"</?kazma:data", new, re.I), (s, new)
        if not re.search(r"</?kazma:data", s, re.I):
            assert new == s
        if OLD_FENCE.search(s) is None and new != s:
            # Stricter than before only where an opener had no ">" before
            # the next "<" or the end: the old filter left its name standing.
            assert re.search(r"</?kazma:data[^<>]*+(?:<|$)", s, re.I), s
    assert prompt_fence._FENCE_TAG_RE.sub("X", 'a</kazma:data>b<kazma:data x="1">c') == "aXbXc"


def test_the_sentinel_filter_redacts_what_it_did():
    """The fence sentinel (2026-11-15): a dash run read from its first dash,
    whole; ``-{2,}`` tried from every dash of a run and backed off to each
    length (20,000 dashes took 5.5 s)."""
    for _ in range(20000):
        s = _random("- ENDBGIOBSRV", 30)
        assert prompt_fence._SENTINEL_RE.sub("x", s) == OLD_SENTINEL.sub("x", s), s


_FENCE_PIECES = (
    '<kazma:data source="doc" untrusted="true">\n', "preamble ", "--- BEGIN OBSERVATION ---\n",
    "body", "\n--- END OBSERVATION ---\n</kazma:data>", "words ", "\n", "<kazma:data", "\"",
)


def test_the_fence_reader_finds_the_same_blocks():
    for _ in range(20000):
        s = "".join(RNG.choice(_FENCE_PIECES) for _ in range(RNG.randint(0, 9)))
        assert prompt_fence.observation_text(s) == OLD_FENCED_BLOCK.sub(lambda m: m.group(1), s), s
        old = OLD_FENCED_BLOCK.sub(" ", s)
        match = prompt_fence._FENCE_OPEN_RE.search(old)
        assert prompt_fence.outside_fences(s) == (old[: match.start()] if match else old), s
    fenced = prompt_fence.format_untrusted_block("inside", source="doc")
    assert prompt_fence.outside_fences(f"mine {fenced} also mine") == "mine   also mine"
    assert prompt_fence.observation_text(f"a {fenced} b") == "a inside b"


def test_the_email_scrubber_finds_the_same_addresses():
    for _ in range(5000):
        local = _random("ab.%+-1", 20) or "a"
        text = f"{_random('xy ', 6)}{local}@mail{_random('ab.', 4)}.com{_random(' z', 4)}"
        assert [m.group() for m in problem_report._EMAIL.finditer(text)] == \
            [m.group() for m in OLD_EMAIL.finditer(text)], text
    assert problem_report.redacted("write to owner@example.com now") == "write to <email> now"


def test_the_attachment_scan_still_reads_a_stub():
    found = extract_active_attachments(None, user_text="see [Attached: report.pdf — use file_read]")
    assert [a["filename"] for a in found] == ["report.pdf"]


# ── linear on hostile input ───────────────────────────────────────────────

def test_hostile_inputs_finish_at_once():
    assert _timed(ego_anchor._VERSION_RE.match, "0.0" + "a" * 50000 + "!") < 0.5
    assert _timed(prompt_fence._FENCE_TAG_RE.sub, "x", "<kazma:data" * 100000) < 1.0
    assert _timed(normalize_provider_url, "https://h.example" + "/v1" * 100000 + "x") < 1.0
    assert _timed(normalize_provider_url, "https://h.example/v0" + "0" * 200000 + "!") < 1.0
    assert _timed(lambda: extract_active_attachments(None, user_text="[attached\t" + "\t\t" * 100000)) < 1.0
    assert _timed(problem_report._EMAIL.sub, "x", "%" * 200000) < 1.0
    assert _timed(prompt_fence._SENTINEL_RE.sub, "x", "-" * 200000) < 1.0
    assert _timed(prompt_fence._sanitize_fence_body, "-" * 200000) < 1.0
    opening = '<kazma:data source="x" untrusted="true">\n'
    for hostile in (opening * 20000, (opening + "--- BEGIN OBSERVATION ---\n") * 10000):
        assert _timed(prompt_fence.outside_fences, hostile) < 1.0
        assert _timed(prompt_fence.observation_text, hostile) < 1.0
    for hostile in ("< " + " " * 200000, "<|" + "a" * 200000, "\uff1c\uff0fkazma" * 50000,
                    ("\u2010" * 100 + " END") * 2000, "\u200b" * 200000):
        assert _timed(prompt_fence._sanitize_fence_body, hostile) < 1.0, hostile[:20]
    # The token table every masker reads (security.token_shapes, 2026-11-15):
    # each shape starts at a literal or at the start of a run.
    for hostile in ("1" * 200000, "eyJ" * 70000, "a" * 200000, "sk-" * 70000, "Bearer " * 30000,
                    "Bearer " + " " * 200000 + "x", "1//0" * 50000, "M" + "a" * 200000 + ".",
                    "bot1234567:" * 20000, "xapp-" * 40000, "kazma_" * 30000, "A" * 200000):
        assert _timed(token_shapes.mask_tokens, hostile) < 1.0, hostile[:20]


def _grows_faster_than_linear(fn, make, small: int, factor: int = 2) -> bool:
    """True when multiplying the input by ``factor`` multiplies the time by more than 1.5 x ``factor``.

    The quadratic controls use a factor of 4: their time then grows 16-fold
    against a bar of 6, so a busy CI runner's noise cannot hide it (a factor
    of 2 gave 4-fold against 3, and a loaded Linux runner measured under it,
    2026-11-15). The exponential one keeps 2: four times its input would take
    minutes.

    The answer is the majority of three measurements (two that agree end it):
    a linear function at a size that runs in microseconds read as growing once
    on a busy CI runner (2026-11-21), and one noisy measurement must decide
    neither way. A function that does grow faster reads so every time.
    """
    fn(make(small))  # warm

    def once() -> bool:
        t1 = min(_timed(fn, make(small)) for _ in range(3))
        t2 = min(_timed(fn, make(factor * small)) for _ in range(3))
        return t2 > 1.5 * factor * t1

    first, second = once(), once()
    return first if first == second else once()


def test_control_a_token_shape_without_its_run_start_grows_faster_than_the_input():
    """Why the JWT shape (and the others not led by a literal) starts at a
    run's start: without the lookbehind, every "eyJ" inside a run of them
    scans to the run's end and fails."""
    unanchored = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}+\.eyJ")
    assert _grows_faster_than_linear(lambda s: unanchored.sub("x", s), lambda n: "eyJ" * n, 1500, 4)
    assert not _grows_faster_than_linear(token_shapes.mask_tokens, lambda n: "eyJ" * n, 1500, 4)


def test_control_the_old_patterns_grow_faster_than_the_input():
    assert _grows_faster_than_linear(OLD_VERSION.match, lambda n: "0.0" + "a" * n + "!", 9)
    assert _grows_faster_than_linear(lambda s: OLD_FENCE.sub("x", s), lambda n: "<kazma:data" * n, 1500, 4)
    assert _grows_faster_than_linear(lambda s: OLD_VERSIONED.search(s), lambda n: "/v0" + "0" * n + "!", 3000, 4)
    assert _grows_faster_than_linear(lambda s: OLD_EMAIL.sub("x", s), lambda n: "%" * n, 3000, 4)
    assert _grows_faster_than_linear(lambda s: OLD_SENTINEL.sub("x", s), lambda n: "-" * n, 1500, 4)
    opening = '<kazma:data source="x" untrusted="true">\n'
    assert _grows_faster_than_linear(lambda s: OLD_FENCED_BLOCK.sub(" ", s), lambda n: opening * n, 400, 4)
    assert not _grows_faster_than_linear(prompt_fence.outside_fences, lambda n: opening * n, 400, 4)
