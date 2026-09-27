"""The metrics generator: what the website reads keeps its shape and its facts.

``scripts/generate_metrics.py`` writes METRICS.md (for people) and, since
2026-09-27, ``metrics.json`` -- the file the kazma.ai build reads. The site
used to parse METRICS.md's tables by row label, so any label edit here
("Source (7 packages)" of a six-package repository, a bold marker, a thousands
separator) silently changed the site. The JSON layout is frozen below; a
change to it is a change to the website's input and must come with a new
``SCHEMA_VERSION`` (docs/docs/ops/website-metrics.md).

Every gate here has a negative control.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "generate_metrics_mod", REPO / "scripts" / "generate_metrics.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


gm = _load()

AREAS = ("source", "tests", "examples", "archive", "scripts", "root")

#: metrics.json schema_version 1, as the website reads it. Changing this is
#: changing the website's input: bump SCHEMA_VERSION, freeze the new layout
#: beside this one, and update docs/docs/ops/website-metrics.md.
LAYOUT_V1 = {
    "schema_version": "int",
    "generated_on": "str",
    "repository": "str",
    "commit": {"sha": "str", "short": "str", "subject": "str", "date": "str"},
    "python": {
        "files": "int",
        "lines": "int",
        "code_lines": "int",
        "blank_lines": "int",
        "comment_lines": "int",
    },
    "areas": {name: {"files": "int", "lines": "int", "code_lines": "int"} for name in AREAS},
    "packages": [{"name": "str", "files": "int", "lines": "int", "code_lines": "int"}],
    "tests": {
        "files": "int",
        "collected": "int|null",
        "static_functions": "int",
        "def_functions": "int",
        "async_def_functions": "int",
        "classes": "int",
        "lines": "int",
    },
    "source_structure": {"functions": "int", "async_functions": "int", "classes": "int"},
    "assets": {
        name: "int"
        for name in (
            "javascript_files",
            "javascript_lines",
            "html_files",
            "yaml_files",
            "markdown_files",
            "tsx_files",
            "css_files",
            "json_files",
            "svg_files",
        )
    },
    "git": {"commits": "int", "contributors": "int", "branches": "int", "tags": "int"},
    "largest_python_files": [{"path": "str", "lines": "int"}],
    "versions": {"pyproject": "str|null", "kazma_yaml": "str|null", "cli": "str|null"},
}


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def layout_problems(layout, value, where: str = "$") -> list[str]:
    """Where *value* departs from *layout*: keys added or missing, wrong types."""
    if isinstance(layout, dict):
        if not isinstance(value, dict):
            return [f"{where}: expected an object, got {_type_name(value)}"]
        problems = [f"{where}.{key}: missing" for key in layout if key not in value]
        problems += [f"{where}.{key}: not in the layout" for key in value if key not in layout]
        for key in layout:
            if key in value:
                problems += layout_problems(layout[key], value[key], f"{where}.{key}")
        return problems
    if isinstance(layout, list):
        if not isinstance(value, list) or not value:
            return [f"{where}: expected a non-empty list"]
        problems: list[str] = []
        for i, item in enumerate(value):
            problems += layout_problems(layout[0], item, f"{where}[{i}]")
        return problems
    if _type_name(value) not in layout.split("|"):
        return [f"{where}: expected {layout}, got {_type_name(value)}"]
    return []


def _sample(collected: int = 11_376) -> dict:
    """What collect() returns, with made-up figures."""
    return {
        "python": {
            "files": 1683,
            "total": 510_972,
            "blank": 79_696,
            "comment": 26_307,
            "code": 431_276,
            "pure_code": 404_969,
        },
        "areas": {
            name: {"files": files, "total": total, "code": code}
            for name, files, total, code in (
                ("source", 853, 301_404, 244_787),
                ("tests", 794, 192_530, 145_967),
                ("examples", 32, 7_597, 6_258),
                ("archive", 0, 0, 0),
                ("scripts", 41, 14_033, 11_316),
                ("root", 2, 757, 435),
            )
        },
        "packages": {
            name: {"files": 40 + i, "total": 10_000 * (i + 1), "code": 8_000 * (i + 1)}
            for i, name in enumerate(gm.PACKAGES)
        },
        "tests": {
            "files": 794,
            "test_def": 7_525,
            "test_async_def": 1_896,
            "test_class": 1_226,
            "test_functions_total": 9_421,
            "collected": collected,
            "collect_problem": "" if collected else "pytest --collect-only exited 4 with no count",
        },
        "source_structure": {"def": 7_489, "async_def": 2_099, "class": 996},
        "assets": {
            "js": 52,
            "tsx": 5,
            "css": 5,
            "html": 25,
            "yaml": 41,
            "json": 53,
            "md": 359,
            "svg": 4,
            "js_loc": 40_299,
        },
        "git": {"commits": 4_035, "branches": 2, "tags": 18, "contributors": 9},
        "largest_files": [
            ("kazma-ui/kazma_ui/routes/ws_chat.py", 2_910),
            ("kazma-ui/kazma_ui/app.py", 2_781),
        ],
        "versions": {"pyproject": "0.11.0", "kazma_yaml": "0.11.0", "cli": "n/a"},
        "commit": {
            "sha": "92b0b15a" + "0" * 32,
            "short": "92b0b15a",
            "subject": "chore(ci): main is protected",
            "date": "2026-09-27",
        },
        "head": "92b0b15a chore(ci): main is protected (2026-09-27)",
        "generated": "2026-09-27",
    }


# ── The layout the website reads ────────────────────────────────────────────


def test_metrics_json_keeps_layout_v1():
    assert gm.SCHEMA_VERSION == 1, (
        "SCHEMA_VERSION moved: freeze the new layout here beside LAYOUT_V1 and "
        "update docs/docs/ops/website-metrics.md -- the website refuses an "
        "unknown version"
    )
    for collected in (11_376, 0):
        data = gm.to_json(_sample(collected))
        assert layout_problems(LAYOUT_V1, data) == []
        assert data["schema_version"] == gm.SCHEMA_VERSION
    assert gm.to_json(_sample(0))["tests"]["collected"] is None


def test_the_real_repository_fills_every_key(monkeypatch):
    """The layout holds for what collect() actually returns, not only for the
    sample (a key the sample has and collect() lacks would pass above)."""
    monkeypatch.setattr(gm, "_count_contributors_via_api", lambda: 7)
    measured = gm.collect(runtime_tests=False)
    assert layout_problems(LAYOUT_V1, gm.to_json(measured)) == []
    assert "Collected at runtime | n/a" in gm.render(measured)


def test_the_layout_check_sees_a_renamed_key_and_a_wrong_type():
    """Negative control: the comparison above is not vacuous."""
    data = gm.to_json(_sample())
    data["tests"]["collected_count"] = data["tests"].pop("collected")
    data["git"]["commits"] = "4,035"
    problems = layout_problems(LAYOUT_V1, data)
    assert "$.tests.collected: missing" in problems
    assert "$.tests.collected_count: not in the layout" in problems
    assert "$.git.commits: expected int, got str" in problems


# ── The two files state the same facts ─────────────────────────────────────


def _table(md: str, heading: str) -> dict[str, list[str]]:
    """Rows of the table under ``## heading``: label -> the other cells."""
    section = md.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|") or re.fullmatch(r"\|[\s:|-]+", line):
            continue
        cells = [c.strip().replace("**", "").replace("`", "") for c in line.strip("|").split("|")]
        rows[cells[0]] = cells[1:]
    return rows


