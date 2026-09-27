#!/usr/bin/env python
"""Put the framework's metrics on the website: one open pull request, proven.

The website (``Mubder/KazmaAI``, private) builds its numbers from
``src/data/metrics.json`` and keeps ``src/data/METRICS.md`` beside it. This
script copies both from a directory ``generate_metrics.py --out-dir`` wrote
onto the branch ``chore/auto-refresh-metrics`` of the website repository, and
makes sure ONE open pull request carries exactly that commit. Merging it
deploys the site (Cloudflare Pages); nothing here merges, and nothing here
writes to this repository.

Why it checks its own work: from 2026-07-30 to 2026-09-27 the workflow before
it force-pushed that branch on every framework push and logged "Updated
existing PR" about a pull request merged on 2026-07-30. ``gh pr view
<branch>`` returns a branch's most recent pull request in any state, so none
was open, nothing reached the site, and every run was green. Here only an OPEN
pull request counts, and the run fails unless one carries the pushed commit.

Usage (CI: .github/workflows/sync-metrics.yml; locally with ``gh`` signed in):
    python scripts/sync_site_metrics.py                  # generate now, then sync
    python scripts/sync_site_metrics.py --source DIR     # sync what DIR holds
    python scripts/sync_site_metrics.py --dry-run        # prepare, push nothing
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SITE_REPO = "Mubder/KazmaAI"
SITE_BRANCH = "chore/auto-refresh-metrics"
SITE_DATA_DIR = "src/data"
#: What the website takes, named as ``generate_metrics.py --out-dir`` names it.
FILES = ("METRICS.md", "metrics.json")
PR_TITLE = "chore(metrics): refresh the site's metrics from the framework"

#: GitHub shows a pushed commit on its pull request within seconds; this is
#: how long the run waits for that before it calls the sync failed.
VERIFY_ATTEMPTS = 10
VERIFY_DELAY_S = 3.0

Runner = Callable[[Sequence[str], "Path | None"], str]


class SyncError(RuntimeError):
    """The website did not get the metrics; the message says what failed."""


def run_command(cmd: Sequence[str], cwd: Path | None = None) -> str:
    """Run *cmd* and return its stdout, or raise SyncError with its error."""
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise SyncError(f"could not run {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1000:]
        raise SyncError(f"`{' '.join(cmd[:4])}` exited {proc.returncode}: {detail}")
    return proc.stdout


def read_metrics(source: Path) -> dict:
    """The ``metrics.json`` in *source*, checked before anything is sent."""
    for name in FILES:
        if not (source / name).is_file():
            raise SyncError(
                f"{source / name} is missing; write it with "
                f"generate_metrics.py --out-dir {source}"
            )
    try:
        data = json.loads((source / "metrics.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SyncError(f"{source / 'metrics.json'} is not JSON: {exc}") from exc
    if not isinstance(data.get("schema_version"), int) or not (data.get("commit") or {}).get("short"):
        raise SyncError(f"{source / 'metrics.json'} has no schema_version or commit")
    # The site shows this number as its test count; without it the site falls
    # back to the static grep, which under-reports (the n/a copies the old
    # workflow produced for two months).
    if not isinstance((data.get("tests") or {}).get("collected"), int):
        raise SyncError(
            "metrics.json has no runtime test count (tests.collected is null); "
            "generate it with --require-collected where the project is installed"
        )
    return data


def open_pull_requests(run: Runner, repo: str, branch: str) -> list[dict]:
    """The OPEN pull requests whose head is *branch*.

    Never ``gh pr view <branch>``: it answers with the branch's latest pull
    request in any state, and a merged one read as "existing" is how two
    months of refreshes went nowhere.
    """
    out = run(
        [
            "gh", "pr", "list", "--repo", repo, "--head", branch,
            "--state", "open", "--json", "number,url,headRefOid", "--limit", "10",
        ],
        None,
    )
    try:
        prs = json.loads(out or "[]")
    except ValueError as exc:
        raise SyncError(f"gh pr list returned something that is not JSON: {exc}") from exc
    return [pr for pr in prs if isinstance(pr, dict)]


def verify(
    run: Runner, repo: str, branch: str, head: str, *, sleep: Callable[[float], None]
) -> str:
    """The URL of the one open pull request whose head is *head*.

    A push and a pull request call that both "succeed" prove nothing about the
    pull request; this reads it back (and waits a little: GitHub updates the
    head a few seconds after a push).
    """
    seen: list[dict] = []
    for attempt in range(VERIFY_ATTEMPTS):
        if attempt:
            sleep(VERIFY_DELAY_S)
        seen = open_pull_requests(run, repo, branch)
        if len(seen) == 1 and seen[0].get("headRefOid") == head:
            return str(seen[0]["url"])
    found = [f"#{pr.get('number')} at {str(pr.get('headRefOid'))[:8]}" for pr in seen] or ["none"]
    raise SyncError(
        f"no single open pull request on {repo} carries {head[:8]} from {branch} "
        f"(open: {', '.join(found)})"
    )


def _remote_branch(run: Runner, site: Path, branch: str) -> tuple[str, str] | None:
    """``(commit, tree)`` of *branch* on the website, or None if unreadable."""
    try:
        run(["git", "fetch", "--depth", "1", "origin", f"refs/heads/{branch}"], site)
    except SyncError:
        return None
    commit = run(["git", "rev-parse", "FETCH_HEAD"], site).strip()
    tree = run(["git", "rev-parse", "FETCH_HEAD^{tree}"], site).strip()
    return commit, tree


def _commit_message(data: dict) -> str:
    c = data["commit"]
    return (
        f"chore(metrics): refresh from framework @ {c['short']}\n\n"
        f"{c['subject']}\n\n"
        "Written by scripts/sync_site_metrics.py in Mubder/kazma."
    )


def _pr_body(data: dict) -> str:
    c, t = data["commit"], data["tests"]
    return "\n".join(
        [
            f"Metrics of `{data.get('repository', 'Mubder/kazma')}` at `{c['short']}` "
            f"({c['date']}): {c['subject']}",
            "",
            f"- `{SITE_DATA_DIR}/metrics.json`: what the site's build reads "
            f"(schema_version {data['schema_version']})",
            f"- `{SITE_DATA_DIR}/METRICS.md`: the same figures, for people",
            "",
            f"Headline: {data['python']['code_lines']:,} lines of Python code, "
            f"{t['collected']:,} tests collected, {data['git']['commits']:,} commits.",
            "",
            "Merging deploys kazma.ai. The Sync Metrics workflow in Mubder/kazma "
            "replaces this branch on every refresh (daily, or when run by hand), "
            "so do not push to it.",
        ]
    )


def sync(
    source: Path,
    *,
    run: Runner = run_command,
    repo: str = SITE_REPO,
    branch: str = SITE_BRANCH,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Make one open pull request on *repo* carry the metrics in *source*.

    Returns what happened, ending in the pull request's URL when there is
    one. Raises SyncError when the website did not get them.
    """
    data = read_metrics(source)
    # A clone that cannot be removed afterwards (a virus scanner holding a pack
    # file on Windows) must not turn a finished sync into a failed one.
    with tempfile.TemporaryDirectory(prefix="kazma-site-", ignore_cleanup_errors=True) as tmp:
        site = Path(tmp) / "site"
        run(["gh", "repo", "clone", repo, str(site), "--", "--depth", "1"], None)
        run(["git", "checkout", "-B", branch, "origin/main"], site)
        targets = []
        for name in FILES:
            dest = site / SITE_DATA_DIR / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
            targets.append(f"{SITE_DATA_DIR}/{name}")
        run(["git", "add", "--", *targets], site)

        prs = open_pull_requests(run, repo, branch)
        if len(prs) > 1:
            numbers = ", ".join(f"#{pr.get('number')}" for pr in prs)
            raise SyncError(f"{len(prs)} open pull requests from {branch} ({numbers}); expected one")
        pr = prs[0] if prs else None

        if not run(["git", "status", "--porcelain", "--", *targets], site).strip():
            # The site's main already has exactly these files.
            if pr is None:
                return "the site's main already carries these metrics"
            if dry_run:
                return f"dry run: the site's main already carries these metrics; would close {pr['url']}"
            run(
                [
                    "gh", "pr", "close", str(pr["number"]), "--repo", repo,
                    "--comment", "Closed: the site's main already carries these metrics.",
                ],
                None,
            )
            return f"the site's main already carries these metrics; closed {pr['url']}"

        run(["git", "commit", "-m", _commit_message(data)], site)
        head = run(["git", "rev-parse", "HEAD"], site).strip()

        if pr is not None:
            remote = _remote_branch(run, site, branch)
            tree = run(["git", "rev-parse", "HEAD^{tree}"], site).strip()
            if remote is not None and remote[1] == tree:
                # Same files on the same main: a new commit would only rebuild
                # the site's preview. Prove the open pull request carries it.
                return verify(run, repo, branch, remote[0], sleep=sleep)

        if dry_run:
            action = f"update {pr['url']}" if pr else "open a pull request"
            return f"dry run: would push {head[:8]} to {repo}:{branch} and {action}"

        run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{branch}"], site)
        body = _pr_body(data)
        if pr is not None:
            run(["gh", "pr", "edit", str(pr["number"]), "--repo", repo, "--body", body], None)
        else:
            run(
                [
                    "gh", "pr", "create", "--repo", repo, "--base", "main",
                    "--head", branch, "--title", PR_TITLE, "--body", body,
                ],
                None,
            )
        return verify(run, repo, branch, head, sleep=sleep)


def _generate(out: Path) -> None:
    """Write METRICS.md and metrics.json for this checkout into *out*."""
    print(
        run_command(
            [
                sys.executable, str(REPO_ROOT / "scripts" / "generate_metrics.py"),
                "--out-dir", str(out), "--require-collected",
            ],
            REPO_ROOT,
        ).strip()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--source",
        type=Path,
        help="directory holding METRICS.md and metrics.json "
        "(default: generate them for this checkout now)",
    )
    parser.add_argument("--repo", default=SITE_REPO, help=f"website repository (default {SITE_REPO})")
    parser.add_argument("--branch", default=SITE_BRANCH, help=f"pull request branch (default {SITE_BRANCH})")
    parser.add_argument(
        "--dry-run", action="store_true", help="prepare the commit and report; push nothing"
    )
    args = parser.parse_args(argv)
    try:
        if args.source is not None:
            result = sync(args.source, repo=args.repo, branch=args.branch, dry_run=args.dry_run)
        else:
            with tempfile.TemporaryDirectory(prefix="kazma-metrics-") as tmp:
                _generate(Path(tmp))
                result = sync(Path(tmp), repo=args.repo, branch=args.branch, dry_run=args.dry_run)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"Website metrics: {result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
