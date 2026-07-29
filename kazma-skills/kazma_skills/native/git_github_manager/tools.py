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


async def _git_sync(action: str = "pull", branch: str | None = None, remote: str = "origin") -> str:
    """Synchronize local branch changes by executing git pull or git push.

    Internal implementation shared by :func:`git_push` and :func:`git_pull`.
    Prefer those explicit tools — calling this with the wrong/default action
    is exactly the footgun the split avoids.

    :param action: Must be 'push' or 'pull'.
    :param branch: Branch name to push or pull (e.g. 'main'). Auto-detected if omitted.
    :param remote: Remote name (default 'origin').
    """
    cwd = _get_workspace()
    if action not in ("push", "pull"):
        return "Invalid action. Use 'push' or 'pull'."

    cmd = ["git"]
    token = ""
    # ── Token resolution for GIT operations (push/pull) ──
    # Prefer the GitHub App installation token (ghs_*): it is the credential
    # designed for git HTTPS operations (sent as x-access-token:<token>@) and
    # carries the App's granted permissions. OAuth/PAT tokens resolved via
    # get_github_token() take precedence there (for the REST API), but a stale
    # gho_ OAuth or ghp_ PAT would shadow the working App token here and get
    # rejected by git as "Invalid username or token" — even though the App
    # token works. So: App token first for git, then OAuth/PAT fallback.
    try:
        from kazma_core.git_identity import get_app_installation_token

        token = get_app_installation_token() or ""
    except Exception:
        token = ""
    if not token:
        try:
            from kazma_gateway.routers.github_client import get_github_token

            token = get_github_token()
        except Exception:
            token = os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_PAT") or ""

    if not token:
        return "Error: No active GitHub token or App credentials found. Please configure GitHub App or PAT in the Web UI."

    # Strip token in case of whitespace/newlines from .env or db
    token = token.strip()

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

    # Retrieve and rewrite the remote URL to inject the token directly
    try:
        remote_res = subprocess.run(["git", "config", "--get", f"remote.{remote}.url"], cwd=cwd, capture_output=True, text=True, timeout=5)
        remote_url = remote_res.stdout.strip()
    except Exception:
        remote_url = ""

    auth_url = ""
    if remote_url:
        if remote_url.startswith("https://"):
            auth_url = remote_url.replace("https://", f"https://x-access-token:{token}@")
        elif remote_url.startswith("git@github.com:"):
            slug = remote_url.split("git@github.com:")[-1]
            auth_url = f"https://x-access-token:{token}@github.com/{slug}"

    if auth_url:
        # Override the remote URL dynamically for this execution only
        cmd.extend(["-c", "credential.helper=", "-c", f"remote.{remote}.url={auth_url}"])
    else:
        # Fallback to extraheader if we couldn't resolve the remote
        import base64
        auth_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        auth_header = f"Authorization: Basic {auth_b64}"
        cmd.extend(["-c", "credential.helper=", "-c", f"http.extraheader={auth_header}"])

    cmd.append(action)

    if action == "push":
        if not has_upstream and target_branch:
            cmd.extend(["--set-upstream", remote, target_branch])
        elif target_branch:
            cmd.extend([remote, target_branch])

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"

    def _looks_like_auth_failure(text: str) -> bool:
        t = text.lower()
        return any(
            s in t
            for s in (
                "could not read username",
                "authentication failed",
                "not authorized",
                "invalid username or password",
                "permission denied",
                "403",  # GH returns 403 for invalid installation tokens on push
                "401",
                "bad credentials",
                "remote: invalid",
                "support for password",
            )
        )

    def _run() -> tuple[int, str]:
        r = subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30
        )
        # Capture BOTH streams — git often puts the real auth error on stderr
        # while stdout holds the benign "Already up to date" / refspec summary.
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        out = (stdout + ("\n" + stderr if stderr else "")) if stderr else (stdout or stderr)
        if token:
            out = out.replace(token, "[REDACTED_TOKEN]")
        # Verbose diagnostic: log exit code + token prefix + tail so the NEXT
        # failure shows exactly what git/GitHub returned (HTTP 401/403, etc.).
        token_prefix = (token[:4] + "***") if token else "none"
        logger.info(
            "[git_push_pull] action=%s returncode=%d token=%s stdout_tail=%r stderr_tail=%r",
            action,
            r.returncode,
            token_prefix,
            stdout[-200:],
            stderr[-200:],
        )
        return r.returncode, out

    def _remote_has_commit(ref: str, sha: str) -> bool:
        """Verify the remote actually points at *sha* (don't trust local exit codes).

        Some auth failures make ``git push origin main`` print a benign
        "Everything up-to-date" / "Already up to date" with exit 0 while
        silently NOT contacting GitHub — so we query the live remote ref.
        """
        try:
            ls_cmd = ["git", "-c", "credential.helper="]
            if auth_url:
                ls_cmd.extend(["-c", f"remote.{remote}.url={auth_url}"])
            else:
                ls_cmd.extend(["-c", f"http.extraheader={auth_header}"])
            ls_cmd.extend(["ls-remote", remote, ref])
            r = subprocess.run(
                ls_cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=20
            )
            out = r.stdout.strip()
            logger.info(
                "[git_push_pull] verify ls-remote %s returncode=%d out_tail=%r",
                ref,
                r.returncode,
                out[-200:],
            )
            # ls-remote output: "<sha>\t<ref>"
            return r.returncode == 0 and sha in out and bool(out)
        except Exception as exc:
            logger.warning("[git_push_pull] verify ls-remote failed: %s", exc)
            return False

    try:
        returncode, output = _run()

        def _refresh_and_retry(prev_output: str) -> str | None:
            """Clear token cache, mint a fresh token, rebuild cmd, retry once.

            Returns the retry output string if a retry happened, else None.
            Handles the rebuild + single retry shared by the exit-nonzero path
            and the verify-mismatch path.
            """
            nonlocal token, auth_url
            token_prefix = (token[:4] + "***") if token else "none"
            logger.info(
                "[git_push_pull] Push auth failure — clearing token cache and re-minting for a single retry"
            )
            try:
                from kazma_core.git_identity import (
                    invalidate_app_token_cache,
                    mint_app_installation_token,
                )

                invalidate_app_token_cache()
                fresh = mint_app_installation_token(force=True)
            except Exception:
                fresh = None

            if not (fresh and fresh != token):
                return None  # No fresh token available; caller reports prev_output.

            # Rebuild cmd / auth_url with the fresh token.
            token = fresh
            if remote_url:
                if remote_url.startswith("https://"):
                    auth_url = remote_url.replace("https://", f"https://x-access-token:{token}@")
                elif remote_url.startswith("git@github.com:"):
                    slug = remote_url.split("git@github.com:")[-1]
                    auth_url = f"https://x-access-token:{token}@github.com/{slug}"
            new_cmd = ["git", "-c", "credential.helper="]
            if auth_url:
                new_cmd.extend(["-c", f"remote.{remote}.url={auth_url}"])
            else:
                import base64 as _b64
                fresh_b64 = _b64.b64encode(f"x-access-token:{token}".encode()).decode()
                new_cmd.extend(["-c", f"http.extraheader=Authorization: Basic {fresh_b64}"])
            new_cmd.append(action)
            if not has_upstream and target_branch:
                new_cmd.extend(["--set-upstream", remote, target_branch])
            elif target_branch:
                new_cmd.extend([remote, target_branch])
            # Rebuild cmd in place so _run() uses the fresh-token command, and
            # _remote_has_commit() uses the fresh auth_url too.
            cmd[:] = new_cmd

            return _run()[1]

        token_prefix = (token[:4] + "***") if token else "none"

        # ── Path 1: explicit failure (non-zero exit) ──
        if returncode != 0:
            if action == "push" and _looks_like_auth_failure(output):
                retried = _refresh_and_retry(output)
                if retried is not None:
                    # Verify the retry actually landed.
                    if target_branch:
                        try:
                            head_sha = subprocess.run(
                                ["git", "rev-parse", "HEAD"], cwd=cwd,
                                capture_output=True, text=True, timeout=5,
                            ).stdout.strip()
                            if head_sha and _remote_has_commit(f"refs/heads/{target_branch}", head_sha):
                                return retried
                        except Exception:
                            pass
                    return retried
                diag = f"[Kazma] Auth failed or rejected. (Used token prefix: {token_prefix}). Ensure token/app is valid.\n"
                return diag + output

            diag = f"[Kazma] Auth failed or rejected. (Used token prefix: {token_prefix}). Ensure token/app is valid.\n"
            return diag + output

        # ── Path 2: push reported success — VERIFY it actually landed ──
        # Some auth failures make `git push origin main` print a benign
        # "Everything up-to-date" / "Already up to date" with exit 0 while
        # silently NOT contacting GitHub (the commit never reaches the remote).
        # We confirm the remote ref actually points at HEAD; if it doesn't AND
        # local is ahead, the push was a no-op and we treat it as an auth fail.
        if action == "push" and target_branch:
            looks_noop = ("up-to-date" in output.lower()) or ("already up to date" in output.lower())
            try:
                head_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=cwd,
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                ahead = subprocess.run(
                    ["git", "rev-list", "--count", f"origin/{target_branch}..HEAD"],
                    cwd=cwd, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                head_sha, ahead = "", ""
            ahead_n = 0
            try:
                ahead_n = int(ahead) if ahead else 0
            except ValueError:
                ahead_n = 0

            if looks_noop and ahead_n > 0 and head_sha:
                # Local is ahead but push said "up to date" — verify the remote.
                if not _remote_has_commit(f"refs/heads/{target_branch}", head_sha):
                    logger.warning(
                        "[git_push_pull] Push reported up-to-date but HEAD is %d commit(s) "
                        "ahead and remote does NOT have %s — treating as auth failure",
                        ahead_n,
                        head_sha[:8],
                    )
                    synthetic = (
                        "[Kazma] Push reported 'up to date' but the commit did not reach GitHub "
                        "(local is ahead, remote unchanged). This is usually an auth/token failure "
                        "that git surfaced as a no-op. Attempting token refresh...\n"
                    )
                    retried = _refresh_and_retry(synthetic)
                    if retried is not None:
                        # Verify the retry landed.
                        if _remote_has_commit(f"refs/heads/{target_branch}", head_sha):
                            return retried
                        diag = (
                            "[Kazma] Auth failed or rejected after token refresh. "
                            f"(Used token prefix: {token_prefix}). The commit still did not reach GitHub. "
                            "Ensure token/app is valid and the App is installed on the repo.\n"
                        )
                        return diag + retried
                    diag = (
                        "[Kazma] Push did not reach GitHub and no fresh token could be minted. "
                        f"(Used token prefix: {token_prefix}). Ensure token/app is valid.\n"
                    )
                    return diag + synthetic + output

        # Self-healing: if push rejected because remote is ahead, auto-rebase and retry push!
        if action == "push" and ("fetch first" in output or "non-fast-forward" in output or "remote contains work" in output):
            logger.info("[git_push_pull] Push rejected (remote ahead) — auto-rebasing and retrying push")

            pull_cmd = ["git"]
            if auth_url:
                pull_cmd.extend(["-c", "credential.helper=", "-c", f"remote.{remote}.url={auth_url}"])
            else:
                pull_cmd.extend(["-c", "credential.helper=", "-c", f"http.extraheader={auth_header}"])
            pull_cmd.extend(["pull", "--rebase", remote, target_branch or "main"])

            subprocess.run(pull_cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)

            # Retry push
            rc2, out2 = _run()
            if rc2 != 0:
                diag = f"[Kazma] Auth failed or rejected after rebase. (Used token prefix: {token_prefix}).\n"
                return diag + out2

            return out2

        return output
    except Exception as e:
        err_msg = str(e)
        if token:
            err_msg = err_msg.replace(token, "[REDACTED_TOKEN]")
        return f"Error running git {action}: {err_msg}"


async def git_push(branch: str | None = None, remote: str = "origin") -> str:
    """Push local commits to the remote repository on GitHub.

    Use this to publish local commits to GitHub. Uploads the current branch's
    commits to the named remote (default origin). If the branch has no upstream
    tracking, ``--set-upstream`` is added automatically.

    :param branch: Branch to push (e.g. 'main'). Auto-detected from the current
        branch if omitted.
    :param remote: Remote name (default 'origin').
    """
    return await _git_sync(action="push", branch=branch, remote=remote)


async def git_pull(branch: str | None = None, remote: str = "origin") -> str:
    """Fetch and merge remote changes into the local branch.

    Use this to pull the latest changes FROM GitHub into the local branch. This
    does NOT push anything — call :func:`git_push` to publish local commits.

    :param branch: Branch to pull (e.g. 'main'). Auto-detected from the current
        branch if omitted.
    :param remote: Remote name (default 'origin').
    """
    return await _git_sync(action="pull", branch=branch, remote=remote)


async def git_push_pull(action: str = "pull", branch: str | None = None, remote: str = "origin") -> str:
    """Synchronize local branch changes by executing git pull or git push.

    .. deprecated::
        Use the explicit :func:`git_push` or :func:`git_pull` tools instead.
        This merged tool defaults to ``action="pull"``, which previously caused
        the agent to run *pull* when it meant *push*. Kept for backward
        compatibility with direct callers/tests only — it is NOT exposed to the
        agent via the skill manifest.

    :param action: Must be 'push' or 'pull'. Set action='push' to push local commits to GitHub.
    :param branch: Branch name to push or pull (e.g. 'main'). Auto-detected if omitted.
    :param remote: Remote name (default 'origin').
    """
    return await _git_sync(action=action, branch=branch, remote=remote)


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
