"""The recall block gives room by rank (the memory plan's W1a).

Until W1a every recalled conversation showed the first 200 characters of its
question and the first 300 of its answer, wherever the question's answer was
in it. On LongMemEval's dev questions most missed answers were shown but cut
(docs/plans/RECALL_BLOCK_BOUNDS_PROPOSAL.md). Now the best conversation shows
up to ``limits.RECALL_LEAD_SHOW`` characters and every other
``limits.RECALL_OTHER_SHOW``, each side cut around the stretch holding the
question's words; when the block's budget would leave a line out, the others
give way first, then the best, and no line is left out.

Each check runs the real code. Negative controls: the old fixed bounds (a
hit with no sides), a block without the ladder (the least room equal to the
first), and a cut that reads words without search's rule.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory import limits
from kazma_core.memory import recall as recall_module
from kazma_core.memory.episode_text import display_at, focused
from kazma_core.memory.query_terms import content_terms, word_term
from kazma_core.memory.recall import RecallHit, RecallResult, _shown, format_recall_block
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

FILLER_EN = "We talked about the garden, the weather and the week ahead. "
FILLER_AR = "تكلمنا عن الحديقة والطقس والأسبوع القادم. "


def _terms(question: str) -> frozenset[str]:
    return frozenset(content_terms(question))


def _hit(eid: str, question: str, answer: str, *, sides: bool = True) -> RecallHit:
    """A recalled conversation as recall leaves it: the line recall made
    (``_shown``), and, for room by rank, its sides."""
    meta = _shown(question, answer, "")
    if sides:
        meta["sides"] = (question, answer, "")
        meta["beyond"] = (0, 0, 0)
    return RecallHit(id=eid, content=question[:400], score=1.0, metadata=meta)


def _history_lines(block: str) -> list[str]:
    body = block.split("## Relevant History", 1)[1] if "## Relevant History" in block else ""
    return [line for line in body.splitlines() if "User:" in line]


# ── The cut: around the question's words, read as search reads them ──────


def test_word_term_reads_words_as_search_does():
    terms = _terms("When is the vaccine appointment? متى موعد القهوة")
    assert word_term("vaccines", terms) == "vaccine"
    assert word_term("appointment", terms) == "appointment"
    assert word_term("وبالقهوه", terms) == "قهوه"  # folded: "وبالقهوة" is "and with the coffee"
    assert word_term("lorem", terms) is None


def test_a_long_memory_is_cut_around_the_question_s_words():
    text = FILLER_EN * 60 + "Later we booked the vaccines appointment at the Salmiya clinic for Tuesday. " \
        + FILLER_EN * 60
    shown = focused(text, 300, _terms("When is my vaccine appointment?"))
    assert "vaccines appointment at the Salmiya clinic" in shown
    # Its opening sentence, then the stretch (the opening is kept).
    assert shown.startswith(FILLER_EN.strip() + " … ") and shown.endswith("…") and len(shown) <= 300
    # No word of the question there: its start, as before.
    assert focused(text, 300, _terms("Where is the lighthouse?")).startswith(FILLER_EN[:40])


def test_a_memory_that_names_its_subject_first_keeps_its_opening(monkeypatch):
    """Memories often name their subject first ("My sister Noura ...") and
    the question's other words come later: the opening sentence is shown,
    then " … " and the stretch. The stretch alone lost one dev answer this
    way (a name in the opening sentence)."""
    from kazma_core.memory import episode_text

    en = ("My sister Noura started a new job this month. " + FILLER_EN * 30
          + "She works as a dentist at the Salmiya clinic and loves it. " + FILLER_EN * 30)
    ar = ("أختي نورة بدأت عملاً جديداً هذا الشهر. " + FILLER_AR * 40
          + "تشتغل طبيبة أسنان في عيادة السالمية وتحب شغلها. " + FILLER_AR * 40)
    for text, question, name, stretch in (
        (en, "What does she do at the clinic, the dentist job?", "Noura", "dentist at the Salmiya clinic"),
        (ar, "شنو تشتغل في العيادة طبيبة أسنان؟", "نورة", "طبيبة أسنان في عيادة السالمية"),
    ):
        shown = focused(text, 300, _terms(question))
        assert name in shown and stretch in shown and " … " in shown and len(shown) <= 300
        # Negative control: the stretch alone shows the stretch, not the name.
        monkeypatch.setattr(episode_text, "RECALL_KEEP_OPENING", 0)
        alone = focused(text, 300, _terms(question))
        monkeypatch.undo()
        assert stretch in alone and name not in alone


def test_an_opening_the_stretch_already_shows_is_not_repeated():
    """When the stretch shown alone would begin inside the opening, the
    opening is not added: it would spend the stretch's room on text the
    stretch nearly shows already. A plain "opening first" rule lost one dev
    answer this way; it is the negative control."""
    from kazma_core.memory import episode_text

    text = ("I'm also interested in exploring different genres of music and discovering new artists. "
            "I recently discovered a bluegrass band that features a banjo player and started enjoying "
            "their music today. " + FILLER_EN * 30)
    terms = _terms("What is the artist that I started to listen to last Friday?")
    shown = focused(text, 200, terms)
    assert " … " not in shown
    assert "a bluegrass band that features a banjo player" in shown
    spans = episode_text._spans_of(text[:limits.RECALL_FOCUS_READ], terms)
    plain = episode_text._with_opening(text, spans, 198, 200, 0, alone_at=10**9)
    assert plain is not None and "a bluegrass band" not in plain[0]


def test_a_long_arabic_memory_is_cut_around_the_arabic_question_s_words():
    text = FILLER_AR * 80 + "وحجزنا موعد التطعيم في عيادة السالمية يوم الثلاثاء. " + FILLER_AR * 80
    shown = focused(text, 200, _terms("متى موعد التطعيم؟"))
    assert "موعد التطعيم في عيادة السالمية" in shown
    assert len(shown) <= 200
    # Vowel marks and the article on the memory's side are read too.
    marked = FILLER_AR * 80 + "وحَجَزْنَا مَوْعِدَ التَّطْعِيمِ يوم الثلاثاء. " + FILLER_AR * 80
    assert "التَّطْعِيمِ" in focused(marked, 200, _terms("متى موعد التطعيم؟"))


def test_negative_control_a_cut_that_reads_words_raw_misses_the_arabic(monkeypatch):
    """Read without the fold and the article rule, the memory's "التطعيم"
    is not the question's "تطعيم": the cut falls back to the start."""
    from kazma_core.memory import episode_text, query_terms

    text = FILLER_AR * 80 + "وحجزنا موعد التطعيم في عيادة السالمية يوم الثلاثاء. " + FILLER_AR * 80
    raw_terms = frozenset({"تطعيم"})
    assert "التطعيم" in focused(text, 200, raw_terms)  # the product reads it
    monkeypatch.setattr(query_terms, "word_term", lambda word, terms: word if word in terms else None)
    episode_text._spans_of.cache_clear()
    try:
        assert "التطعيم" not in focused(text, 200, raw_terms)
    finally:
        episode_text._spans_of.cache_clear()


