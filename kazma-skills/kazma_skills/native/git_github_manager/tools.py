"""Git and GitHub Native Skill — tools for local repository and remote API operations."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import httpx
from kazma_core.tools.file_write import _get_workspace

# Disable interactive terminal credential prompts across all Git sub-processes
os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GIT_ASKPASS"] = "echo"

logger = logging.getLogger(__name__)


async def git_status() -> str:
    """Get the current git repository status, branch, and staged/unstaged changes."""
    cwd = _get_workspace()
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return "Not a git repository or git command failed."
        
        # Get active branch name
        branch_res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_res.stdout.strip() or "Detached HEAD"
        
        status_lines = res.stdout.strip()
        if not status_lines:
            return f"On branch {branch}\nWorking tree clean."
        
        return f"On branch {branch}\nChanges:\n{status_lines}"
    except Exception as e:
        return f"Error executing git status: {e}"


async def git_commit(message: str, files: list[str] | None = None) -> str:
    """Commit modified or untracked files with a detailed commit message.

    When bot identity is enabled (``git.bot_identity`` in ``kazma.yaml``),
    the commit is authored as the bot (e.g. ``Kazma Agent [bot]``) via
    ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` env vars — without mutating the
    repo's ``.git/config``.
    """
    cwd = _get_workspace()
    try:
        # Resolve bot identity env (no-op when disabled).
        from kazma_core.git_identity import get_commit_env

        commit_env = get_commit_env()

        # Stage files
        add_args = ["git", "add", "."] if not files else ["git", "add"] + files
        subprocess.run(add_args, cwd=cwd, check=True, env=commit_env)

        # Commit
        res = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=commit_env,
        )
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error committing changes: {e}"


async def git_push_pull(action: str = "pull", branch: str | None = None, remote: str = "origin") -> str:
    """Synchronize local branch changes by executing git pull or git push.

    :param action: Must be 'push' or 'pull'. Set action='push' to push local commits to GitHub.
    :param branch: Branch name to push or pull (e.g. 'main'). Auto-detected if omitted.
    :param remote: Remote name (default 'origin').
    """
    cwd = _get_workspace()
    if action not in ("push", "pull"):
        return "Invalid action. Use 'push' or 'pull'."

    cmd = ["git"]
    token = ""
    try:
        from kazma_gateway.routers.github_client import get_github_token
        token = get_github_token()
    except Exception:
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT") or ""

    if not token:
        return "Error: No active GitHub token or App credentials found. Please configure GitHub App or PAT in the Web UI."

    import base64
    auth_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    auth_header = f"Authorization: Basic {auth_b64}"

    cmd.extend(["-c", "credential.helper=", "-c", f"http.extraheader={auth_header}"])
    cmd.append(action)

    target_branch = branch
    if action == "push":
        # Resolve active branch if not explicitly given
        if not target_branch:
            try:
                b_res = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, capture_output=True, text=True, timeout=5)
                target_branch = b_res.stdout.strip()
            except Exception:
                target_branch = ""

        # Check if upstream tracking branch exists
        has_upstream = False
        try:
            u_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=cwd, capture_output=True, text=True, timeout=5)
            has_upstream = (u_res.returncode == 0 and bool(u_res.stdout.strip()))
        except Exception:
            has_upstream = False

        if not has_upstream and target_branch:
            cmd.extend(["--set-upstream", remote, target_branch])
        elif target_branch:
            cmd.extend([remote, target_branch])

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = res.stdout.strip() or res.stderr.strip()

        # Self-healing: if push rejected because remote is ahead, auto-rebase and retry push!
        if action == "push" and ("fetch first" in output or "non-fast-forward" in output or "remote contains work" in output):
            logger.info("[git_push_pull] Push rejected (remote ahead) — auto-rebasing and retrying push")
            pull_cmd = ["git", "-c", "credential.helper=", "-c", f"http.extraheader={auth_header}", "pull", "--rebase", remote, target_branch or "main"]
            subprocess.run(pull_cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)

            # Retry push
            retry_res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            return retry_res.stdout.strip() or retry_res.stderr.strip()

        return output
    except Exception as e:
        return f"Error running git {action}: {e}"


async def git_checkout(branch: str, create: bool = False) -> str:
    """Switch branches or create a new branch locally."""
    cwd = _get_workspace()
    cmd = ["git", "checkout", "-b", branch] if create else ["git", "checkout", branch]
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=10)
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error running git checkout: {e}"


async def git_merge(source_branch: str) -> str:
    """Merge a branch into the currently active local branch."""
    cwd = _get_workspace()
    try:
        res = subprocess.run(["git", "merge", source_branch], cwd=cwd, capture_output=True, text=True, timeout=15)
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error merging branch {source_branch}: {e}"


async def github_create_pr(title: str, body: str, head: str, base: str = "main") -> str:
    """Create a new Pull Request on the GitHub repository."""
    owner_repo = _resolve_owner_repo()
    if isinstance(owner_repo, str):
        return owner_repo  # error message

    owner, repo = owner_repo
    client = _get_shared_client()
    if client is not None:
        try:
            async with client as gh:
                data = await gh.request(
                    "POST", f"/repos/{owner}/{repo}/pulls",
                    json={"title": title, "body": body, "head": head, "base": base},
                )
            return f"Successfully created Pull Request: {data.get('html_url')}"
        except Exception as e:
            return f"Error creating Pull Request: {e}"

    # Fallback: direct httpx with env-var token (headless deployment).
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: No GitHub token configured. Set GITHUB_TOKEN or connect GitHub in the Web UI."
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code == 201:
                return f"Successfully created Pull Request: {r.json().get('html_url')}"
            return f"Failed to create PR (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error creating Pull Request: {e}"


async def github_merge_pr(number: int, commit_title: str | None = None, merge_method: str = "squash") -> str:
    """Merge an open Pull Request on GitHub."""
    owner_repo = _resolve_owner_repo()
    if isinstance(owner_repo, str):
        return owner_repo

    owner, repo = owner_repo
    client = _get_shared_client()
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title

    if client is not None:
        try:
            async with client as gh:
                data = await gh.request(
                    "PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge",
                    json=payload,
                )
            if data.get("merged"):
                return f"Successfully merged PR #{number}: {data.get('message', 'Merged')}"
            return f"Failed to merge PR #{number}: {data.get('message', 'Unknown error')}"
        except Exception as e:
            return f"Error merging PR #{number}: {e}"

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: No GitHub token configured."
    try:
        async with httpx.AsyncClient() as http:
            r = await http.put(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/merge",
                json=payload,
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code == 200:
                data = r.json()
                return f"Successfully merged PR #{number}: {data.get('message', 'Merged')}"
            return f"Failed to merge PR #{number} (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error merging PR #{number}: {e}"


async def github_create_issue(title: str, body: str, labels: list[str] | None = None) -> str:
    """Create a new Issue on GitHub."""
    owner_repo = _resolve_owner_repo()
    if isinstance(owner_repo, str):
        return owner_repo

    owner, repo = owner_repo
    client = _get_shared_client()
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    if client is not None:
        try:
            async with client as gh:
                data = await gh.request("POST", f"/repos/{owner}/{repo}/issues", json=payload)
            return f"Successfully created Issue #{data.get('number')}: {data.get('html_url')}"
        except Exception as e:
            return f"Error creating issue: {e}"

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: No GitHub token configured."
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                json=payload,
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code == 201:
                data = r.json()
                return f"Successfully created Issue #{data.get('number')}: {data.get('html_url')}"
            return f"Failed to create issue (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error creating issue: {e}"


async def github_comment_issue(number: int, body: str) -> str:
    """Post a comment on a GitHub Issue or Pull Request."""
    owner_repo = _resolve_owner_repo()
    if isinstance(owner_repo, str):
        return owner_repo

    owner, repo = owner_repo
    client = _get_shared_client()
    if client is not None:
        try:
            async with client as gh:
                data = await gh.request(
                    "POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
                    json={"body": body},
                )
            return f"Successfully posted comment on #{number}: {data.get('html_url')}"
        except Exception as e:
            return f"Error posting comment: {e}"

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: No GitHub token configured."
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments",
                json={"body": body},
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code == 201:
                data = r.json()
                return f"Successfully posted comment on #{number}: {data.get('html_url')}"
            return f"Failed to post comment (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error posting comment: {e}"


async def github_list_issues(repo: str | None = None, state: str = "open") -> str:
    """List issues on the repository.

    ``repo`` is optional — when omitted, the repo is inferred from the
    workspace's ``origin`` remote (consistent with ``github_create_pr``).
    Uses the shared ``GitHubClient`` when available.
    """
    # Resolve repo: explicit arg → workspace remote.
    if not repo:
        owner_repo = _resolve_owner_repo()
        if isinstance(owner_repo, str):
            return owner_repo
        slug = f"{owner_repo[0]}/{owner_repo[1]}"
    else:
        slug = repo

    client = _get_shared_client()
    if client is not None:
        try:
            async with client as gh:
                issues = await gh.request(
                    "GET", f"/repos/{slug}/issues", params={"state": state},
                )
            return _format_issues(issues, state)
        except Exception as e:
            return f"Error listing issues: {e}"

    # Fallback: direct httpx (headless).
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(
                f"https://api.github.com/repos/{slug}/issues?state={state}",
                headers=headers,
            )
            if r.status_code == 200:
                return _format_issues(r.json(), state)
            return f"Failed to fetch issues (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error listing issues: {e}"


def _format_issues(issues: list, state: str) -> str:
    """Render an issues list as a compact string."""
    results = []
    for iss in (issues or [])[:10]:
        # GitHub's issues endpoint also returns PRs; filter them out.
        if "pull_request" in iss:
            continue
        results.append(f"#{iss.get('number')}: {iss.get('title')} ({iss.get('html_url')})")
    return "\n".join(results) or f"No {state} issues found."


def _resolve_owner_repo() -> tuple[str, str] | str:
    """Resolve (owner, repo) from the workspace git remote.

    Returns an error-message string on failure (so callers can return it
    directly). Prefers the shared gateway resolver when available.
    """
    try:
        from kazma_gateway.routers.github_client import resolve_repo, get_active_cwd  # type: ignore

        slug = resolve_repo(get_active_cwd())
        if slug:
            return slug
    except Exception:
        pass
    # Fallback: parse locally.
    cwd = _get_workspace()
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        url = res.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
        if not m:
            return f"Could not determine owner/repo from origin remote URL: {url}"
        return m.group(1), m.group(2)
    except Exception as e:
        return f"Error resolving repository: {e}"


def _get_shared_client():
    """Return a GitHubClient instance if the gateway is importable, else None.

    Lazy import so this skill (in ``kazma-skills``, which depends only on
    ``kazma_core``) doesn't hard-depend on ``kazma-gateway``. When the
    gateway is present, the returned client resolves the token via
    ConfigStore (OAuth → PAT → env), closing the gap where OAuth-saved
    tokens were invisible to these tools.
    """
    try:
        from kazma_gateway.routers.github_client import GitHubClient  # type: ignore

        return GitHubClient()
    except Exception:
        return None
