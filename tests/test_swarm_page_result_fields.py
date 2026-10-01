"""The Swarm page reads only fields a task's result has (2026-10-02).

``task_completed`` carries ``TaskResult.to_dict()``. The pipeline view and
Play mode showed the finished task's answer from ``result.synthesis`` or
``result.response``, which no result has ever carried, so both always said
"no synthesis" over a task that had one (``synthesized_output``).

Every field the page reads off a result -- ``x.result.field``, or ``field``
on a name assigned ``x.result`` (``|| {}``) read within the block that
assigned it -- must be a ``TaskResult`` field. A field the page writes onto
the object for itself (``data.result._prompt = ...``) is not a read.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from kazma_core.swarm.task import TaskResult
from tests.test_scripts_have_no_english import Tok, lex, matching

ROOT = Path(__file__).resolve().parents[1]
SWARM_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "swarm.js"


def _text(toks: list[Tok], k: int) -> str:
    return toks[k].text if 0 <= k < len(toks) else ""


def _assigned(toks: list[Tok], k: int) -> bool:
    """``toks[k]`` is a plain ``=`` (not ``==``/``===``/``=>``)."""
    return _text(toks, k) == "=" and _text(toks, k + 1) not in ("=", ">")


def _member(toks: list[Tok], k: int, name: str) -> Tok | None:
    """The field read as ``name.field`` at ``toks[k]``, or None."""
    if (
        toks[k].kind == "id" and toks[k].text == name
        and _text(toks, k + 1) == "."
        and k + 2 < len(toks) and toks[k + 2].kind == "id"
        and not _assigned(toks, k + 3)
        and _text(toks, k - 1) != "."
    ):
        return toks[k + 2]
    return None


def _enclosing_block(toks: list[Tok], k: int) -> tuple[int, int]:
    """The ``{ ... }`` around ``toks[k]``, or the whole script."""
    depth = 0
    for j in range(k - 1, -1, -1):
        if toks[j].kind != "punct":
            continue
        if toks[j].text == "}":
            depth += 1
        elif toks[j].text == "{":
            if depth == 0:
                return j, matching(toks, j)
            depth -= 1
    return 0, len(toks)


def result_fields_read(src: str) -> list[tuple[int, str]]:
    """``(line, field)`` for every field the script reads off a task result."""
    toks = lex(src)
    found: list[tuple[int, str]] = []
    for i in range(len(toks)):
        # x.result.field
        if toks[i].kind == "id" and toks[i].text == "result" and _text(toks, i - 1) == ".":
            field = _member(toks, i, "result")
            if field is not None:
                found.append((field.line, field.text))
        # var alias = x.result [|| {}] ;  -- then alias.field in the same block
        if toks[i].kind == "id" and toks[i].text in ("var", "let", "const"):
            name, j = _text(toks, i + 1), i + 3
            if not (_text(toks, i + 2) == "=" and toks[j].kind == "id"
                    and _text(toks, j + 1) == "." and _text(toks, j + 2) == "result"):
                continue
            k = j + 3
            if [_text(toks, k + n) for n in range(4)] == ["|", "|", "{", "}"]:
                k += 4
            if _text(toks, k) not in (";", ","):
                continue
            _start, end = _enclosing_block(toks, i)
            for m in range(k, min(end, len(toks))):
                field = _member(toks, m, name)
                if field is not None:
                    found.append((field.line, field.text))
    return found


def _unknown(src: str) -> list[str]:
    fields = {f.name for f in dataclasses.fields(TaskResult)}
    return [f"line {n}: result.{field}" for n, field in result_fields_read(src) if field not in fields]


def test_the_swarm_page_reads_only_real_result_fields():
    src = SWARM_JS.read_text(encoding="utf-8")
    assert len(result_fields_read(src)) >= 4, "the scan found no result reads: the page changed shape"
    problems = _unknown(src)
    assert not problems, (
        "The Swarm page reads a field no TaskResult carries (it is always "
        "undefined):\n  " + "\n  ".join(problems)
    )


def test_a_field_no_result_carries_is_caught():
    """Negative control: the pipeline view's old line; a write and a fetch's res pass."""
    planted = (
        "function onDone(d) {\n"
        "  var res = d.result || {};\n"
        "  var summary = res.synthesis || res.response || t('swarm.no_synthesis');\n"
        "  var ok = data.result.status;\n"
        "  data.result._prompt = 'x';\n"
        "}\n"
        "function load() { fetch(u).then(function (res) { return res.ok && res.json(); }); }\n"
    )
    assert _unknown(planted) == ["line 3: result.synthesis", "line 3: result.response"]
