"""The website gets its metrics through one open pull request, or the run fails.

From 2026-07-30 to 2026-09-27 the Sync Metrics workflow force-pushed the
site's metrics branch on every framework push and logged "Updated existing
PR" about PR #3 -- merged on 2026-07-30 -- because ``gh pr view <branch>``
returns a branch's latest pull request in any state. No pull request was
open, the site kept old numbers (then a hand copy of 2026-09-24), and every
run was green. The job also installed nothing, so every copy it made said
"Collected at runtime: n/a", and it ran per push: 529 Cloudflare preview
builds of the site in September.

These tests drive ``scripts/sync_site_metrics.py`` against a fake GitHub that
answers the way the real one does (merged pull requests included), and hold
``.github/workflows/sync-metrics.yml`` to its rules. Each has a negative
control.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "sync-metrics.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location(
        "sync_site_metrics_mod", REPO / "scripts" / "sync_site_metrics.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ssm = _load()
SITE = ssm.SITE_REPO
BRANCH = ssm.SITE_BRANCH
TARGETS = [f"{ssm.SITE_DATA_DIR}/{name}" for name in ssm.FILES]


def _digest(*parts: object) -> str:
    return hashlib.sha1(repr(parts).encode()).hexdigest()


class FakeGitHub:
    """``gh`` and ``git`` for the website repository, answering like the real
    ones: ``gh pr view <branch>`` returns the latest pull request in ANY
    state, ``gh pr list --state open`` only open ones, ``gh pr create``
    refuses a second open pull request for a branch."""

    def __init__(
        self,
        *,
        main: dict[str, str] | None = None,
        prs: list[dict] | None = None,
        branch_files: dict[str, str] | None = None,
        push_reaches_pr: bool = True,
    ):
        self.main = dict(main or {})
        self.prs = [dict(pr) for pr in (prs or [])]
        self.branch: tuple[str, str] | None = None
        if branch_files is not None:
            tree = self._tree({**self.main, **branch_files})
            self.branch = (_digest("branch", tree), tree)
            for pr in self.prs:
                if pr["state"] == "OPEN":
                    pr["headRefOid"] = self.branch[0]
        self.push_reaches_pr = push_reaches_pr
        self.calls: list[list[str]] = []
        self.head = self.tree = ""

    @staticmethod
    def _tree(files: dict[str, str]) -> str:
        return _digest("tree", sorted(files.items()))

    def _open(self) -> list[dict]:
        return [pr for pr in self.prs if pr["state"] == "OPEN" and pr["head"] == BRANCH]

    def _staged(self, cwd: Path) -> dict[str, str]:
        return {t: (cwd / t).read_text(encoding="utf-8") for t in TARGETS}

    def __call__(self, cmd, cwd=None) -> str:  # noqa: C901 -- one branch per command
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "repo", "clone"]:
            Path(cmd[4]).mkdir(parents=True)
            return ""
        if cmd[:2] == ["git", "checkout"] or cmd[:2] == ["git", "add"]:
            return ""
        if cmd[:3] == ["gh", "pr", "list"]:
            assert cmd[cmd.index("--state") + 1] == "open"
            return json.dumps(
                [{"number": p["number"], "url": p["url"], "headRefOid": p["headRefOid"]} for p in self._open()]
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            latest = [p for p in self.prs if p["head"] == cmd[3]][-1]
            return json.dumps({"url": latest["url"], "state": latest["state"]})
        if cmd[:3] == ["git", "status", "--porcelain"]:
            staged = self._staged(cwd)
            return "".join(f"M  {t}\n" for t in TARGETS if self.main.get(t) != staged[t])
        if cmd[:2] == ["git", "commit"]:
            self.tree = self._tree({**self.main, **self._staged(cwd)})
            self.head = _digest("commit", self.tree, len(self.calls))
            return ""
        if cmd[:2] == ["git", "rev-parse"]:
            return {
                "HEAD": self.head,
                "HEAD^{tree}": self.tree,
                "FETCH_HEAD": self.branch[0] if self.branch else "",
                "FETCH_HEAD^{tree}": self.branch[1] if self.branch else "",
            }[cmd[2]] + "\n"
        if cmd[:2] == ["git", "fetch"]:
            if self.branch is None:
                raise ssm.SyncError("fatal: couldn't find remote ref")
            return ""
        if cmd[:2] == ["git", "push"]:
            assert cmd[-1] == f"HEAD:refs/heads/{BRANCH}"
            self.branch = (self.head, self.tree)
            if self.push_reaches_pr:
                for pr in self._open():
                    pr["headRefOid"] = self.head
            return ""
        if cmd[:3] == ["gh", "pr", "create"]:
            if self._open():
                raise ssm.SyncError(f"a pull request for branch {BRANCH} already exists")
            number = max([p["number"] for p in self.prs] + [0]) + 1
            self.prs.append(
                {
                    "number": number,
                    "url": f"https://github.com/{SITE}/pull/{number}",
                    "state": "OPEN",
                    "head": BRANCH,
                    "headRefOid": self.branch[0],
                }
            )
            return f"https://github.com/{SITE}/pull/{number}\n"
        if cmd[:3] == ["gh", "pr", "edit"] or cmd[:3] == ["gh", "pr", "close"]:
            if cmd[2] == "close":
                next(p for p in self.prs if str(p["number"]) == cmd[3])["state"] = "CLOSED"
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    def ran(self, *prefix: str) -> bool:
        return any(call[: len(prefix)] == list(prefix) for call in self.calls)


MERGED_3 = {
    "number": 3,
    "url": f"https://github.com/{SITE}/pull/3",
    "state": "MERGED",
    "head": BRANCH,
    "headRefOid": "0" * 40,
}


def _source(tmp_path: Path, *, collected: int | None = 11_376, marker: str = "fresh") -> Path:
    src = tmp_path / "metrics"
    src.mkdir()
    data = {
        "schema_version": 1,
        "repository": "Mubder/kazma",
        "commit": {"sha": "9" * 40, "short": "99999999", "subject": marker, "date": "2026-09-27"},
        "python": {"code_lines": 405_101},
        "tests": {"collected": collected},
        "git": {"commits": 4_036},
    }
    (src / "metrics.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "METRICS.md").write_text(f"# Repository Metrics ({marker})\n", encoding="utf-8")
    return src


def _files(src: Path) -> dict[str, str]:
    return {t: (src / Path(t).name).read_text(encoding="utf-8") for t in TARGETS}


def _no_sleep(_seconds: float) -> None:
    return None


# ── The pull request ─────────────────────────────────────────────────────────


def test_a_merged_pull_request_is_not_an_open_one(tmp_path):
    src = _source(tmp_path)
    gh = FakeGitHub(main={TARGETS[0]: "# July\n"}, prs=[MERGED_3])
    url = ssm.sync(src, run=gh, sleep=_no_sleep)
    assert url == f"https://github.com/{SITE}/pull/4"
    assert gh.ran("gh", "pr", "create")
    assert not gh.ran("gh", "pr", "view")


def test_the_old_lookup_took_the_merged_pull_request_for_an_open_one():
    """Negative control: the lookup the old workflow used, against the same
    fake, answers "a pull request exists" -- so it pushed, skipped
    `gh pr create`, and logged "Updated existing PR" about #3."""
    gh = FakeGitHub(prs=[MERGED_3])
    old = json.loads(gh(["gh", "pr", "view", BRANCH, "--repo", SITE, "--json", "url,state"]))
    assert old["url"].endswith("/pull/3")  # the old workflow: "exists, update it"
    assert ssm.open_pull_requests(gh, SITE, BRANCH) == []  # the truth: none open