def _number(cell: str) -> int:
    return int(re.match(r"[\d,]+", cell).group(0).replace(",", ""))


def disagreements(md: str, data: dict) -> list[str]:
    """The figures METRICS.md states differently from metrics.json."""
    stated = {
        ("Python — headline", "Total .py files"): data["python"]["files"],
        ("Python — headline", "Total lines"): data["python"]["lines"],
        ("Python — headline", "Pure code lines"): data["python"]["code_lines"],
        ("Tests", "Test files"): data["tests"]["files"],
        ("Tests", "Total test functions"): data["tests"]["static_functions"],
        ("Tests", "Test LOC"): data["tests"]["lines"],
        ("Git history", "Commits"): data["git"]["commits"],
        ("Git history", "Contributors"): data["git"]["contributors"],
        ("By area", f"Source ({len(data['packages'])} packages)"): data["areas"]["source"]["files"],
    }
    if data["tests"]["collected"] is not None:
        stated[("Tests", "Collected at runtime")] = data["tests"]["collected"]
    for pkg in data["packages"]:
        stated[("LOC per package", pkg["name"])] = pkg["lines"]
    out = []
    for (heading, label), want in stated.items():
        row = _table(md, heading).get(label)
        cell = row[1] if row and heading in ("LOC per package",) else (row[0] if row else None)
        if cell is None:
            out.append(f"{heading} / {label}: no such row")
        elif _number(cell) != want:
            out.append(f"{heading} / {label}: METRICS.md says {cell}, metrics.json {want:,}")
    return out


