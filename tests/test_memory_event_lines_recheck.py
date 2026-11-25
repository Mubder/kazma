"""Regressions for the re-check of the third review's fixes of the memory plan's
W4 (0 H, 4 M, 7 L; on f68b21ae5, 2026-11-25), in English, Gulf Arabic and MSA.

* **M1** Forgetting finishes on its first call whatever the archive holds: an
  archived fact names no predecessor that is not there, comes back as the kind
  it was, with no vector, and leaves the archive only when ``beliefs`` took it.
* **M2, M3** The user's own line is kept as main keeps it: at least 98 % in
  each language, the languages within two points of each other
  (``tests/fixtures/memory_events/own_words_openings.json``).
* **M4** "Too" and "again" undo no correction that names the old value.
* **Low** More ways to put right (L-a), a plan in MSA (L-b), "also" in Arabic
  (L-c), the ledger pass reads the archive (L-e).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from kazma_core.memory import event_text, fact_days, forget, pasted_text

from tests.test_memory_event_lines_review import TOLD, at, event, mem  # noqa: F401 - the fixture
from tests.test_memory_event_lines_second_review import DAY, _tell

# ruff: noqa: F811
OWN = json.loads((Path(__file__).parent / "fixtures/memory_events/own_words_openings.json").read_text(encoding="utf-8"))
LANGS = ("english", "gulf", "msa")
LATER = at(TOLD) + 400 * DAY

# ── M1: a forget finishes ────────────────────────────────────────────────

CARS = {"english": (("a Toyota", "My car is a Toyota"), ("a Honda", "My car is a Honda now"),
                    ("my Honda", "I sold my Honda yesterday")),
        "gulf": (("تويوتا", "سيارتي تويوتا"), ("هوندا", "سيارتي صارت هوندا"), ("الهوندا", "بعت الهوندا أمس")),
        "msa": (("تويوتا", "سيارتي تويوتا"), ("هوندا", "أصبحت سيارتي هوندا"), ("الهوندا", "بعتُ الهوندا أمس"))}


def _archive(mem, monkeypatch, how: str) -> None:
    if how == "night":
        from kazma_core.memory.macro_sleep import run_macro_sleep

        assert run_macro_sleep(mem.db, tenant_id="default", now=LATER)["archived_beliefs"]
        return
    from kazma_ui import memory_api

    path = mem.db.execute("PRAGMA database_list").fetchone()[2]

    def opened():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(memory_api, "_conn", opened)
    monkeypatch.setattr(memory_api, "_memory_tenant_id", lambda: "default")
    assert memory_api._archive_invalidated_sync()["archived"]


def _cars(mem, lang: str) -> list[str]:
    """Toyota, then Honda, then the Honda sold: two cars one after the other,
    the second closed by an event. The turns' ids."""
    ids = [mem.episode(n, said) for n, (_v, said) in enumerate(CARS[lang], 1)]
    for turn, (value, said) in enumerate(CARS[lang][:2], 1):
        mem.tell([{**event("car", value, None), "predicate_type": "functional"}], said, turn=turn, on=f"2026-0{turn}-01")
    value, said = CARS[lang][2]
    mem.tell([event("sold", value, "2026-05-30", replaces="f1")], said, turn=3, held=mem.held()[0])
    return ids


@pytest.mark.parametrize("keys", ["ON", "OFF"])
@pytest.mark.parametrize("how", ["night", "page"])
@pytest.mark.parametrize("lang", LANGS)
def test_a_forget_finishes_with_an_archived_predecessor(mem, monkeypatch, lang, how, keys):
    mem.db.execute(f"PRAGMA foreign_keys = {keys}")
    ids = _cars(mem, lang)
    mem.db.execute("UPDATE beliefs SET visibility = 'swarm_shared'")
    _archive(mem, monkeypatch, how)
    mem.forget(ids[2])
    assert mem.facts("predicate", "predicate_type", "object", "visibility", "supersedes_id") == [
        ("car", "functional", CARS[lang][1][0], "swarm_shared", None)]
    left = [json.loads(r[0])["object"] for r in mem.db.execute("SELECT original_belief_json FROM beliefs_archive")]
    assert left == [CARS[lang][0][0]]
    mem.forget(ids[1])
    mem.forget(ids[0])
    assert mem.facts() == [] and CARS[lang][0][0] not in "".join(
        r[0] for r in mem.db.execute("SELECT original_belief_json FROM beliefs_archive"))