def test_a_short_side_gives_its_room_to_the_other():
    answer = FILLER_EN * 40 + "The code is ORCHID-7. " + FILLER_EN * 40
    shown, hidden = display_at("What was the code?", answer, "", 500, _terms("What was the code?"))
    assert shown.startswith("User: What was the code? / Assistant: ")
    assert "ORCHID-7" in shown and hidden > 0
    assert len(shown) <= 500 + len("User:  / Assistant: ")


def test_what_lies_past_the_part_read_is_counted_and_marked():
    head = FILLER_EN * 10
    shown, hidden = display_at(head, "", "", 300, frozenset(), (5000, 0, 0))
    assert shown.endswith("…")
    assert hidden == len(" ".join(head.split())) - 299 + 5000


# ── The block: room by rank, and no line left out ──────────────────────


def _five(question_word: str = "ORCHID") -> list[RecallHit]:
    hits = []
    for i in range(5):
        answer = FILLER_EN * 30 + f"The {question_word} code for room {i} is {i}{i}{i}. " + FILLER_EN * 30
        hits.append(_hit(f"e_{i}", f"Which {question_word.lower()} code did we pick for room {i}?", answer))
    return hits


def test_the_best_memory_gets_the_lead_room_and_the_others_less():
    result = RecallResult(beliefs=[], episodes=_five(), focus_terms=_terms("Which orchid code for room 0?"))
    lines = _history_lines(format_recall_block(result, max_tokens=4000))
    assert len(lines) == 5
    assert "The ORCHID code for room 0 is 000" in lines[0]
    lead = len(lines[0].split(" [", 1)[0])
    others = [len(line.split(" [", 1)[0]) for line in lines[1:]]
    assert lead > limits.RECALL_OTHER_SHOW + 100
    assert all(n <= limits.RECALL_OTHER_SHOW + 80 for n in others)
    # Every cut line still says how to read the rest.
    assert all('memory_read("' in line for line in lines)