def test_an_open_pull_request_is_updated_not_duplicated(tmp_path):
    open_5 = {**MERGED_3, "number": 5, "url": f"https://github.com/{SITE}/pull/5", "state": "OPEN"}
    gh = FakeGitHub(main={}, prs=[MERGED_3, open_5], branch_files={TARGETS[0]: "# yesterday\n"})
    url = ssm.sync(_source(tmp_path), run=gh, sleep=_no_sleep)
    assert url.endswith("/pull/5")
    assert gh.ran("git", "push") and gh.ran("gh", "pr", "edit", "5")
    assert not gh.ran("gh", "pr", "create")


def test_an_unchanged_refresh_pushes_nothing(tmp_path):
    """Same files on the same main: a push would only rebuild the preview."""
    src = _source(tmp_path)
    open_5 = {**MERGED_3, "number": 5, "url": f"https://github.com/{SITE}/pull/5", "state": "OPEN"}
    gh = FakeGitHub(main={}, prs=[open_5], branch_files=_files(src))
    assert ssm.sync(src, run=gh, sleep=_no_sleep).endswith("/pull/5")
    assert not gh.ran("git", "push")
    assert not gh.ran("gh", "pr", "edit")


def test_a_current_site_closes_the_stale_pull_request(tmp_path):
    src = _source(tmp_path)
    open_5 = {**MERGED_3, "number": 5, "url": f"https://github.com/{SITE}/pull/5", "state": "OPEN"}
    gh = FakeGitHub(main=_files(src), prs=[open_5], branch_files={TARGETS[0]: "# older\n"})
    result = ssm.sync(src, run=gh, sleep=_no_sleep)
    assert "already carries" in result and result.endswith("/pull/5")
    assert gh.ran("gh", "pr", "close", "5")
    assert not gh.ran("git", "push")