SHOWS = {"english": ("a pottery show", "the pottery show at Dar Al Athar", "the pottery show at Dar Al Athar in Kuwait"),
         "gulf": ("معرض فخار", "معرض فخار بدار الآثار", "معرض فخار بدار الآثار في الكويت"),
         "msa": ("معرض الفخار", "معرض الفخار في دار الآثار", "معرض الفخار في دار الآثار الإسلامية")}


@pytest.mark.parametrize("order", [(2, 1, 0), (1, 2, 0), (0, 2, 1)])
@pytest.mark.parametrize("how", ["night", "page"])
@pytest.mark.parametrize("lang", LANGS)
def test_a_chain_of_archived_entries_is_forgotten_in_any_order(mem, monkeypatch, lang, how, order):
    mem.db.execute("PRAGMA foreign_keys = ON")
    shows = SHOWS[lang]
    ids = [mem.episode(n, said) for n, said in enumerate(shows, 1)]
    for turn, show in enumerate(shows, 1):
        mem.tell([event("attended", show, "2026-05-30", **({"replaces": "f1"} if turn > 1 else {}))], show, turn=turn,
                 held=mem.held()[0])
    mem.db.execute("UPDATE beliefs SET embedding = ? WHERE valid_until IS NOT NULL", (b"\x00" * 4096,))
    _archive(mem, monkeypatch, how)
    standing = set(shows)
    for turn in order:
        mem.forget(ids[turn])
        standing.discard(shows[turn])
        assert [v for _p, v in mem.facts()] == [next(s for s in reversed(shows) if s in standing)] if standing else (
            mem.facts() == [])
        for vector, _kind in mem.facts("embedding", "predicate_type"):
            assert vector is None or isinstance(vector, bytes)
    assert "".join(r[0] for r in mem.db.execute("SELECT original_belief_json FROM beliefs_archive")).count(shows[0]) == 0


def test_an_insert_not_taken_leaves_the_archive_alone(mem, monkeypatch):
    ids = _cars(mem, "english")
    _archive(mem, monkeypatch, "night")
    for bid, blob in mem.db.execute("SELECT id, original_belief_json FROM beliefs_archive").fetchall():
        mem.db.execute("UPDATE beliefs_archive SET original_belief_json = ? WHERE id = ?",
                       (json.dumps({**json.loads(blob), "valid_from": None}), bid))
    mem.forget(ids[2])
    blobs = [json.loads(r[0]) for r in mem.db.execute("SELECT original_belief_json FROM beliefs_archive")]
    assert sorted(b["object"] for b in blobs) == ["a Honda", "a Toyota"] and mem.facts() == []
    assert not any("closed_by" in (b["metadata_json"] or "") for b in blobs)


def test_a_row_archived_before_its_kind_was_kept_comes_back_as_its_name_says(mem, monkeypatch):
    ids = _cars(mem, "english")
    _archive(mem, monkeypatch, "night")
    mem.db.execute("UPDATE beliefs_archive SET original_belief_json = json_remove(original_belief_json, "
                   "'$.predicate_type', '$.visibility')")
    mem.forget(ids[2])
    assert mem.facts("object", "visibility") == [("a Honda", "private")]


