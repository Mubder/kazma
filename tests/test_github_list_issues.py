"""The GitHub issue list never says "none" when GitHub did not show them all.

Live 2026-09-28: asked for the open issues of Mubder/kazma, the agent's tool
answered "No open issues found." while issue #20 was open, and the agent
reported it as fact. The live install's token got an empty list where an
anonymous request gets #20 -- GitHub filters the endpoint to what a token may
read (a fine-grained token without "Issues") instead of refusing. The tool
now checks an empty answer against the repository's own open count. And the
list is cut to ten AFTER pull requests are dropped: cutting first let ten
newer pull requests hide every issue.
"""

from __future__ import annotations

import pytest

from kazma_skills.native.git_github_manager import tools


def _issue(n: int) -> dict:
    return {"number": n, "title": f"issue {n}", "html_url": f"https://github.com/o/r/issues/{n}", "state": "open"}


def _pr(n: int) -> dict:
    return {**_issue(n), "title": f"pr {n}", "pull_request": {"url": "x"}}


def test_newer_pull_requests_do_not_hide_an_issue():
    items = [_pr(n) for n in range(40, 28, -1)] + [_issue(20)]
    assert tools._format_issues(items, "open").startswith("#20: issue 20")


def test_the_old_cut_hid_it():
    """Negative control: the formatter as it was until 2026-09-28."""

    def old(issues, state):
        results = []
        for iss in (issues or [])[:10]:
            if "pull_request" in iss:
                continue
            results.append(f"#{iss.get('number')}")
        return "\n".join(results) or f"No {state} issues found."

    items = [_pr(n) for n in range(40, 28, -1)] + [_issue(20)]
    assert old(items, "open") == "No open issues found."


def test_an_empty_answer_the_repository_contradicts_is_not_believed():
    out = tools._format_issues([], "open", repo_meta={"open_issues_count": 1})
    assert "No open issues found" not in out
    assert "counts 1 open issue" in out and "Issues: Read" in out


def test_open_pull_requests_account_for_the_count():
    """Two open PRs and a count of two: nothing is missing."""
    out = tools._format_issues([_pr(5), _pr(6)], "open", repo_meta={"open_issues_count": 2})
    assert out == "No open issues found."


def test_a_repository_with_nothing_open_says_so():
    assert tools._format_issues([], "open", repo_meta={"open_issues_count": 0}) == "No open issues found."


def test_closed_issues_are_not_checked_against_the_open_count():
    assert tools._format_issues([], "closed", repo_meta={"open_issues_count": 3}) == "No closed issues found."


def test_more_than_ten_issues_says_how_many_more():
    out = tools._format_issues([_issue(n) for n in range(1, 14)], "open")
    assert out.count("\n#") == 9 and out.endswith("... and 3 more")


class _FakeGitHub:
    """The shared client's surface: an async context manager with request()."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, path, params=None, json=None):
        self.calls.append((path, params))
        return self.answers[path]


async def test_the_tool_asks_the_repository_when_github_lists_nothing(monkeypatch):
    gh = _FakeGitHub({
        "/repos/Mubder/kazma/issues": [],
        "/repos/Mubder/kazma": {"open_issues_count": 1},
    })
    monkeypatch.setattr(tools, "_get_shared_client", lambda: gh)
    out = await tools.github_list_issues(repo="Mubder/kazma", state="open")
    assert "Issues: Read" in out and "No open issues found" not in out
    assert gh.calls[0] == ("/repos/Mubder/kazma/issues", {"state": "open", "per_page": 100})


async def test_the_tool_lists_issues_without_a_second_request(monkeypatch):
    gh = _FakeGitHub({"/repos/Mubder/kazma/issues": [_issue(20)]})
    monkeypatch.setattr(tools, "_get_shared_client", lambda: gh)
    out = await tools.github_list_issues(repo="Mubder/kazma", state="open")
    assert "#20: issue 20" in out
    assert [c[0] for c in gh.calls] == ["/repos/Mubder/kazma/issues"]


@pytest.mark.parametrize("bad", [None, {"message": "Not Found"}, "text"])
def test_a_malformed_answer_is_not_an_issue_list(bad):
    assert tools._format_issues(bad, "open") == "No open issues found."