def test_the_run_fails_when_no_open_pull_request_carries_the_push(tmp_path):
    """A push and a `gh pr create` that both "succeed" prove nothing; the run
    reads the pull request back and fails loudly."""
    open_5 = {**MERGED_3, "number": 5, "url": f"https://github.com/{SITE}/pull/5", "state": "OPEN"}
    gh = FakeGitHub(main={}, prs=[open_5], branch_files={TARGETS[0]: "# old\n"}, push_reaches_pr=False)
    waits: list[float] = []
    with pytest.raises(ssm.SyncError, match="no single open pull request"):
        ssm.sync(_source(tmp_path), run=gh, sleep=waits.append)
    assert len(waits) == ssm.VERIFY_ATTEMPTS - 1


def test_metrics_without_the_test_count_are_refused_before_anything_is_sent(tmp_path):
    gh = FakeGitHub()
    with pytest.raises(ssm.SyncError, match="no runtime test count"):
        ssm.sync(_source(tmp_path, collected=None), run=gh, sleep=_no_sleep)
    assert gh.calls == []


def test_a_dry_run_pushes_nothing(tmp_path):
    gh = FakeGitHub(main={}, prs=[MERGED_3])
    result = ssm.sync(_source(tmp_path), run=gh, dry_run=True, sleep=_no_sleep)
    assert result.startswith("dry run: would push")
    assert not gh.ran("git", "push") and not gh.ran("gh", "pr", "create")


# ── The workflow ─────────────────────────────────────────────────────────────


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the key `on` as the boolean True.
    return workflow.get("on", workflow.get(True)) or {}


def _steps(workflow: dict) -> list[dict]:
    return [step for job in (workflow.get("jobs") or {}).values() for step in job.get("steps") or []]


def _install_line(workflow: dict, job: str | None = None) -> str:
    jobs = workflow.get("jobs") or {}
    steps = jobs[job]["steps"] if job else _steps(workflow)
    return next(str(s.get("run", "")).strip() for s in steps if s.get("name") == "Install deps")


def workflow_problems(sync_text: str, ci_text: str) -> list[str]:
    """The ways a sync workflow breaks the rules this file's docstring tells."""
    wf, ci = yaml.safe_load(sync_text), yaml.safe_load(ci_text)
    problems = []
    triggers = set(_triggers(wf))
    if triggers != {"schedule", "workflow_dispatch"}:
        problems.append(f"runs on {sorted(triggers)}: daily and by hand only (each run rebuilds a site preview)")
    grants = [wf.get("permissions")] + [j.get("permissions") for j in (wf.get("jobs") or {}).values()]
    if wf.get("permissions") != {"contents": "read"} or any(
        "write" in json.dumps(g) for g in grants if g is not None
    ):
        problems.append("may write to this repository: permissions must be contents: read")
    if _install_line(wf) != _install_line(ci, "tests"):
        problems.append("installs something else than CI's Tests job: the test count would differ or be n/a")
    runs = [str(s.get("run", "")) for s in _steps(wf)]
    if not any("generate_metrics.py" in r and "--require-collected" in r for r in runs):
        problems.append("does not generate with --require-collected: it could publish n/a")
    if not any("scripts/sync_site_metrics.py" in r for r in runs):
        problems.append("does not sync through scripts/sync_site_metrics.py")
    if any("gh pr view" in r for r in runs):
        problems.append("looks a pull request up with `gh pr view`, which returns merged ones")
    return problems


def test_the_sync_workflow_keeps_its_rules():
    assert workflow_problems(
        WORKFLOW.read_text(encoding="utf-8"), CI.read_text(encoding="utf-8")
    ) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("  workflow_dispatch:", "  workflow_dispatch:\n  push:\n    branches: [main]"), "runs on"),
        (("  contents: read", "  contents: write"), "may write"),
        (("numpy prometheus-client", "numpy"), "installs something else"),
        (('"$RUNNER_TEMP/site-metrics" --require-collected', '"$RUNNER_TEMP/site-metrics"'), "--require-collected"),
        (("python scripts/sync_site_metrics.py", "gh pr view \"$B\" ; python scripts/sync_site_metrics.py"), "gh pr view"),
    ],
)
def test_the_rules_see_each_break(change, problem):
    """Negative controls: each rule fails on the workflow broken its way."""
    text = WORKFLOW.read_text(encoding="utf-8")
    old, new = change
    assert old in text, f"the control's anchor {old!r} is gone from the workflow"
    broken = text.replace(old, new, 1)
    found = workflow_problems(broken, CI.read_text(encoding="utf-8"))
    assert any(problem in p for p in found), found