def test_control_the_old_way_back_raises(mem, monkeypatch):
    """Negative control (M1): the row as it was archived, its pointer too."""
    def as_it_was(conn, bid, original, meta):
        row = {**original, "id": bid, "valid_until": None, "invalidated_at": None, "metadata_json": json.dumps(meta)}
        cols = [c for c in row if c in forget._table_columns(conn, "beliefs")]
        conn.execute(f"INSERT OR IGNORE INTO beliefs({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [row[c] for c in cols])
        return True

    monkeypatch.setattr(forget, "_from_archive", as_it_was)
    mem.db.execute("PRAGMA foreign_keys = ON")
    ids = _cars(mem, "english")
    _archive(mem, monkeypatch, "night")
    with pytest.raises(sqlite3.IntegrityError):
        mem.forget(ids[2])


# ── L-e: the ledger pass reads the archive ───────────────────────────────


@pytest.mark.parametrize("lang", LANGS)
def test_the_ledger_opens_an_archived_entry(mem, monkeypatch, lang):
    shows = SHOWS[lang]
    mem.episode(1, shows[0])
    second = mem.episode(2, shows[1])
    mem.forget(second)  # the forget lands first; the pass that read the turn writes after it
    mem.tell([event("attended", shows[0], "2026-05-30")], shows[0], turn=1)
    held = mem.held()[0]
    monkeypatch.setattr("kazma_core.memory.forget.turn_forgotten", lambda *a, **k: False, raising=False)
    mem.db.execute("UPDATE memory_forgotten SET forgotten_at = forgotten_at + 1000000000")
    _closed_by_turn_two(mem, held)
    _archive(mem, monkeypatch, "night")
    assert mem.facts() == []
    forget.enforce_ledger(mem.db)
    assert mem.facts() == [("attended", shows[0])]


def _closed_by_turn_two(mem, held) -> None:
    """What a deep pass of the forgotten turn left behind: the entry closed."""
    by = {"closed_by": {"session": "chat-1", "turn": 2, "at": at(TOLD), "belief_id": "gone"}}
    row = mem.db.execute("SELECT id, metadata_json FROM beliefs WHERE id = ?", (held[0]["id"],)).fetchone()
    mem.db.execute("UPDATE beliefs SET valid_until = ?, invalidated_at = ?, metadata_json = ? WHERE id = ?",
                   (at(TOLD), at(TOLD), json.dumps({**json.loads(row[1] or "{}"), **by}), row[0]))


# ── M2, M3: the user's own lines ─────────────────────────────────────────


def _kept(mem, shapes, n: int) -> tuple[int, list[str]]:
    kept, lost = 0, []
    for shape in shapes:
        for told in OWN["events"]:
            mem.db.execute("DELETE FROM beliefs")
            said = shape[n].format(told["said"][n])
            mem.tell([event(told["predicate"], told["value"][n], told["day"])], said)
            if [fact_days.event_day(m) for _o, m in mem.days()] == [told["day"] or ""]:
                kept += 1
            else:
                lost.append(said)
    return kept, lost


@pytest.mark.parametrize("which", sorted(OWN["own"]))
def test_the_users_own_lines_stay_in_each_language(mem, which):
    shapes = OWN["own"][which]
    total = len(shapes) * len(OWN["events"])
    # 'fresh_after' holds one line honestly unclear ("Please rephrase: too long. ...").
    least = 0.97 if which == "fresh_after" else 0.98
    rates = {}
    for n, lang in enumerate(LANGS):
        kept, lost = _kept(mem, shapes, n)
        rates[lang] = kept / total
        assert rates[lang] >= least, (lang, kept, total, lost)
    assert max(rates.values()) - min(rates.values()) <= 0.02, rates


@pytest.mark.parametrize(("lang", "said"), [("en", "I sold my car yesterday"), ("ar", "بعت سيارتي أمس"),
                                            ("ar", "بعتُ سيارتي أمس")])
def test_a_label_alone_is_no_speaker(lang, said):
    lost = [w for w in OWN["labels"][lang] if not pasted_text.told_and_pasted(f"{w}: {said}")[0].rstrip().endswith(said)]
    assert lost == []


@pytest.mark.parametrize(("lang", "said", "again", "no"), [
    ("en", "I sold my car yesterday", "{} was fun", "{}: no time"),
    ("ar", "بعت سيارتي أمس", "{} كانت حلوة", "{}: ما لحقت، لا"),
    ("ar", "بعتُ سيارتي أمس", "كانت {} جميلة", "{}: لا وقت بعد"),
])
def test_a_heading_named_again_or_beside_a_no_is_no_speaker(lang, said, again, no):
    words = OWN["headings"][lang]
    lost = [w for w in words if said not in pasted_text.told_and_pasted(f"{w}: {said}\n{again.format(w)}")[0]]
    lost += [f"{a}/{b}" for a, b in zip(words, words[1:], strict=False)
             if said not in pasted_text.told_and_pasted(f"{a}: {said}\n{no.format(b)}")[0]]
    lost += [f"{a}/{a}" for a in words if said not in pasted_text.told_and_pasted(f"{a}: {said}\n{a}: {a}")[0]]
    assert lost == []


@pytest.mark.parametrize("said", [
    "Omar: I bought a Rolex yesterday\nMe: nice",
    "عمر: شريت Rolex أمس\nأنا: حلو",
    "عمر: اشتريت Rolex أمس\nأنا: جميل",
    "Omar: hi\nSara: I bought a Rolex yesterday\nOmar: wow",
    "عمر: هلا\nسارة: شريت Rolex أمس\nعمر: واو",
    "عمر: مرحبا\nسارة: اشتريت Rolex أمس\nعمر: رائع",
    "Omar: I bought a Rolex yesterday\nSara: congratulations Omar",
    "عمر: شريت Rolex أمس\nسارة: مبروك عليك",
    "عمر: اشتريت Rolex أمس\nسارة: مبارك لك",
    "Omar: I bought a Rolex yesterday\nWhat should I tell Omar?",
    "عمر: شريت Rolex أمس\nشنو أقول حق عمر؟",
    "عمر: اشتريت Rolex أمس\nماذا أقول لعمر؟",
    "From: Omar\nSubject: hi\n\nI bought a Rolex yesterday.",
    "من: عمر\nالموضوع: هلا\n\nشريت Rolex أمس.",
    "من: عمر\nالموضوع: مرحبا\n\nاشتريت Rolex أمس.",
    "From: Omar <omar@x.y>\nI bought a Rolex yesterday.",
    "من: عمر <omar@x.y>\nشريت Rolex أمس.",
    "من: عمر <omar@x.y>\nاشتريت Rolex أمس.",
    "My boss wrote: I bought a Rolex yesterday",
    "مديري كتب: شريت Rolex أمس",
    "كتب مديري: اشتريت Rolex أمس",
    "Translate what my sister wrote: I bought a Rolex yesterday",
    "ترجم كلام أختي: شريت Rolex أمس",
    "ترجم رسالة أختي: اشتريت Rolex أمس",
])
def test_what_shows_a_conversation_is_still_another_s(mem, said):
    """The other side of the narrowing."""
    mem.tell([event("bought", "a Rolex", "2026-05-30")], said)
    assert mem.facts() == [], pasted_text.told_and_pasted(said)


@pytest.mark.parametrize(("said", "predicate", "value", "day"), [
    ("> Sony WH-1000XM5 wireless headphones\nJust bought these!", "bought", "Sony WH-1000XM5 headphones", None),
    ("> سماعات سوني XM5 لاسلكية\nتوني شريتها!", "bought", "سماعات سوني XM5", None),
    ("> سماعات سوني XM5 لاسلكية\nاشتريتها للتو!", "bought", "سماعات سوني XM5", None),
    ("I bought the watch in this ad:\n> Rolex Submariner 2019 steel, $9000", "bought", "Rolex Submariner 2019", None),
    ("شريت الساعة اللي في هالإعلان:\n> رولكس صبمارينر ٢٠١٩ فولاذ", "bought", "رولكس صبمارينر ٢٠١٩", None),
    ("اشتريت الساعة التي في هذا الإعلان:\n> رولكس صبمارينر ٢٠١٩ فولاذ", "bought", "رولكس صبمارينر ٢٠١٩", None),
    ("I went to this one yesterday:\n> Sadu House pottery exhibition, free entry", "attended",
     "Sadu House pottery exhibition", "2026-05-30"),
    ("رحت هذا أمس:\n> معرض فخار بيت السدو، الدخول مجاني", "attended", "معرض فخار بيت السدو", "2026-05-30"),
    ("ذهبت إلى هذا أمس:\n> معرض فخار بيت السدو، الدخول مجاني", "attended", "معرض فخار بيت السدو", "2026-05-30"),
    ("Translate this to French: hello. Also I sold my car yesterday", "sold", "my car", "2026-05-30"),
    ("ترجم للفرنسي: مرحبا. وبعد بعت سيارتي أمس", "sold", "سيارتي", "2026-05-30"),
    ("ترجم إلى الفرنسية: مرحبا. وأيضا بعت سيارتي أمس", "sold", "سيارتي", "2026-05-30"),
    ("Translate to French: good morning.\nI sold my car yesterday, how do I say that?", "sold", "my car", "2026-05-30"),
    ("ترجم للفرنسي: صباح الخير.\nبعت سيارتي أمس، شلون أقولها؟", "sold", "سيارتي", "2026-05-30"),
    ("ترجم إلى الفرنسية: صباح الخير.\nبعت سيارتي أمس، كيف أقولها؟", "sold", "سيارتي", "2026-05-30"),
    ("[30/05/2026, 10:11] Omar: congrats\n[30/05/2026, 10:12] Me: thanks\nI sold my car yesterday", "sold", "my car",
     "2026-05-30"),
    ("[30/05/2026, 10:11] عمر: مبروك\n[30/05/2026, 10:12] أنا: مشكور\nبعت سيارتي أمس", "sold", "سيارتي", "2026-05-30"),
    ("[30/05/2026, 10:11] عمر: مبارك\n[30/05/2026, 10:12] أنا: شكرا\nأنا بعتُ سيارتي أمس", "sold", "سيارتي",
     "2026-05-30"),
    ("From: Omar\nSubject: car\n\nCan you send the papers?\n\nI already sold my car yesterday. Help me reply.", "sold",
     "my car", "2026-05-30"),
    ("من: عمر\nالموضوع: السيارة\n\nتقدر ترسل الأوراق؟\n\nأنا بعت سيارتي أمس خلاص. ساعدني أرد.", "sold", "سيارتي",
     "2026-05-30"),
    ("من: عمر\nالموضوع: السيارة\n\nهل ترسل الأوراق؟\n\nلقد بعت سيارتي أمس. بماذا أجيبه؟", "sold", "سيارتي",
     "2026-05-30"),
    ("Forwarded message\nFrom: Omar\nhi\n\nI sold my car yesterday, what should I answer Omar?", "sold", "my car",
     "2026-05-30"),
    ("رسالة محولة\nمن: عمر\nهلا\n\nبعت سيارتي أمس، شنو أرد على عمر؟", "sold", "سيارتي", "2026-05-30"),
    ("رسالة محولة\nمن: عمر\nمرحبا\n\nبعت سيارتي أمس، ماذا أقول لعمر؟", "sold", "سيارتي", "2026-05-30"),
])
def test_the_users_own_line_beside_a_paste_stays(mem, said, predicate, value, day):
    mem.tell([event(predicate, value, day)], said)
    assert [(v, fact_days.event_day(m)) for v, m in mem.days()] == [(value, day or "")], pasted_text.told_and_pasted(said)


@pytest.mark.parametrize(("said", "value"), [
    ("> Sony WH-1000XM5 headphones, wireless\nJust look at these!", "Sony WH-1000XM5 headphones"),
    ("> سماعات سوني XM5 لاسلكية\nشوف هذي!", "سماعات سوني XM5"),
    ("> سماعات سوني XM5 لاسلكية\nانظر إلى هذه!", "سماعات سوني XM5"),
])
def test_control_what_is_not_pointed_at_stays_out(mem, said, value):
    """Negative control: looked at is not bought."""
    mem.tell([event("bought", value, None)], said)
    assert mem.facts() == []


def test_control_a_role_or_a_second_naming_as_the_evidence_loses_lines(mem, monkeypatch):
    """Negative control (M2): what f68b21ae5 took for a speaker."""
    monkeypatch.setattr(pasted_text, "_certain_speaker", lambda label: pasted_text._own._names_a_speaker(label))
    for n in range(3):
        kept, _lost = _kept(mem, OWN["own"]["review"], n)
        assert kept < 0.96 * len(OWN["own"]["review"]) * 3, (LANGS[n], kept)


def test_control_without_the_users_own_heads_lines_are_lost(mem, monkeypatch):
    monkeypatch.setattr(pasted_text, "_the_users_own", lambda head: False)
    monkeypatch.setattr(pasted_text, "_MY_WORDS_EN", pasted_text.re.compile(r"(?!)"))
    for n in range(3):
        assert _kept(mem, OWN["own"]["review"], n)[0] < _whole("review"), LANGS[n]


def _whole(which: str) -> int:
    return len(OWN["own"][which]) * len(OWN["events"])


# ── M4: "too" and "again" ────────────────────────────────────────────────

NAMES = {"english": ("Ali", "I met Ali yesterday", "Omar"), "gulf": ("علي", "قابلت علي أمس", "عمر"),
         "msa": ("علي", "التقيت علي أمس", "عمر")}


@pytest.mark.parametrize(("first", "said", "then", "day", "put_right"), [
    ("Ali", "I met Ali yesterday", "Omar", "2026-05-30", "It was Omar, not Ali, I'm too tired"),
    ("علي", "قابلت علي أمس", "عمر", "2026-05-30", "كان عمر مو علي، وأنا تعبان كمان"),
    ("علي", "التقيت علي أمس", "عمر", "2026-05-30", "كان عمر وليس علي، وأنا متعب أيضا"),
    ("Paris", "I visited Paris yesterday", "Lyon", "2026-05-30", "Not Paris, it was Lyon again"),
    ("باريس", "زرت باريس أمس", "ليون", "2026-05-30", "مو باريس، ليون مرة ثانية"),
    ("باريس", "زرتُ باريس أمس", "ليون", "2026-05-30", "ليست باريس، بل ليون مرة أخرى"),
    ("Ali", "I met Ali yesterday", "Ali", "2026-05-29", "No, it was Friday, I was tired too"),
    ("علي", "قابلت علي أمس", "علي", "2026-05-29", "لا كان الجمعة، وكنت تعبان كمان"),
    ("علي", "التقيت علي أمس", "علي", "2026-05-29", "لا، كان الجمعة، وكنت متعبا أيضا"),
])
def test_too_and_again_undo_no_correction(mem, first, said, then, day, put_right):
    rows = _tell(mem, (TOLD, said, [event("met", first, "2026-05-30")]),
                 ("2026-06-01", put_right, [event("met", then, day, replaces="f1")]))
    assert rows == [("met", then, day)]


@pytest.mark.parametrize(("first", "said", "then", "told"), [
    ("bananas", "I bought bananas yesterday", "a lamp", "I bought a lamp yesterday too"),
    ("bananas", "I bought bananas yesterday", "a lamp", "Plus a lamp yesterday"),
    ("موز", "شريت موز أمس", "لمبة", "وشريت لمبة أمس بعد"),
    ("موز", "شريت موز أمس", "لمبة", "شريت لمبة أمس برضه"),
    ("موز", "شريت موز أمس", "لمبة", "شريت بعد لمبة أمس"),
    ("موز", "شريت موز أمس", "لمبة", "هم شريت لمبة أمس"),
    ("موز", "اشتريت الموز أمس", "مصباح", "وكذلك اشتريت مصباحا أمس"),
    ("موز", "اشتريت الموز أمس", "مصباح", "بالإضافة إلى ذلك اشتريت مصباحا أمس"),
])
def test_one_more_closes_nothing_in_any_words(mem, first, said, then, told):
    """L-c, and the control of M4: no denial, so both stay."""
    rows = _tell(mem, (TOLD, said, [event("bought", first, "2026-05-30")]),
                 (TOLD, told, [event("bought", then, "2026-05-30", replaces="f1")]))
    assert [v for _p, v, _d in rows] == [first, then]


@pytest.mark.parametrize("said", ["I'm too tired", "رحت بعد الدوام", "وصلت بعد ساعة", "ذهبت بعد صلاة الجمعة", "هم قالوا لي"])
def test_what_is_no_once_more(said):
    assert not event_text.says_once_more(said)


def test_control_the_old_value_need_be_denied_by_name(mem, monkeypatch):
    monkeypatch.setattr(event_text, "_denies_value", lambda folded, value: False)
    rows = _tell(mem, (TOLD, "I visited Paris yesterday", [event("visited", "Paris", "2026-05-30")]),
                 (TOLD, "Not Paris, it was Lyon again", [event("visited", "Lyon", "2026-05-30", replaces="f1")]))
    assert len(rows) == 2


# ── L-a: more ways to put right ──────────────────────────────────────────


@pytest.mark.parametrize(("lang", "put_right"), [
    *(("english", t) for t in (
        "Wait no, it was Omar", "No wait it was Omar", "Scratch that, Omar", "My bad, Omar", "I misspoke, it was Omar",
        "Apologies, Omar", "Forgive me, it was Omar", "I said Ali but it was Omar", "I mixed them up, it was Omar")),
    *(("gulf", t) for t in ("لا لا عمر", "خربطت، كان عمر", "لخبطت كان عمر", "سوري كان عمر", "لا، كان عمر", "عدلها عمر")),
    *(("msa", t) for t in ("لا، بل عمر", "معذرة، كان عمر", "أعتذر، إنه عمر", "التبس علي الأمر، كان عمر", "لا، كان عمر")),
])
def test_a_correction_in_other_words(mem, lang, put_right):
    first, said, then = NAMES[lang]
    rows = _tell(mem, (TOLD, said, [event("met", first, "2026-05-30")]),
                 ("2026-06-01", put_right, [event("met", then, None, replaces="f1")]))
    assert rows == [("met", then, "2026-05-30")]


@pytest.mark.parametrize(("lang", "told"), [
    ("english", "No problem, I met Omar"), ("gulf", "لا مشكلة، قابلت عمر"), ("msa", "لا بأس، التقيت عمر"),
    ("english", "I apologized to a friend, Omar, whom I met"), ("gulf", "قابلت عمر وهو سوري"),
    ("msa", "التقيت عمر وهو سوري")])
def test_control_no_correction_closes_nothing(mem, lang, told):
    first, said, then = NAMES[lang]
    rows = _tell(mem, (TOLD, said, [event("met", first, "2026-05-30")]),
                 ("2026-06-01", told, [event("met", then, None, replaces="f1")]))
    assert [v for _p, v, _d in rows] == [first, then]


# ── L-b: a plan, in each language ────────────────────────────────────────


@pytest.mark.parametrize(("said", "day", "to_come"), [
    ("I will attend the concert on 3 June", "2026-06-03", True), ("بحضر الحفلة يوم ٣ يونيو", "2026-06-03", True),
    ("سأحضر الحفل يوم ٣ يونيو", "2026-06-03", True),
    ("We will visit Dubai on Friday", "2026-06-05", True), ("بنزور دبي يوم الجمعة", "2026-06-05", True),
    ("سنزور دبي يوم الجمعة", "2026-06-05", True),
    ("I intend to sell my car on Friday", "2026-06-05", True), ("ناوي أبيع سيارتي الجمعة", "2026-06-05", True),
    ("أنوي بيع سيارتي يوم الجمعة", "2026-06-05", True),
    ("I'm travelling to Rome the day after tomorrow", "2026-06-02", True), ("مسافر روما عقب بكرة", "2026-06-02", True),
    ("سأسافر إلى روما بعد غد", "2026-06-02", True),
    ("My flight to Rome is in two days", "2026-06-02", True), ("سفري لروما بعد يومين", "2026-06-02", True),
    ("رحلتي إلى روما بعد يومين", "2026-06-02", True),
    ("My surgery is in 3 days", "2026-06-03", True), ("عمليتي بعد ٣ أيام", "2026-06-03", True),
    ("عمليتي بعد ثلاثة أيام", "2026-06-03", True),
    ("I was going to the concert on 3 June", "2026-06-03", False), ("كنت رايح الحفلة يوم ٣ يونيو", "2026-06-03", False),
    ("كنت ذاهبا إلى الحفل يوم ٣ يونيو", "2026-06-03", False),
    ("I'm taking a breath, I sold the car on 3 June", "2026-06-03", False),
    ("باخذ نفس، بعت السيارة يوم ٣ يونيو", "2026-06-03", False),
    ("سآخذ نفسا، بعت السيارة يوم ٣ يونيو", "2026-06-03", False),
    ("I finished it in two days", "2026-06-02", False), ("خلصته بعد يومين", "2026-06-02", False),
    ("أنهيته، وبعد يومين حصلت على الجائزة", "2026-06-02", False),
    ("I asked the doctor about 3 June", "2026-06-03", False), ("سألت الدكتور عن ٣ يونيو", "2026-06-03", False),
    ("سألت الطبيب عن ٣ يونيو", "2026-06-03", False),
])
def test_what_is_to_come_is_read_alike_in_each_language(said, day, to_come):
    from kazma_core.memory.time_words import says_to_come

    assert says_to_come(said, day, TOLD) is to_come


@pytest.mark.parametrize(("said", "value"), [
    ("I will attend the jazz concert on 3 June", "the jazz concert"), ("بحضر حفلة الجاز يوم ٣ يونيو", "حفلة الجاز"),
    ("سأحضر حفل الجاز يوم ٣ يونيو", "حفل الجاز")])
def test_a_plan_is_kept_as_one_in_each_language(mem, said, value):
    mem.tell([event("attended", value, "2026-06-03")], said)
    assert [m.get("planned") for _v, m in mem.days()] == ["2026-06-03"]


def test_control_the_past_of_i_is_read(monkeypatch):
    from kazma_core.memory import time_words

    monkeypatch.setattr(time_words, "_TOLD_DONE", time_words.re.compile(r"(?!)"))
    assert time_words.says_to_come("كنت رايح الحفلة يوم ٣ يونيو", "2026-06-03", TOLD)
