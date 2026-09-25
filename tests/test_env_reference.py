"""The environment-variable index is generated, current, and only gets better.

The curated ``environment-variables.md`` explains what each variable is for,
and it drifts: the 2026-09-16 audit counted 272 ``KAZMA_*`` variables read by
the code against 43 documented. ``scripts/generate_env_reference.py`` writes
``environment-variables-index.md`` from the code itself, so the inventory
cannot drift; these tests keep it regenerated and hold the number of variables
the curated page does not describe on a ratchet.
"""

from __future__ import annotations

import functools
import importlib.util
import re
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Variables read by the code that environment-variables.md does not describe.
#: Zero since 2026-09-25, when every one of the 148 was read at its call site
#: and written up. Never raise it to make room for a new variable -- describe
#: the new one instead (and if it turns a protection off, put it in the
#: security table: tests/test_static_gates.py::SECURITY_ENV_NAMES).
UNDOCUMENTED_BASELINE = 0


@functools.lru_cache(maxsize=1)
def _generator():
    name = "_kazma_generate_env_reference"
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / "generate_env_reference.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # @dataclass resolves its module through sys.modules while the class is
    # being built, so the module must be registered before it executes.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@functools.lru_cache(maxsize=1)
def _built():
    """One scan of the product tree, shared by the tests below."""
    return _generator().build(REPO)


def test_the_env_index_is_current():
    gen = _generator()
    text, found, _missing = _built()
    on_disk = gen.OUT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == text, (
        "docs/docs/reference/environment-variables-index.md is stale -- a KAZMA_* "
        "variable was added, removed or moved. Run: python scripts/generate_env_reference.py"
    )
    assert len(found) > 200, "the scanner stopped finding variables; the gate would be decoration"


def test_undocumented_variables_only_go_down():
    _text, _found, missing = _built()
    assert len(missing) <= UNDOCUMENTED_BASELINE, (
        f"{len(missing)} KAZMA_* variables are read by the code but not described in "
        f"docs/docs/reference/environment-variables.md (baseline {UNDOCUMENTED_BASELINE}). "
        "Describe the new one there -- what it turns on or OFF, and when it is safe to set."
    )
    assert len(missing) >= UNDOCUMENTED_BASELINE, (
        f"Only {len(missing)} undocumented now -- lower UNDOCUMENTED_BASELINE to "
        f"{len(missing)} in this file to lock the gain in."
    )


def test_the_scanner_finds_reads_and_skips_what_is_not_a_variable():
    """Negative control: a new read is found; __all__ and Python names are not."""
    gen = _generator()
    planted = {
        "kazma-core/kazma_core/planted.py": textwrap.dedent(
            '''
            import os
            __all__ = ["KAZMA_EXPORTED_CONSTANT"]
            KAZMA_EXPORTED_CONSTANT = ("a", "b")
            FLAG = os.environ.get("KAZMA_BRAND_NEW_SWITCH", "1")
            PORT = int(os.getenv("KAZMA_BRAND_NEW_PORT") or 0)
            for _name in ("KAZMA_BRAND_NEW_ALIAS",):
                os.environ.get(_name)
            '''
        )
    }
    found = gen.collect(planted)
    assert set(found) == {"KAZMA_BRAND_NEW_SWITCH", "KAZMA_BRAND_NEW_PORT", "KAZMA_BRAND_NEW_ALIAS"}
    assert found["KAZMA_BRAND_NEW_SWITCH"].defaults == {'"1"'}
    assert found["KAZMA_BRAND_NEW_SWITCH"].modules == {"kazma_core.planted"}
    assert gen.undocumented(found, "`KAZMA_BRAND_NEW_PORT` | 0 | the port |") == [
        "KAZMA_BRAND_NEW_ALIAS", "KAZMA_BRAND_NEW_SWITCH",
    ]
    # A prefix is not a mention: KAZMA_BRAND_NEW is not KAZMA_BRAND_NEW_PORT.
    assert gen.undocumented({"KAZMA_BRAND_NEW": found["KAZMA_BRAND_NEW_PORT"]},
                            "KAZMA_BRAND_NEW_PORT") == ["KAZMA_BRAND_NEW"]


def test_the_index_is_in_the_docs_sidebar():
    sidebars = (REPO / "docs" / "sidebars.js").read_text(encoding="utf-8")
    assert "'reference/environment-variables-index'" in sidebars


# ── A documented default is the code's default ────────────────────────
#
# The curated page said KAZMA_TOOL_RESULT_MAX_CHARS was 4000 and the research
# cap 16000; the code had 100000 and 200000. It also said the canonical HITL
# floor was off unless set, when it had been on by default since 2026-09-16.
# A description can only be checked by reading it, but a stated default can be
# checked against the literal the code falls back to.

_DOCUMENTED_DEFAULT = re.compile(
    r"^\|\s*`(KAZMA_[A-Z0-9_]+)`\s*\|\s*`([^`|]+)`[^|]*\|"
)


def documented_defaults(curated: str) -> dict[str, str]:
    """Variable -> default, for rows whose default cell starts with one literal."""
    out: dict[str, str] = {}
    for line in curated.splitlines():
        m = _DOCUMENTED_DEFAULT.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def default_drift(found: dict, curated: str) -> list[str]:
    """Rows whose stated default is not the literal the code falls back to.

    Only variables whose code states literal defaults are checked, and an
    empty-string default is skipped: it means "unset", and the effective
    default then comes from somewhere else (``KAZMA_TZ`` falls back to UTC).
    """
    drift = []
    for name, doc in documented_defaults(curated).items():
        var = found.get(name)
        if var is None:
            continue
        code = {d.strip('"') for d in var.defaults} - {""}
        if code and doc not in code:
            drift.append(f"{name}: page says {doc!r}, code falls back to {sorted(code)}")
    return drift


def test_documented_defaults_match_the_code():
    _text, found, _missing = _built()
    curated = _generator().CURATED.read_text(encoding="utf-8")
    drift = default_drift(found, curated)
    assert not drift, (
        "docs/docs/reference/environment-variables.md states defaults the code "
        "does not use — fix the page (or the code, if the page was the intent):\n  "
        + "\n  ".join(drift)
    )


def test_the_default_check_sees_a_wrong_default():
    """Negative control."""
    gen = _generator()
    found = gen.collect({
        "kazma-core/kazma_core/planted.py":
            'import os\nCAP = os.environ.get("KAZMA_PLANTED_CAP", "100000")\n'
            'TZ = os.environ.get("KAZMA_PLANTED_TZ", "")\n',
    })
    page = (
        "| `KAZMA_PLANTED_CAP` | `4000` | stale |\n"
        "| `KAZMA_PLANTED_TZ` | `UTC` | effective default, not the literal |\n"
    )
    assert default_drift(found, page) == [
        "KAZMA_PLANTED_CAP: page says '4000', code falls back to ['100000']"
    ]
    assert default_drift(found, page.replace("`4000`", "`100000`")) == []
