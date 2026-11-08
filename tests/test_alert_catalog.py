"""The alert catalog and the one card (unified alerts plan, Stage 1).

``kazma_core/observability/alert_catalog.py`` names every alert Kazma's four
senders can raise; the guard and Kazma Watch read generated copies of it. The
gates here hold the table to the senders' own source, both ways, so an alert
added anywhere is catalogued the day it is added and an entry nothing raises
any more goes:

* every ops ``alert(`` key in the product packages is a catalog entry, and
  every ops entry is raised (keys read through f-strings, wrappers and local
  assignments by ``tests/_alert_senders.py``; a key it cannot read, and a
  wildcard that stands for a known table, are declared below with where
  their values come from);
* every guard ``_page(`` names a ``kind=`` the catalog holds, and every guard
  entry is paged;
* every condition Kazma Watch diagnoses is an entry, and every Watch entry is
  diagnosed (or is its recovery);
* every lifecycle event is an entry, with the card head and severity the
  notifier uses;
* no condition has two owning senders unless ``SHARED_OWNERS`` says why;
* the committed copies equal the table.

The card: ``tests/fixtures/alert_card/cards.json`` holds each input and the
exact text; Kazma's card and the guard's copy are checked here, Kazma
Watch's ``card.js`` by the observer's Node tests.

Each gate runs its checker on a planted violation first (the negative
control): a checker that cannot fail proves nothing.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from types import SimpleNamespace

import pytest
from kazma_core.observability import alert_catalog
from kazma_core.observability.alert_card import format_operator_card
from kazma_core.observability.alert_catalog import (
    CATALOG,
    SENDERS,
    SEVERITIES,
    SHARED_OWNERS,
    _Entry,
)

from tests import _alert_senders as senders

REPO = senders.REPO
CARDS = json.loads((REPO / "tests" / "fixtures" / "alert_card" / "cards.json").read_text(encoding="utf-8"))


def _load_guard():
    spec = importlib.util.spec_from_file_location("_alert_catalog_guard", senders.GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def _of(sender: str, catalog=CATALOG) -> list[_Entry]:
    return [e for e in catalog if e.sender == sender]


# -- the ops alerts ------------------------------------------------------------

#: Sites whose key the reader cannot read, and where its values come from.
UNREADABLE_KEYS: dict[tuple[str, str], tuple[str, str]] = {
    # The cron engine raises the alert its store queued in the change's own
    # transaction (AGENTS §16 E2): the keys are the store's "ops_key" values.
    ("kazma-core/kazma_core/cron/scheduler.py", "_raise_alert"):
        ("kazma-core/kazma_core/cron/store.py", "ops_key"),
}

#: A wildcard that stands for the keys of a table in the sender's module.
EXPANSIONS: dict[str, tuple[str, str, str]] = {
    # runs/notify.deliver: "runs.<condition>.<incident>", one per condition.
    "runs.*.*": ("kazma-core/kazma_core/runs/notify.py", "_CONDITION", "runs.{}.*"),
    # web_acquire/monitor.tick: "acquisition.<condition>", one per label.
    "acquisition.*": ("kazma-core/kazma_core/web_acquire/monitor.py", "_LABELS", "acquisition.{}"),
}


def _code_keys(sites: list[senders.AlertSite]) -> tuple[list[tuple[str, senders.AlertSite]], list[str]]:
    """(every key pattern the sites raise, with its site; the problems)."""
    keys: list[tuple[str, senders.AlertSite]] = []
    problems: list[str] = []
    for site in sites:
        if site.patterns is None:
            declared = UNREADABLE_KEYS.get((site.rel, site.function))
            if declared is None:
                problems.append(f"{site.rel}:{site.line} ({site.function}): the alert key cannot be read; "
                                "make it a literal or declare it in UNREADABLE_KEYS")
                continue
            values = senders.literal_dict_values(declared[0], declared[1])
            if not values:
                problems.append(f"{site.rel}:{site.line}: UNREADABLE_KEYS names {declared}, which holds no key")
            keys += [(v, site) for v in values]
            continue
        for pattern in site.patterns:
            if pattern in EXPANSIONS:
                rel, table, template = EXPANSIONS[pattern]
                keys += [(template.format(k), site) for k in senders.literal_dict_keys(rel, table)]
            else:
                keys.append((pattern, site))
    return keys, problems


def _missing_from_catalog(keys, catalog) -> list[str]:
    patterns = [senders.catalog_pattern(e.key) for e in _of("ops", catalog)]
    return sorted({f"{key} ({site.rel}:{site.line})" for key, site in keys
                   if not any(senders.covers(p, key) for p in patterns)})


def _not_raised(keys, catalog) -> list[str]:
    raised = [k for k, _ in keys]
    out = []
    for entry in _of("ops", catalog):
        pattern = senders.catalog_pattern(entry.key)
        if not any(senders.covers(pattern, k) or senders.covers(k, pattern) for k in raised):
            out.append(entry.key)
    return out


_PLANTED_MODULE = '''
from kazma_core.observability.ops_alerts import alert
import asyncio


def _wrapped(key, title):
    alert(key, title, "", severity="warn")


async def raise_some(name, row):
    alert("planted.never_catalogued", "x")
    await asyncio.to_thread(alert, f"planted.per_host.{name}", "x")
    _wrapped("planted.through_a_wrapper", "x")
    alert(row["key"], "x")
'''


def test_control_the_ops_reader_finds_keys_through_every_shape():
    sites = senders.scan_source(_PLANTED_MODULE, "kazma-core/kazma_core/planted.py")
    keys = sorted(p for s in sites for p in (s.patterns or ()))
    assert keys == ["planted.never_catalogued", "planted.per_host.*", "planted.through_a_wrapper"]
    assert [s.patterns for s in sites if s.patterns is None] == [None]
    found, problems = _code_keys(sites)
    assert any("cannot be read" in p for p in problems)
    missing = _missing_from_catalog(found, CATALOG)
    assert len(missing) == 3 and all(m.startswith("planted.") for m in missing)


def test_every_ops_alert_key_is_in_the_catalog():
    sites = senders.ops_alert_sites()
    assert len(sites) >= 60, len(sites)  # the reader is not blind
    keys, problems = _code_keys(sites)
    assert not problems, problems
    missing = _missing_from_catalog(keys, CATALOG)
    assert not missing, ("ops alerts with no catalog entry (add them to "
                         "kazma_core/observability/alert_catalog.py, then run "
                         "python -m kazma_core.observability.alert_catalog --write):\n  " + "\n  ".join(missing))


def test_every_declaration_still_names_a_site():
    sites = senders.ops_alert_sites()
    unreadable = {(s.rel, s.function) for s in sites if s.patterns is None}
    assert set(UNREADABLE_KEYS) <= unreadable, set(UNREADABLE_KEYS) - unreadable
    patterns = {p for s in sites for p in (s.patterns or ())}
    assert set(EXPANSIONS) <= patterns, set(EXPANSIONS) - patterns


def test_every_ops_catalog_entry_is_raised():
    keys, _ = _code_keys(senders.ops_alert_sites())
    planted = (*CATALOG, _Entry("planted.nobody_raises", "ops", "warn", "x", "س"))
    assert _not_raised(keys, planted) == ["planted.nobody_raises"]  # the control
    assert _not_raised(keys, CATALOG) == []


def _severity_mismatches(sites) -> list[str]:
    """A direct call with a literal key and severity, against its entry
    (``warning``, which ops_alerts treats as ``warn``, is ``warn``)."""
    out = []
    for site in sites:
        if not site.direct or site.severity is None or not site.patterns:
            continue
        said = {"warning": "warn"}.get(site.severity, site.severity)
        for key in site.patterns:
            entry = alert_catalog._entry_for(key)
            if entry is not None and entry.severity != said:
                out.append(f"{key}: raised {said} at {site.rel}:{site.line}, catalogued {entry.severity}")
    return out


def test_a_literal_severity_is_the_catalogs():
    planted = senders.scan_source(
        'from kazma_core.observability.ops_alerts import alert\n'
        'def f():\n    alert("backup.offsite_failed", "x", severity="info")\n', "planted.py")
    assert _severity_mismatches(planted) == [
        "backup.offsite_failed: raised info at planted.py:3, catalogued critical"]
    assert _severity_mismatches(senders.ops_alert_sites()) == []


def test_the_acquisition_entries_are_the_monitors_labels():
    from kazma_core.web_acquire import monitor

    for condition, (en, ar) in monitor._LABELS.items():
        entry = alert_catalog._entry_for(f"acquisition.{condition}")
        assert entry is not None and (entry.title, entry.title_ar) == (en, ar), condition
        assert entry.severity == monitor._ALERT_SEVERITY[monitor._severity(condition)], condition


def test_a_raised_key_finds_its_entry():
    assert alert_catalog._entry_for("llm.provider_unreachable.deepseek").key == "llm.provider_unreachable.{provider}"
    assert alert_catalog._entry_for("runs.failed.inc-1").key == "runs.failed.{incident}"
    assert alert_catalog._entry_for("database.away").key == "database.away"
    assert alert_catalog._entry_for("llm.fallback.substitution:z.ai.resolved").key == (
        "llm.fallback.substitution:{provider}.resolved")
    assert alert_catalog._entry_for("llm.fallback.substitution:z.ai").key == "llm.fallback.substitution:{provider}"
    assert alert_catalog._entry_for("planted.unknown") is None


# -- the lifecycle messages ------------------------------------------------------


def _lifecycle_problems(events: set[str], catalog) -> list[str]:
    entries = {e.key.removeprefix("lifecycle."): e for e in _of("lifecycle", catalog)}
    return ([f"lifecycle.{e} is not in the catalog" for e in sorted(events - set(entries))]
            + [f"{k} is no event of lifecycle_notifier" for k in sorted(set(entries) - events)])


def test_every_lifecycle_event_is_in_the_catalog():
    events = senders.lifecycle_events()
    assert _lifecycle_problems(events | {"planted"}, CATALOG) == ["lifecycle.planted is not in the catalog"]
    assert _lifecycle_problems(events, CATALOG) == []


def test_a_lifecycle_entry_has_the_notifiers_head_and_severity():
    from kazma_core import lifecycle_notifier

    for event, spec in lifecycle_notifier._EVENTS.items():
        entry = alert_catalog._entry_for(f"lifecycle.{event}")
        assert (entry.title, entry.severity) == (spec["label"], spec["level"]), event


# -- the guard ------------------------------------------------------------------------

#: Guard pages sent outside ``_page``, and the function that sends them.
GUARD_KINDS_OUTSIDE_PAGE = {"guard.page_command": "_cmd_page"}


def _guard_problems(pages: list[senders.GuardPage], catalog) -> list[str]:
    entries = {e.key: e for e in _of("guard", catalog)}
    problems = [f"line {p.line}: _page(...) without a literal kind=" for p in pages if not p.kinds]
    paged = {k for p in pages for k in (p.kinds or ())} | set(GUARD_KINDS_OUTSIDE_PAGE)
    problems += [f"kind {k} is not in the catalog" for k in sorted(paged - set(entries))]
    problems += [f"{k} is never paged" for k in sorted(set(entries) - paged)]
    for p in pages:
        if p.severity and p.kinds and len(p.kinds) == 1 and p.kinds[0] in entries:
            if entries[p.kinds[0]].severity != p.severity:
                problems.append(f"line {p.line}: {p.kinds[0]} paged {p.severity}, "
                                f"catalogued {entries[p.kinds[0]].severity}")
    return problems


def test_control_the_guard_reader_catches_a_page_without_a_known_kind():
    planted = senders.guard_pages(
        "class G:\n"
        "    def a(self):\n"
        "        self._page('warn', 'no kind')\n"
        "        self.guard._page('warn', 'x', kind='planted.kind')\n"
        "        self._page('info', 'x', kind='disk.low')\n")
    problems = _guard_problems(planted, CATALOG)
    assert "line 3: _page(...) without a literal kind=" in problems
    assert "kind planted.kind is not in the catalog" in problems
    assert "line 5: disk.low paged info, catalogued warn" in problems


def test_every_guard_page_names_a_kind_in_the_catalog():
    pages = senders.guard_pages()
    assert len(pages) >= 30, len(pages)
    assert _guard_problems(pages, CATALOG) == []
    source = senders.GUARD.read_text(encoding="utf-8")
    for kind, function in GUARD_KINDS_OUTSIDE_PAGE.items():
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
        assert "format_operator_card" in ast.unparse(func), (kind, function)


def test_the_guard_reads_its_copy_and_says_an_unknown_kind(monkeypatch):
    monkeypatch.setattr(guard, "_alert_catalog", None)
    monkeypatch.setattr(guard, "_UNKNOWN_KINDS_SAID", set())
    kinds = guard.guard_alert_kinds()
    assert set(kinds) == {e.key for e in _of("guard")}
    monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
    g = guard.Guard()
    sent: list[str] = []
    said: list[tuple[str, dict]] = []
    g.notify = SimpleNamespace(send=sent.append)
    g.log = lambda level, event, **kw: said.append((event, kw))
    assert g._page("warn", "Disk space low on C:", "12% free", kind="disk.low", force=True)
    assert g._page("warn", "Planted", "", kind="planted.kind", force=True)
    assert len(sent) == 2  # an unknown kind is said, and the page still goes out
    unknown = [kw for event, kw in said if event == "guard.page_kind_unknown"]
    assert [kw["kind"] for kw in unknown] == ["planted.kind"]
    # A copy that cannot be read checks nothing and pages all the same.
    monkeypatch.setattr(guard, "ALERT_CATALOG_PATH", REPO / "no-such-catalog.json")
    monkeypatch.setattr(guard, "_alert_catalog", None)
    assert guard.guard_alert_kinds() == {}
    assert g._page("warn", "x", "", kind="planted.other", force=True) and len(sent) == 3


# -- Kazma Watch --------------------------------------------------------------------------


def _watch_problems(diagnose_js: str, incidents_js: str, catalog) -> list[str]:
    found, good = senders.watch_conditions(diagnose_js)
    conditions = found - good
    entries = {e.key.removeprefix("watch."): e for e in _of("watch", catalog)}
    problems = [f"watch.{c} is not in the catalog" for c in sorted(conditions - set(entries))]
    recovery = {"recovered"} if "recovered" in senders.watch_page_kinds(incidents_js) else set()
    problems += [f"watch.{c} is never diagnosed" for c in sorted(set(entries) - conditions - recovery)]
    problems += [f"watch.{c} pages, but diagnose.js counts it as good" for c in sorted(set(entries) & good)]
    return problems


def test_every_watch_condition_is_in_the_catalog():
    diagnose_js = (senders.OBSERVER_SRC / "diagnose.js").read_text(encoding="utf-8")
    incidents_js = (senders.OBSERVER_SRC / "incidents.js").read_text(encoding="utf-8")
    planted = diagnose_js + '\nexport const X = () => ({ condition: "planted_condition" });\n'
    assert _watch_problems(planted, incidents_js, CATALOG) == ["watch.planted_condition is not in the catalog"]
    found, good = senders.watch_conditions(diagnose_js)
    assert len(found - good) >= 9 and good == {"up", "maintenance"}
    assert _watch_problems(diagnose_js, incidents_js, CATALOG) == []


# -- owners ---------------------------------------------------------------------------------


def _owner_problems(catalog, shared: dict[str, str]) -> list[str]:
    owners: dict[str, set[str]] = {}
    for e in catalog:
        if e.condition and e.owner:
            owners.setdefault(e.condition, set()).add(e.sender)
    problems = [f"{c} has {len(s)} owning senders ({', '.join(sorted(s))}); declare it in SHARED_OWNERS or "
                "mark all but one owner=False" for c, s in sorted(owners.items()) if len(s) > 1 and c not in shared]
    problems += [f"SHARED_OWNERS names {c}, which one sender owns" for c in sorted(shared)
                 if len(owners.get(c, set())) < 2]
    problems += [f"{c}: Kazma Watch shares it with an inside owner" for c in sorted(shared)
                 if "watch" in owners.get(c, set())]
    conditions = {e.condition for e in catalog if e.condition}
    problems += [f"{c} has no owner" for c in sorted(conditions - set(owners))]
    return problems


def test_no_condition_has_two_owning_senders_unless_declared():
    planted = (*CATALOG, _Entry("planted.disk", "ops", "critical", "x", "س", condition="disk_critical"))
    assert _owner_problems(planted, SHARED_OWNERS) == [
        "disk_critical has 2 owning senders (guard, ops); declare it in SHARED_OWNERS or mark all but one owner=False"]
    assert _owner_problems(CATALOG, {**SHARED_OWNERS, "kazma_restarting": "planted"}) == [
        "SHARED_OWNERS names kazma_restarting, which one sender owns"]
    outside = (*CATALOG, _Entry("watch.planted", "watch", "error", "x", "س", condition="database_away"))
    assert _owner_problems(outside, SHARED_OWNERS) == [
        "database_away: Kazma Watch shares it with an inside owner"]
    assert _owner_problems(CATALOG, SHARED_OWNERS) == []


# -- the table itself --------------------------------------------------------------------------

_KEY = re.compile(r"^[a-z_]+(\.[a-z_:{}]+)+$")
_ARABIC = re.compile(r"[\u0600-\u06ff]")


def _entry_problems(catalog) -> list[str]:
    keys = [e.key for e in catalog]
    problems = [f"{k} is in the catalog twice" for k in sorted({k for k in keys if keys.count(k) > 1})]
    by_key = {e.key: e for e in catalog}
    for e in catalog:
        if e.sender not in SENDERS:
            problems.append(f"{e.key}: unknown sender {e.sender}")
        if e.severity not in SEVERITIES:
            problems.append(f"{e.key}: unknown severity {e.severity}")
        if not _KEY.match(e.key):
            problems.append(f"{e.key}: not a dotted key")
        if not e.title.strip() or not _ARABIC.search(e.title_ar):
            problems.append(f"{e.key}: needs a title in English and in Arabic")
        if e.closed_by:
            closer = by_key.get(e.closed_by)
            if closer is None:
                problems.append(f"{e.key}: closed by {e.closed_by}, which is not in the catalog")
            elif closer.sender != e.sender:
                problems.append(f"{e.key}: closed by {e.closed_by} of another sender")
            elif closer.severity not in ("info", "success"):
                problems.append(f"{e.key}: closed by {e.closed_by}, which is not a recovery")
    return problems


def test_every_entry_is_whole():
    planted = (*CATALOG, _Entry("planted.half", "pager", "loud", "x", "x", closed_by="disk.back"))
    assert _entry_problems(planted) == [
        "planted.half: unknown sender pager", "planted.half: unknown severity loud",
        "planted.half: needs a title in English and in Arabic",
        "planted.half: closed by disk.back of another sender"]
    assert _entry_problems(CATALOG) == []


def _site_pages() -> set[str]:
    """The pages this site serves: a route that renders a template."""
    ui = REPO / "kazma-ui" / "kazma_ui"
    text = "\n".join(p.read_text(encoding="utf-8") for p in ui.rglob("*.py") if "tests" not in p.parts)
    routes = set(re.findall(r'\.get\(\s*"(/[a-z_-]*)"', text))
    return {r for r in routes if (ui / "templates" / f"{r.strip('/') or 'index'}.html").exists()} | {
        r for r in routes if r in ("/knowledge", "/x")}  # knowledge_base.html, x_studio.html


def _settings_tabs() -> set[str]:
    text = (REPO / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    return set(re.findall(r"tab === '([a-z_]+)'", text))


def _page_problems(catalog) -> list[str]:
    pages, tabs = _site_pages(), _settings_tabs()
    out = []
    for e in catalog:
        path, _, query = e.page.partition("?")
        if path not in pages:
            out.append(f"{e.key}: {e.page} is no page of this site")
        elif query and not (path == "/settings" and query.removeprefix("tab=") in tabs):
            out.append(f"{e.key}: {e.page} names no Settings tab")
    return out


def test_every_page_is_a_page_of_this_site():
    planted = (_Entry("planted.a", "ops", "warn", "x", "س", page="/nowhere"),
               _Entry("planted.b", "ops", "warn", "x", "س", page="/settings?tab=nowhere"))
    assert _page_problems(planted) == ["planted.a: /nowhere is no page of this site",
                                       "planted.b: /settings?tab=nowhere names no Settings tab"]
    assert {"/dashboard", "/settings", "/memory", "/scheduled"} <= _site_pages()
    assert _page_problems(CATALOG) == []


def test_the_committed_copies_are_the_table(tmp_path):
    assert alert_catalog._stale_copies(REPO) == [], (
        "run: python -m kazma_core.observability.alert_catalog --write")
    # Control: a copy someone edited, and one never written, are stale.
    alert_catalog._write_copies(tmp_path)
    assert alert_catalog._stale_copies(tmp_path) == []
    edited = tmp_path / alert_catalog.COPIES["guard"]
    edited.write_text(edited.read_text(encoding="utf-8").replace("Disk space is low", "Disk low"),
                      encoding="utf-8")
    (tmp_path / alert_catalog.COPIES["observer"]).unlink()
    assert sorted(alert_catalog._stale_copies(tmp_path)) == sorted(
        p.as_posix() for p in alert_catalog.COPIES.values())
    # The copies are what they say: JSON, and the observer's module holds the same JSON.
    table = json.loads(alert_catalog._read_copy(REPO, "guard"))
    module = alert_catalog._read_copy(REPO, "observer")
    body = module[module.index("export const CATALOG = ") + len("export const CATALOG = "):].rstrip().rstrip(";")
    assert json.loads(body) == table == alert_catalog._export_table()


# -- the one card --------------------------------------------------------------------------------


def _card(fn, case: dict) -> str:
    return fn(case["source"], case["severity"], case["title"], case["detail"], **case["options"])


def test_the_fixture_holds_every_form():
    forms = {c["options"].get("form", "page").strip().lower() for c in CARDS["cards"]}
    assert {"page", "reminder", "recovery"} <= forms
    sources = {c["source"].strip("[]").lower() for c in CARDS["cards"]}
    assert {"guard", "ops", "system", "watch", "cloudflare"} <= sources
    assert any("Open: " in c["text"] for c in CARDS["cards"])
    assert any("Since " in c["text"] for c in CARDS["cards"])


@pytest.mark.parametrize("copy", ["kazma_core", "guard"])
def test_the_card_matches_the_fixture(copy):
    fn = format_operator_card if copy == "kazma_core" else guard.format_operator_card
    for case in CARDS["cards"]:
        assert _card(fn, case) == case["text"], case["name"]


def test_control_a_card_without_the_new_forms_fails_the_fixture():
    def old_card(source, severity, title, detail="", **_ignored):
        return format_operator_card(source, severity, title, detail)

    failing = [c["name"] for c in CARDS["cards"] if _card(old_card, c) != c["text"]]
    assert {"reminder", "recovery", "watch: the plan's example"} <= set(failing)


def test_a_page_with_none_of_the_new_options_is_the_old_card():
    # Byte for byte what format_operator_card made before the forms existed.
    assert format_operator_card("guard", "info", "Reload", "Back soon") == "\U0001f7e0 [Guard] Reload\nBack soon"
    assert format_operator_card("ops", "warn", "MCP down", "") == "\U0001f7e1 [Ops] MCP down"
    assert guard.format_operator_card("guard", "", "", "") == "\U0001f7e1 [Guard] Warn"