def test_negative_control_the_fixed_bounds_hide_the_answer():
    """A hit with no sides is shown as recall made it: the first 200 and 300
    characters, where the answer is not."""
    hits = [_hit(h.id, h.metadata["sides"][0], h.metadata["sides"][1], sides=False) for h in _five()]
    result = RecallResult(beliefs=[], episodes=hits, focus_terms=_terms("Which orchid code for room 0?"))
    block = format_recall_block(result, max_tokens=4000)
    assert "The ORCHID code for room 0 is 000" not in block


def _overrun() -> RecallResult:
    """A block whose budget the first room would overrun: five long
    conversations and facts before them."""
    facts = [RecallHit(id=f"b_{i}", content=f"user likes item number {i} " + "x" * 120, score=1.0,
                       kind="belief", metadata={}) for i in range(5)]
    return RecallResult(beliefs=facts, episodes=_five(), focus_terms=_terms("Which orchid code for room 0?"))


#: A whole-block budget (rules and fence included) the first room overruns
#: and the ladder fits: at 1,300 tokens the ladder shows all five, the first
#: room alone two.
_TIGHT = 1300


def test_no_line_is_left_out_the_others_give_way_first():
    block = format_recall_block(_overrun(), max_tokens=_TIGHT)
    lines = _history_lines(block)
    assert len(lines) == 5  # every conversation shown
    assert block.count("user likes item number") == 5  # and every fact
    lead = len(lines[0].split(" [", 1)[0])
    others = [len(line.split(" [", 1)[0]) for line in lines[1:]]
    assert max(others) <= limits.RECALL_OTHER_LEAST + 80  # the others gave way first, all the way
    assert lead > limits.RECALL_LEAD_LEAST  # then the best, only as far as needed
    # Inside the same budget the best line still holds the answer, which the
    # fixed bounds never show.
    assert "The ORCHID code for room 0 is 000" in lines[0]
    fixed = _overrun()
    fixed.episodes = [_hit(h.id, h.metadata["sides"][0], h.metadata["sides"][1], sides=False)
                      for h in fixed.episodes]
    assert "The ORCHID code for room 0 is 000" not in format_recall_block(fixed, max_tokens=_TIGHT)


def test_negative_control_without_the_ladder_a_line_is_left_out(monkeypatch):
    """The first room alone (no giving way) overruns the budget: a line is
    left out. The ladder is what keeps every line."""
    monkeypatch.setattr(recall_module, "RECALL_OTHER_LEAST", limits.RECALL_OTHER_SHOW)
    monkeypatch.setattr(recall_module, "RECALL_LEAD_LEAST", limits.RECALL_LEAD_SHOW)
    lines = _history_lines(format_recall_block(_overrun(), max_tokens=_TIGHT))
    assert len(lines) < 5


def test_a_possibly_related_memory_keeps_the_fixed_bounds(monkeypatch):
    """Room by rank goes only to memories recall rates strong; a "possibly
    related" one (``strength`` weak) keeps the fixed bounds, so an ordinary
    turn whose memories are loosely related stays as small as before (the
    W1a review). Negative control: the rule without the strength check gives
    the weak memory room too, and the block grows."""
    hits = _five()
    for h in hits[1:]:
        h.metadata["strength"] = "weak"
    result = RecallResult(beliefs=[], episodes=hits, focus_terms=_terms("Which orchid code for room 0?"))
    lines = _history_lines(format_recall_block(result, max_tokens=4000))
    assert len(lines) == 5
    assert "The ORCHID code for room 0 is 000" in lines[0]  # strong: its stretch
    fixed = {h.id: _shown(h.metadata["sides"][0], h.metadata["sides"][1], "")["display"] for h in hits}
    for i, line in enumerate(lines[1:], 1):
        assert line.startswith("- (possibly related) User: ")
        assert fixed[f"e_{i}"] in line and f"The ORCHID code for room {i}" not in line
    weak_only = RecallResult(beliefs=[], episodes=hits[1:], focus_terms=result.focus_terms)
    small = format_recall_block(weak_only, max_tokens=4000)
    monkeypatch.setattr(recall_module, "_roomy", lambda h: not (h.metadata or {}).get("joined"))
    grown = format_recall_block(weak_only, max_tokens=4000)
    assert len(grown) > len(small) + 400


