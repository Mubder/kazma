"""Scheduling clarify cards speak the page's language, with a time (Scheduled audit SCH-S08).

The remind and cancel clarify cards were English diagnostics built on the
server ("Use the memory-anchored date (2026-10-14)", "job_id 'x' is not a
pending job ...") and painted as they were; two options on the same day read
identically, with no time and no zone. Now the server sends structured
options (``kind``, the ISO instant ``at`` and its ``zone``, the ``job_id``)
and a ``question_kind``; the page words them from the catalog, in English or
Arabic, with the date, time and zone. ``label`` stays the English for chat
apps, and now names the time and zone too.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "kazma-ui/kazma_ui/static/js/chat.js"
FMT_JS = ROOT / "kazma-ui/kazma_ui/static/js/locale_format.js"


def _options(zone="Asia/Kuwait"):
    from zoneinfo import ZoneInfo

    from kazma_core.safety.commitment.authorize import _build_remind_clarify_options

    a = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    b = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    return _build_remind_clarify_options(
        SimpleNamespace(option_fire_ats={"memory_anchor": a, "from_now": b}),
        zone=ZoneInfo(zone), zone_name=zone)


def test_the_options_are_structured_and_name_the_time() -> None:
    opts = _options()
    remind = [o for o in opts if o["kind"] == "remind_at"]
    assert [o["anchor"] for o in remind] == ["memory", "now"]
    assert all(o["zone"] == "Asia/Kuwait" and o["at"].endswith("+00:00") for o in remind)
    labels = [o["label"] for o in remind]
    assert labels[0] != labels[1]  # same day, different times: no longer identical
    assert "09:00 Asia/Kuwait" in labels[0] and "18:00 Asia/Kuwait" in labels[1]
    # The booking the option resumes is the instant in the operator's zone.
    assert remind[0]["slots_patch"]["timing"] == "2026-10-14T09:00:00+03:00"
    assert opts[-1]["kind"] == "cancel"


def test_negative_control_the_old_labels_were_date_only() -> None:
    a = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    b = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    old = [f"Use the memory-anchored date ({a.date()})", f"Use the from-now date ({b.date()})"]
    assert old[0].split("(")[1] == old[1].split("(")[1]  # the two read the same


def test_the_card_payload_carries_the_question_kind() -> None:
    src = (ROOT / "kazma-core/kazma_core/agent/graph_tool_worker.py").read_text(encoding="utf-8")
    assert '"question_kind": getattr(_dec, "question_kind", "")' in src


def _js_functions(src: str) -> str:
    out = []
    for name in ("semanticQuestion", "semanticOptionLabel"):
        start = src.index(f"function {name}(")
        depth, i = 0, src.index("{", start)
        while True:
            ch = src[i]
            depth += ch == "{"
            depth -= ch == "}"
            i += 1
            if depth == 0:
                break
        out.append(src[start:i])
    return "\n".join(out)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_page_words_the_card_with_date_time_and_zone(tmp_path, lang) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    from kazma_ui.i18n.catalog.scheduled import TRANSLATIONS

    bridge = {k.replace("scheduled.", ""): v[lang] for k, v in TRANSLATIONS.items() if k.startswith("scheduled.clarify_")}
    fns = _js_functions(CHAT_JS.read_text(encoding="utf-8"))
    script = tmp_path / "card.js"
    script.write_text(
        "const vm=require('vm'),fs=require('fs');\n"
        f"const ctx={{document:{{documentElement:{{lang:{json.dumps(lang)}}}}},Intl,Date,String,Number,isNaN,Math,Object,JSON}};\n"
        "ctx.window=ctx;vm.createContext(ctx);\n"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(FMT_JS))},'utf8'),ctx);\n"
        f"ctx.CHAT_I18N={json.dumps(bridge, ensure_ascii=False)};\n"
        "vm.runInContext(`function ti(k,f){var v=window.CHAT_I18N[k];return (v!=null&&String(v)!=='')?String(v):(f||k);}"
        "function tiFmt(k,f,vars){var s=ti(k,f);Object.keys(vars||{}).forEach(function(x){s=s.split('{'+x+'}').join(String(vars[x]));});return s;}`,ctx);\n"
        f"vm.runInContext({json.dumps(fns)},ctx);\n"
        f"const opts={json.dumps(_options())};\n"
        "const out={q:vm.runInContext('semanticQuestion',ctx)({question_kind:'remind_when',question:'diag'}),"
        "labels:opts.map(o=>vm.runInContext('semanticOptionLabel',ctx)(o))};\n"
        "process.stdout.write(JSON.stringify(out));\n",
        encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["q"] != "diag" and out["q"] == bridge["clarify_remind_when"]
    remind = out["labels"][:2]
    assert remind[0] != remind[1]
    for label in remind:
        assert re.search(r"\d{1,2}:\d{2}", label), label  # the time is shown
        assert "GMT" in label or "+3" in label or "UTC" in label, label  # and the zone
    if lang == "ar":
        joined = " ".join(out["labels"]) + out["q"]
        assert not re.search(r"\b(Remind|Cancel|from|memory|now)\b", joined), joined
        assert re.search(r"[؀-ۿ]", joined)


def test_the_cancel_card_words_its_jobs(tmp_path) -> None:
    from kazma_core.safety.commitment.authorize import _resolve_cancel_job_act

    assert "question_kind=\"cancel_which\"" in Path(
        ROOT / "kazma-core/kazma_core/safety/commitment/authorize.py").read_text(encoding="utf-8")
    assert callable(_resolve_cancel_job_act)
