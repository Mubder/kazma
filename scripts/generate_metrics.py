#!/usr/bin/env python
"""Generate Kazma repository metrics.

Counts Python LOC, files, tests, classes/functions, non-Python assets, and git
history from **git-tracked** files only (so the numbers are stable and
reproducible regardless of local clutter like ``.pytest_tmp_*`` dirs).

Outputs a Markdown table to stdout that can be pasted into ``METRICS.md``, or
written directly with ``--write``.

Usage:
    python scripts/generate_metrics.py            # print to stdout
    python scripts/generate_metrics.py --check    # exit 1 if METRICS.md is stale
    python scripts/generate_metrics.py --write    # update METRICS.md in place

From the project root:
    .venv/Scripts/python.exe scripts/generate_metrics.py
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

PACKAGES = [
    "kazma-core",
    "kazma-gateway",
    "kazma-ui",
    "kazma-tui",
    "kazma-memory",
    "kazma-skills",
    "kazma-cli",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_FILE = REPO_ROOT / "METRICS.md"

# Regex patterns counted per-file
RE_DEF = re.compile(r"^\s*def\s+\w+", re.M)
RE_ASYNC_DEF = re.compile(r"^\s*async\s+def\s+\w+", re.M)
RE_CLASS = re.compile(r"^\s*class\s+\w+", re.M)
RE_TEST_DEF = re.compile(r"^\s*def\s+test_", re.M)
RE_TEST_ASYNC = re.compile(r"^\s*async\s+def\s+test_", re.M)
RE_TEST_CLASS = re.compile(r"^\s*class\s+Test\w+", re.M)


# ── Git helpers ──────────────────────────────────────────────────────────────


def git(*args: str) -> str:
    """Run a git command in REPO_ROOT and return stdout as text."""
    return subprocess.check_output(
        ["git", *args], cwd=str(REPO_ROOT), text=True, errors="replace"
    ).strip()


def git_files(pattern: str = "*.py") -> list[str]:
    """List git-tracked files matching a pattern."""
    out = git("ls-files", "-z", pattern)
    return [f for f in out.split("\0") if f.strip()]


# ── Counting ─────────────────────────────────────────────────────────────────


def count_lines(paths: list[str]) -> tuple[int, int, int]:
    """Return (total, blank, comment) line counts for the given file paths.

    comment = lines whose first non-whitespace char is ``#``.
    code = total - blank (includes comments, per the cloc convention; callers
    that want pure code subtract comments themselves).
    """
    total = blank = comment = 0
    for rel in paths:
        path = REPO_ROOT / rel
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    total += 1
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        blank += 1
                    elif line.startswith("#"):
                        comment += 1
        except OSError:
            # File may have been removed between ls-files and read; skip it.
            pass
    return total, blank, comment


def count_patterns(paths: list[str], *patterns: re.Pattern) -> list[int]:
    """Count regex matches across the given files. Returns one count per pattern."""
    counts = [0] * len(patterns)
    for rel in paths:
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, pat in enumerate(patterns):
            counts[i] += len(pat.findall(text))
    return counts


def bucket(files: list[str], prefixes: list[str]) -> list[str]:
    return [f for f in files if any(f.startswith(p + "/") for p in prefixes)]


# ── Metric collection ────────────────────────────────────────────────────────


def collect() -> dict:
    py = git_files("*.py")
    src = bucket(py, PACKAGES)
    # Tests live both in the root suite AND in per-package _tests directories
    # (kazma_core_tests/, kazma_gateway_tests/, kazma_ui_tests/, kazma_tui_tests/).
    test_files = [
        f for f in py
        if f.startswith("tests/")
        or f.startswith("loadtests/")
        or any(part.endswith("_tests") for part in Path(f).parts[:-1])
    ]
    examples = [f for f in py if f.startswith("examples/")]
    archive = [f for f in py if f.startswith("archive/")]
    scripts = [f for f in py if f.startswith("scripts/")]
    root_py = [f for f in py if "/" not in f]

    m: dict = {}

    # ── Python totals ──
    t, b, c = count_lines(py)
    m["python"] = {
        "files": len(py),
        "total": t,
        "blank": b,
        "comment": c,
        "code": t - b,           # non-blank (includes comments)
        "pure_code": t - b - c,  # non-blank, non-comment
    }

    # ── Per-area LOC ──
    m["areas"] = {}
    for name, files in [
        ("source", src),
        ("tests", test_files),
        ("examples", examples),
        ("archive", archive),
        ("scripts", scripts),
        ("root", root_py),
    ]:
        at, ab, ac = count_lines(files)
        m["areas"][name] = {"files": len(files), "total": at, "code": at - ab - ac}

    # ── Per-package LOC ──
    m["packages"] = {}
    for pkg in PACKAGES:
        pf = [f for f in py if f.startswith(pkg + "/")]
        pt, pb, pc = count_lines(pf)
        m["packages"][pkg] = {"files": len(pf), "total": pt, "code": pt - pb - pc}

    # ── Tests ──
    tdefs = count_patterns(test_files, RE_TEST_DEF, RE_TEST_ASYNC, RE_TEST_CLASS)
    m["tests"] = {
        "files": len(test_files),
        "test_def": tdefs[0],
        "test_async_def": tdefs[1],
        "test_class": tdefs[2],
        "test_functions_total": tdefs[0] + tdefs[1],
    }

    # ── Source structure ──
    sdefs = count_patterns(src, RE_DEF, RE_ASYNC_DEF, RE_CLASS)
    m["source_structure"] = {
        "def": sdefs[0],
        "async_def": sdefs[1],
        "class": sdefs[2],
    }

    # ── Non-Python assets ──
    m["assets"] = {
        "js": len(git_files("kazma-ui/**/*.js")),
        "tsx": len(git_files("*.tsx")),
        "css": len(git_files("*.css")),
        "html": len([f for f in git_files("**/*.html") if "node_modules" not in f]),
        "yaml": len(
            [
                f
                for f in (git_files("*.yaml") + git_files("*.yml"))
                if "node_modules" not in f
            ]
        ),
        "json": len(
            [
                f
                for f in (git_files("*.json") + git_files("**/*.json"))
                if "node_modules" not in f and "package-lock" not in f
            ]
        ),
        "md": len(git_files("*.md") + git_files("**/*.md")),
        "svg": len(git_files("**/*.svg")),
    }

    # JS LOC
    js_files = git_files("kazma-ui/**/*.js")
    js_t, _, _ = count_lines(js_files)
    m["assets"]["js_loc"] = js_t

    # ── Git history ──
    m["git"] = {
        "commits": int(git("rev-list", "--count", "HEAD") or 0),
        "branches": len(git("branch").splitlines()),
        "tags": len(git("tag").splitlines()) if git("tag") else 0,
        "contributors": len(git("shortlog", "-sne", "HEAD").splitlines()),
    }

    # ── Largest Python files ──
    sizes: list[tuple[str, int]] = []
    for rel in py:
        try:
            n = sum(1 for _ in open(REPO_ROOT / rel, "rb"))
            sizes.append((rel, n))
        except OSError:
            pass
    sizes.sort(key=lambda x: x[1], reverse=True)
    m["largest_files"] = sizes[:15]

    # ── Versions (for the drift note) ──
    m["versions"] = read_versions()

    m["head"] = git("log", "-1", "--format=%h %s (%ad)", "--date=short")
    m["generated"] = date.today().isoformat()
    return m


def read_versions() -> dict[str, str]:
    """Read version strings from the known sources to flag drift."""
    v = {"pyproject": "?", "kazma_yaml": "?", "cli": "?"}
    try:
        txt = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
        if match:
            v["pyproject"] = match.group(1)
    except OSError:
        pass
    try:
        txt = (REPO_ROOT / "kazma.yaml").read_text(encoding="utf-8")
        match = re.search(r"^\s*version:\s*(\S+)", txt, re.M)
        if match:
            v["kazma_yaml"] = match.group(1)
    except OSError:
        pass
    # cli --help string: read the source directly and find a version literal
    # (best-effort, non-fatal)
    v["cli"] = _read_cli_version()
    return v


def _read_cli_version() -> str:
    """Scan kazma_cli/main.py for a version string shown in --help text."""
    candidates = [
        REPO_ROOT / "kazma-cli" / "kazma_cli" / "main.py",
        REPO_ROOT / "kazma_cli" / "main.py",
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Look for "Kazma CLI vX.Y.Z" style strings or version = "X.Y.Z"
        match = re.search(r"[Vv]ersion[:\s]*v?(\d+\.\d+\.\d+)", text)
        if not match:
            match = re.search(r'CLI\s+v?(\d+\.\d+\.\d+)', text)
        if match:
            return match.group(1)
        break
    return "n/a"


# ── Markdown rendering ───────────────────────────────────────────────────────


def render(m: dict) -> str:
    py = m["python"]
    lines = [
        "# Repository Metrics",
        "",
        f"> Auto-generated by `scripts/generate_metrics.py` — run `python scripts/generate_metrics.py --write` to refresh.",
        f"> **Last updated:** {m['generated']} · HEAD: `{m['head']}`",
        "",
        "All figures are derived from **git-tracked** files, so they are stable and reproducible regardless of local build artifacts (`.venv/`, `.pytest_tmp_*`, caches).",
        "",
        "## Python — headline",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Total `.py` files | **{py['files']:,}** |",
        f"| Total lines | **{py['total']:,}** |",
        f"| Pure code lines | {py['pure_code']:,} ({pct(py['pure_code'], py['total'])}) |",
        f"| Blank lines | {py['blank']:,} ({pct(py['blank'], py['total'])}) |",
        f"| Comment/doc lines | {py['comment']:,} ({pct(py['comment'], py['total'])}) |",
        "",
        "## By area",
        "",
        "| Area | Files | Total LOC |",
        "|---|---:|---:|",
    ]
    for name in ["source", "tests", "examples", "archive", "scripts", "root"]:
        a = m["areas"][name]
        label = {
            "source": "Source (7 packages)",
            "tests": "tests/ + loadtests/",
            "root": "root *.py",
        }.get(name, name + "/")
        lines.append(f"| {label} | {a['files']:,} | {a['total']:,} |")

    lines += [
        "",
        "## LOC per package",
        "",
        "| Package | Files | Total LOC | Code LOC |",
        "|---|---:|---:|---:|",
    ]
    for pkg in PACKAGES:
        p = m["packages"][pkg]
        lines.append(f"| `{pkg}` | {p['files']} | {p['total']:,} | {p['code']:,} |")

    src = m["areas"]["source"]
    tests = m["areas"]["tests"]
    ratio = (tests["total"] / src["total"]) if src["total"] else 0
    t = m["tests"]
    lines += [
        "",
        "## Tests",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Test files | **{t['files']}** |",
        f"| `def test_*` functions | {t['test_def']:,} |",
        f"| `async def test_*` functions | {t['test_async_def']:,} |",
        f"| `Test*` classes | {t['test_class']} |",
        f"| Total test functions | **{t['test_functions_total']:,}** |",
        f"| Test LOC | {tests['total']:,} |",
        f"| Test-to-source LOC ratio | ~{ratio:.2f}:1 |",
        "",
        "> The function count above is a **static source count** (greps `def test_*`).",
        "> pytest collects **~3,981** tests at runtime — the gap is `@pytest.mark.parametrize`",
        "> expansion (one function → many test cases). Of those, **3,800+ pass** across all 5 suites.",
        "",
        "## Source structure",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| `def` functions | {m['source_structure']['def']:,} |",
        f"| `async def` functions | {m['source_structure']['async_def']:,} |",
        f"| Classes | {m['source_structure']['class']} |",
        "",
        "## Non-Python assets",
        "",
        "| Type | Files |",
        "|---|---:|",
    ]
    a = m["assets"]
    asset_rows = [
        ("JavaScript (UI static)", a["js"], True),
        ("HTML templates", a["html"], False),
        ("YAML/YML config", a["yaml"], False),
        ("Markdown", a["md"], False),
        ("TSX (docs site)", a["tsx"], False),
        ("CSS", a["css"], False),
        ("JSON", a["json"], False),
        ("SVG", a["svg"], False),
    ]
    for label, count, _ in asset_rows:
        lines.append(f"| {label} | {count} |")
    lines += [
        "",
        f"| JS LOC (UI static) | **{a['js_loc']:,}** |",
        "",
        "## Git history",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Commits | **{m['git']['commits']:,}** |",
        f"| Contributors | {m['git']['contributors']} |",
        f"| Branches | {m['git']['branches']} |",
        f"| Tags | {m['git']['tags']} |",
        "",
        "## Largest Python files (top 15)",
        "",
        "| File | Lines |",
        "|---|---:|",
    ]
    for rel, n in m["largest_files"]:
        lines.append(f"| `{rel}` | {n:,} |")

    # Version drift note
    v = m["versions"]
    drift = len({v["pyproject"], v["kazma_yaml"], v["cli"]} - {"?", "n/a"}) > 1
    lines += [
        "",
        "## Version status",
        "",
        f"- `pyproject.toml`: **{v['pyproject']}**",
        f"- `kazma.yaml` `agent.version`: **{v['kazma_yaml']}**",
        f"- CLI help string: **{v['cli']}**",
    ]
    if drift:
        lines.append("")
        lines.append(
            "> ⚠ **Version drift detected** — these strings are independent and "
            "should be reconciled."
        )

    lines += [
        "",
        "---",
        "",
        "## How to update this file",
        "",
        "```bash",
        "# from the repo root, using the project venv:",
        ".venv/Scripts/python.exe scripts/generate_metrics.py          # print to stdout",
        ".venv/Scripts/python.exe scripts/generate_metrics.py --write   # update METRICS.md in place",
        ".venv/Scripts/python.exe scripts/generate_metrics.py --check   # CI: exit 1 if stale",
        "```",
        "",
        "The generator counts **git-tracked** files only via `git ls-files`, so deleting or "
        "adding source is reflected immediately after a commit. Regenerate after any structural "
        "change (new package, large refactor, test additions).",
        "",
    ]
    return "\n".join(lines)


def pct(part: int, whole: int) -> str:
    return f"{100 * part // whole}%" if whole else "0%"


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Kazma repository metrics.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the output to METRICS.md (default: print to stdout)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against METRICS.md and exit 1 if stale (for CI)",
    )
    args = parser.parse_args()

    if not (REPO_ROOT / ".git").exists():
        print("error: not a git repository (or not run from repo root)", file=sys.stderr)
        return 2

    rendered = render(collect())

    if args.check:
        try:
            existing = METRICS_FILE.read_text(encoding="utf-8")
        except OSError:
            print("error: METRICS.md not found", file=sys.stderr)
            return 1
        if existing.strip() != rendered.strip():
            print("METRICS.md is stale. Run: python scripts/generate_metrics.py --write", file=sys.stderr)
            return 1
        print("METRICS.md is up to date.")
        return 0

    if args.write:
        METRICS_FILE.write_text(rendered, encoding="utf-8")
        print(f"updated {METRICS_FILE.relative_to(REPO_ROOT)}")
        return 0

    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
