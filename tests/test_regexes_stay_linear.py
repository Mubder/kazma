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
  e-mail scrubber (``problem_report.redacted``) were quadratic.

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
from kazma_core.support import problem_report
from kazma_core.url_utils import normalize_provider_url

OLD_VERSION = re.compile(r"^v?\d+([._]\d+)+([._-]?[a-z0-9]+)*$", re.I)
OLD_VERSIONED = re.compile(r"/v\d+[a-z0-9]*(?:/|$)")
OLD_FENCE = re.compile(r"</?kazma:data[^>]*>", re.I)
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


def _grows_faster_than_linear(fn, make, small: int) -> bool:
    """True when doubling the input more than triples the time."""
    fn(make(small))  # warm
    t1 = min(_timed(fn, make(small)) for _ in range(2))
    t2 = min(_timed(fn, make(2 * small)) for _ in range(2))
    return t2 > 3 * t1


def test_control_the_old_patterns_grow_faster_than_the_input():
    assert _grows_faster_than_linear(OLD_VERSION.match, lambda n: "0.0" + "a" * n + "!", 9)
    assert _grows_faster_than_linear(lambda s: OLD_FENCE.sub("x", s), lambda n: "<kazma:data" * n, 1500)
    assert _grows_faster_than_linear(lambda s: OLD_VERSIONED.search(s), lambda n: "/v0" + "0" * n + "!", 3000)
    assert _grows_faster_than_linear(lambda s: OLD_EMAIL.sub("x", s), lambda n: "%" * n, 3000)