def test_joined_and_another_member_s_lines_keep_recall_s_line():
    joined = _hit("e_j", "And then?", FILLER_EN * 30, sides=False)
    joined.metadata["joined"] = ["e_x"]
    joined.metadata["display"] = "(before that: User: hi / Assistant: hello) User: And then? / Assistant: ..."
    assert not recall_module._roomy(joined)
    member = _hit("e_m", "Where is Sara's car?", FILLER_EN * 30, sides=False)
    member.metadata["display"] = "Sara: Where is my car? / Assistant: ..."
    assert not recall_module._roomy(member)
    assert recall_module._roomy(_hit("e_u", "Where is my car?", FILLER_EN * 30, sides=False))


# ── Through the real recall ─────────────────────────────────────────────


class _NoEmbedder:
    """Meaning search unavailable: recall answers from its words alone."""

    dim = 8

    def encode(self, text: str) -> list[float]:
        raise RuntimeError("no embedder in this test")


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _NoEmbedder())
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


@pytest.mark.parametrize(("question", "answer", "asked", "expected"), [
    ("Can you keep my notes on the clinic visit?",
     FILLER_EN * 80 + "The vaccine appointment is at the Salmiya clinic on Tuesday. " + FILLER_EN * 80,
     "Where is my vaccine appointment clinic?", "vaccine appointment is at the Salmiya clinic"),
    ("احفظ ملاحظاتي عن زيارة العيادة",
     FILLER_AR * 80 + "موعد التطعيم في عيادة السالمية يوم الثلاثاء. " + FILLER_AR * 80,
     "وين موعد التطعيم في العيادة؟", "موعد التطعيم في عيادة السالمية"),
], ids=["en", "ar"])
def test_recall_shows_the_stretch_that_answers(mem, question, answer, asked, expected):
    """The live writer stores a long turn; the real ``recall`` finds it and
    keeps its sides; the block shows the stretch holding the question's
    words, bounded, with the note."""
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_core.memory.recall import recall

    eid = mirror_episode(session_id="s-room", turn_number=1, user_text=question, assistant_text=answer,
                         tier="recall")
    result = recall(asked, limit=5, tenant_id="default")
    assert [h.id for h in result.episodes] == [eid]
    assert result.episodes[0].metadata.get("sides") is not None
    block = format_recall_block(result)
    line = _history_lines(block)[0]
    assert expected in line
    assert f'memory_read("{eid}")' in line
    assert len(line) < limits.RECALL_LEAD_SHOW + 250


# ── A line that cannot fit takes no room (the step 5 review, M1) ─────────

def _five_ar() -> list[RecallHit]:
    hits = []
    for i in range(5):
        answer = FILLER_AR * 40 + f"رمز الأوركيد للغرفة {i} هو {i}{i}{i}. " + FILLER_AR * 40
        hits.append(_hit(f"a_{i}", f"أي رمز أوركيد اخترنا للغرفة {i}؟", answer))
    return hits


def _summary(text: str) -> RecallHit:
    return RecallHit(id="sum_1", content=text[:80], score=0.5, kind="summary", source="summary",
                     metadata={"level": "week", "display": "Week of May 1, 2023 -- " + text})


def _with_and_without(language: str, budget: int, unfit: str = "summary") -> tuple[str, str]:
    """The same recall at *budget* with one more line no step can fit (a long
    weekly summary, or a long last conversation) and without it."""
    if language == "en":
        episodes, terms, long = _five(), _terms("Which orchid code for room 0?"), FILLER_EN * 80
    else:
        episodes, terms, long = _five_ar(), _terms("أي رمز أوركيد للغرفة 0؟"), FILLER_AR * 120
    facts = [RecallHit(id=f"b_{i}", content=f"user likes item number {i} " + "x" * 120, score=1.0, kind="belief",
                       metadata={}) for i in range(5)]
    without = format_recall_block(RecallResult(beliefs=facts, episodes=episodes, focus_terms=terms),
                                  max_tokens=budget)
    if unfit == "summary":
        more = RecallResult(beliefs=facts, episodes=episodes, summaries=[_summary(long)], focus_terms=terms)
    else:
        # a conversation with no sides is shown whole, at its own length
        last = _hit("e_long", "a long conversation", long, sides=False)
        more = RecallResult(beliefs=facts, episodes=[*episodes, last], focus_terms=terms)
    return format_recall_block(more, max_tokens=budget), without


