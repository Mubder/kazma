"""The Scheduled page's statuses, labels and dates (Scheduled audit SCH-S04, S12).

* One map from every status -- each cron job status and each X publication
  state -- to a page class (upcoming, needs attention, done, failed,
  cancelled), held equal in Python (the API) and in the page's script; an
  unknown status needs attention, never "Completed".
* Every status has a label in English and Arabic: the page showed raw ids
  ("managed", "outcome_unknown") in both languages.
* Dates are formatted through ``window.KazmaFormat`` only (SCH-S12): a gate
  refuses ``new Intl.DateTimeFormat`` / ``toLocale*String`` outside
  locale_format.js, with the scripts that still do it named as debt that may
  only shrink, and a planted call as its negative control.
(The page's behaviour runs in tests/js/test_scheduled_page.js.)
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "kazma-ui/kazma_ui/static/js"
_X_STATES = {"scheduled", "deferred", "sending", "outcome_unknown", "published", "failed_permanent",
             "cancelled", "awaiting_approval", "expired"}


def _js_map() -> dict[str, str]:
    src = (JS_DIR / "scheduled.js").read_text(encoding="utf-8")
    body = src.split("const STATUS_CLASS = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r"^\s*([a-z_]+):\s*'([a-z]+)'", body, re.M))


def test_one_map_in_the_api_and_the_page() -> None:
    from kazma_ui.scheduled_api import SCHEDULED_STATUS_CLASS

    assert _js_map() == SCHEDULED_STATUS_CLASS


def test_every_status_is_mapped() -> None:
    from kazma_core.cron.store import JobStatus

    from kazma_ui.scheduled_api import SCHEDULED_STATUS_CLASS

    statuses = {s.value for s in JobStatus} | _X_STATES
    assert statuses <= set(SCHEDULED_STATUS_CLASS), statuses - set(SCHEDULED_STATUS_CLASS)
    assert set(SCHEDULED_STATUS_CLASS.values()) <= {"upcoming", "attention", "done", "failed", "cancelled"}


def test_the_x_states_are_the_stores() -> None:
    """The X states named here are the ones the publication store writes."""
    src = (ROOT / "kazma-core/kazma_core/x_api/publication_store.py").read_text(encoding="utf-8")
    written = set(re.findall(r"\bstate = '([a-z_]+)'", src))
    for group in re.findall(r"\bstate IN \(([^)]*)\)", src):
        written |= set(re.findall(r"'([a-z_]+)'", group))
    assert written and written <= _X_STATES, written - _X_STATES


def test_every_status_has_a_label_in_both_languages() -> None:
    from kazma_ui.i18n.catalog.scheduled import TRANSLATIONS

    from kazma_ui.scheduled_api import SCHEDULED_STATUS_CLASS

    missing = [s for s in SCHEDULED_STATUS_CLASS
               if not (TRANSLATIONS.get(f"scheduled.st_{s}", {}).get("en")
                       and TRANSLATIONS.get(f"scheduled.st_{s}", {}).get("ar"))]
    assert missing == []
    assert TRANSLATIONS["scheduled.st_attention"]["ar"]


def test_negative_control_the_old_catalog_lacked_labels() -> None:
    old = {"scheduled.st_pending", "scheduled.st_running", "scheduled.st_done", "scheduled.st_failed",
           "scheduled.st_cancelled", "scheduled.st_fired"}
    assert {"managed", "awaiting_approval", "outcome_unknown"} - {k.split("st_")[1] for k in old}


# ── dates through KazmaFormat ────────────────────────────────────────────

_DIRECT = re.compile(r"new Intl\.DateTimeFormat\(|new Intl\.RelativeTimeFormat\(|"
                     r"\.toLocale(?:String|DateString|TimeString)\(")
#: Scripts outside the Scheduled area that still format dates themselves:
#: debt that may only go down (count per file). The Scheduled page and X
#: Studio are held at zero.
_DEBT = {
    "agents.js": 1, "chat.js": 4, "dash_lists.js": 1, "dashboard.js": 1, "memory.js": 1,
    "memory_console.js": 3, "research.js": 1, "settings_agent.js": 3, "settings_ops.js": 1,
    "streaming.js": 1, "modules/util.js": 1,
}
_HELD_AT_ZERO = ("scheduled.js", "x_studio.js")


def _direct_calls(text: str) -> int:
    return len(_DIRECT.findall(text))


def _scripts() -> dict[str, int]:
    out = {}
    for path in JS_DIR.rglob("*.js"):
        rel = path.relative_to(JS_DIR).as_posix()
        if rel in ("locale_format.js",) or rel.endswith(".min.js") or "vendor" in rel:
            continue
        n = _direct_calls(path.read_text(encoding="utf-8", errors="replace"))
        if n:
            out[rel] = n
    return out


def test_dates_go_through_kazmaformat() -> None:
    found = _scripts()
    for name in _HELD_AT_ZERO:
        assert name not in found, f"{name} formats dates itself: use window.KazmaFormat"
    grown = {name: n for name, n in found.items() if n > _DEBT.get(name, 0)}
    assert grown == {}, f"new direct date formatting (use window.KazmaFormat): {grown}"


def test_negative_control_the_gate_sees_a_planted_call() -> None:
    planted = "const s = new Date(x).toLocaleString(lang, { month: 'short' });"
    assert _direct_calls(planted) == 1
    old_page = "return new Intl.DateTimeFormat(this._locale(), { dateStyle: 'medium' }).format(d);"
    assert _direct_calls(old_page) == 1
    # The viewer's zone query is not formatting, and is allowed.
    assert _direct_calls("Intl.DateTimeFormat().resolvedOptions().timeZone") == 0


# ── SCH-S09 (menu half): /x says what /x does ──────────────────────────────


def _promises_scheduling(description: str) -> bool:
    low = description.lower()
    return "schedul" in low or "مجدول" in description


def test_the_x_menu_promises_only_what_x_does() -> None:
    """The "/" menu said "/x: ... scheduled posts", but /x in a chat app is the
    auto-reply loop and its help names no scheduling; posts are booked with
    the x_schedule_post tool or in X Studio."""
    from kazma_core.agent.command_catalog import BY_NAME

    x = BY_NAME["x"]
    assert not _promises_scheduling(x.description), x.description
    assert not _promises_scheduling(x.description_ar), x.description_ar
    cmds = (ROOT / "kazma-gateway/kazma_gateway/agent_handler/commands.py").read_text(encoding="utf-8")
    start = cmds.index('"𝕏 *Auto-reply*')
    assert "schedul" not in cmds[start:start + 900].lower()  # the help agrees


def test_negative_control_the_old_menu_promised_scheduled_posts() -> None:
    assert _promises_scheduling("X Studio: drafts, accounts and scheduled posts (/x help)")
    assert _promises_scheduling("استوديو X: المسودات والحسابات والمنشورات المجدولة")