def test_json_and_markdown_state_the_same_figures():
    m = _sample()
    assert disagreements(gm.render(m), gm.to_json(m)) == []


def test_the_agreement_check_sees_a_different_figure():
    """Negative control: a table that says something else is caught."""
    m = _sample()
    md = gm.render(m).replace("| Commits | **4,035** |", "| Commits | **4,036** |")
    assert disagreements(md, gm.to_json(m)) == [
        "Git history / Commits: METRICS.md says 4,036, metrics.json 4,035"
    ]


# ── The package list names real packages ───────────────────────────────────


def untracked_packages(names: list[str]) -> list[str]:
    """The names with no git-tracked file under a folder of that name."""
    return [
        name
        for name in names
        if not subprocess.run(
            ["git", "ls-files", "--", f"{name}/"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
    ]


def test_every_listed_package_is_a_folder_in_the_repository():
    assert untracked_packages(gm.PACKAGES) == [], (
        "a package in generate_metrics.PACKAGES has no files: the metrics "
        "would count it in 'Source (N packages)'"
    )


def test_the_package_check_sees_a_retired_package():
    """Negative control: kazma-memory, retired, stayed on the list until
    2026-09-27 and made the site say 7 packages."""
    assert untracked_packages(["kazma-core", "kazma-memory"]) == ["kazma-memory"]


# ── A run without the test count says why, and can refuse ──────────────────


def _fake_subprocess(run):
    return types.SimpleNamespace(
        run=run,
        TimeoutExpired=subprocess.TimeoutExpired,
        SubprocessError=subprocess.SubprocessError,
    )


def test_a_collection_reports_its_count(monkeypatch):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "tests/test_x.py::t\n\n11376 tests collected in 3.22s\n", "")

    monkeypatch.setattr(gm, "subprocess", _fake_subprocess(run))
    assert gm.count_collected_tests() == (11_376, "")


def test_a_failed_collection_says_why(monkeypatch):
    """The workflow's job without the project installed got 0 for two months
    and nothing said why."""

    def run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 4, "", "ImportError while loading conftest\nE   ModuleNotFoundError: No module named 'langgraph'\n"
        )

    monkeypatch.setattr(gm, "subprocess", _fake_subprocess(run))
    count, problem = gm.count_collected_tests()
    assert count == 0
    assert "exited 4" in problem and "No module named 'langgraph'" in problem

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(gm, "subprocess", _fake_subprocess(hang))
    assert "did not finish" in gm.count_collected_tests()[1]


def test_require_collected_refuses_to_publish_na(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gm, "collect", lambda runtime_tests=True: _sample(collected=0))
    out = tmp_path / "out"
    assert gm.main(["--out-dir", str(out), "--require-collected"]) == 1
    assert "exited 4" in capsys.readouterr().err
    assert not out.exists()

    # Without the flag the files are written, with n/a and a warning.
    assert gm.main(["--out-dir", str(out)]) == 0
    assert "will read n/a" in capsys.readouterr().err
    assert "| Collected at runtime | n/a |" in (out / "METRICS.md").read_text(encoding="utf-8")
    assert json.loads((out / "metrics.json").read_text(encoding="utf-8"))["tests"]["collected"] is None


def test_out_dir_leaves_the_repository_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(gm, "collect", lambda runtime_tests=True: _sample())
    monkeypatch.setattr(gm, "METRICS_FILE", tmp_path / "repo" / "METRICS.md")
    monkeypatch.setattr(gm, "README_FILE", tmp_path / "repo" / "README.md")
    out = tmp_path / "out"
    assert gm.main(["--out-dir", str(out)]) == 0
    assert not (tmp_path / "repo").exists()
    for name in ("METRICS.md", "metrics.json"):
        raw = (out / name).read_bytes()
        assert raw and b"\r\n" not in raw, f"{name}: the website's copy is LF on every OS"
    assert json.loads((out / "metrics.json").read_text(encoding="utf-8"))["commit"]["short"] == "92b0b15a"


def test_the_readme_gate_runs_no_pytest(monkeypatch):
    """--check-readme compares static counts; it skips the collection."""
    asked: list[bool] = []

    def collect(runtime_tests=True):
        asked.append(runtime_tests)
        return _sample(collected=0)

    monkeypatch.setattr(gm, "collect", collect)
    monkeypatch.setattr(gm, "check_readme", lambda m, text: [])
    assert gm.main(["--check-readme"]) == 0
    assert asked == [False]