#: Every tenth budget from a tight block to a roomy one (the delta review's
#: sweep: 32 of 155 English and 28 of 244 Arabic budgets failed with the
#: frame counted at its largest).
_SWEEP = range(800, 4001, 10)
_UNFIT_MARK = {"summary": "Week of May 1, 2023", "conversation": "e_long"}


def _left_out_yet_different(language: str, unfit: str) -> list[int]:
    """The budgets at which the unfit line is not shown, yet the block is not
    the block without it."""
    bad = []
    for budget in _SWEEP:
        with_one, without = _with_and_without(language, budget, unfit)
        if _UNFIT_MARK[unfit] not in with_one and with_one != without:
            bad.append(budget)
    return bad


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_summary_that_cannot_fit_takes_no_room_from_the_excerpts(language):
    """A long weekly summary no step can fit was dropped, but the excerpts
    had shrunk for it (822 to 623 characters on LongMemEval): the block is
    now the block without it, in English and Arabic."""
    with_one, without = _with_and_without(language, _TIGHT)
    assert "Week of May 1, 2023" not in with_one
    assert with_one == without
    lead = len(_history_lines(with_one)[0].split(" [", 1)[0])
    assert lead > limits.RECALL_LEAD_LEAST + 50, lead


@pytest.mark.parametrize("unfit", ["summary", "conversation"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_whenever_a_line_is_left_out_the_block_is_the_block_without_it(language, unfit):
    """The class, at every tenth budget from 800 to 4,000: a block that
    leaves out a line no step can fit, a summary or a later conversation, is
    the same recall's block without that line, header and footer included."""
    shown = sum(_UNFIT_MARK[unfit] not in _with_and_without(language, b, unfit)[0] for b in _SWEEP[::40])
    assert shown, "the sweep never leaves the line out"
    assert _left_out_yet_different(language, unfit) == []


def test_control_the_frame_counted_at_its_largest_fails_the_sweep(monkeypatch):
    """The negative control: room by rank with the frame counted at its
    largest, the header and footer of a summary not shown included (0c1789cd5),
    leaves out the summary yet differs from the block without it."""
    real = recall_module._room_by_rank

    def frame_counted(result, kept):
        shown, _exact = real(result, lambda room, exact: kept(room, False))
        return shown, False

    monkeypatch.setattr(recall_module, "_room_by_rank", frame_counted)
    assert _left_out_yet_different("en", "summary") or _left_out_yet_different("ar", "summary")


def test_control_the_narrowest_step_fails_the_conversation_sweep(monkeypatch):
    """The conversation half's negative control (the final review's L2): room
    by rank as it was before M1 (2179116ec): the first step that keeps every
    line, else the narrowest, the frame counted at its largest. It gives the
    other conversations less room for a last conversation that cannot fit."""
    real = recall_module._room_by_rank
    every = (10**6,) * 3

    def before_m1(result, kept):
        calls = []

        def all_or_nothing(room, _exact):
            calls.append(room)
            counts, all_fit = kept(room, False)
            # the target is every line; a step reaches it only if all fit
            if len(calls) == 2 or all_fit:
                return every, all_fit
            return counts, all_fit

        shown, _exact = real(result, all_or_nothing)
        return shown, False

    monkeypatch.setattr(recall_module, "_room_by_rank", before_m1)
    assert _left_out_yet_different("en", "conversation") or _left_out_yet_different("ar", "conversation")


def test_a_summary_that_fits_still_takes_its_room():
    """The control: a short summary that fits at a narrower step is shown,
    and the excerpts give way for it, as before."""
    facts = [RecallHit(id=f"b_{i}", content=f"user likes item number {i} " + "x" * 120, score=1.0, kind="belief",
                       metadata={}) for i in range(5)]
    terms = _terms("Which orchid code for room 0?")
    short = _summary("The orchid codes were chosen for each room. " * 3)
    block = format_recall_block(RecallResult(beliefs=facts, episodes=_five(), summaries=[short], focus_terms=terms),
                                max_tokens=_TIGHT + 60)
    assert "Week of May 1, 2023" in block and len(_history_lines(block)) == 5
