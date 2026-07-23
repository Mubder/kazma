"""Install Agent Skills from GitHub / local paths without Node/npm.

Supports:
  - owner/repo                 (e.g. shadcn/improve)
  - https://github.com/owner/repo
  - https://github.com/owner/repo/tree/branch/path
  - local filesystem path containing SKILL.md

Downloads via GitHub zipball (httpx) — no shell, no npx.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kazma_core.agent_skills.discovery import user_agent_skills_dir
from kazma_core.agent_skills.parser import parse_skill_md

__all__ = [
    "InstallResult",
    "install_from_github",
    "install_from_source",
    "parse_github_source",
    "uninstall_skill",
]

logger = logging.getLogger(__name__)

_GITHUB_HTTPS = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subpath>.*))?)?"
    r"/?$",
    re.IGNORECASE,
)
_OWNER_REPO = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$"
)
_NPX_SKILLS = re.compile(
    r"(?:npx\s+skills\s+add\s+|skills\s+add\s+)"
    r"(?P<source>\S+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class InstallResult:
    """Outcome of an install attempt."""

    success: bool
    message: str
    installed: list[dict[str, str]] = field(default_factory=list)
    source: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "installed": self.installed,
            "source": self.source,
            "errors": self.errors,
        }

    def to_user_message(self) -> str:
        if not self.success:
            err = "; ".join(self.errors) if self.errors else self.message
            return f"❌ Skill install failed: {err}"
        if not self.installed:
            return f"⚠️ {self.message}"
        lines = [f"✅ Installed {len(self.installed)} skill(s) from `{self.source}`:"]
        for item in self.installed:
            lines.append(
                f"  • **{item['name']}** — {item.get('description', '')[:120]}"
            )
            lines.append(f"    → `{item.get('path', '')}`")
        lines.append(
            "\nActivate with `activate_skill(name)` or just ask me to use the skill."
        )
        return "\n".join(lines)


def parse_github_source(source: str) -> dict[str, str] | None:
    """Parse a user/agent-provided source into owner/repo/ref/subpath.

    Accepts owner/repo, GitHub URLs, and ``npx skills add …`` phrases.
    """
    raw = (source or "").strip()
    if not raw:
        return None

    # Strip common wrappers
    raw = raw.strip("`\"'")
    npx = _NPX_SKILLS.search(raw)
    if npx:
        raw = npx.group("source").strip()

    # Drop trailing fragments
    raw = raw.rstrip("/").removesuffix(".git")

    m = _GITHUB_HTTPS.match(raw)
    if m:
        return {
            "owner": m.group("owner"),
            "repo": m.group("repo").removesuffix(".git"),
            "ref": m.group("ref") or "",
            "subpath": (m.group("subpath") or "").strip("/"),
        }

    m = _OWNER_REPO.match(raw)
    if m:
        return {
            "owner": m.group("owner"),
            "repo": m.group("repo"),
            "ref": "",
            "subpath": "",
        }

    # Bare skill slug that looks like a GitHub path without scheme
    if "github.com/" in raw.lower():
        idx = raw.lower().index("github.com/")
        return parse_github_source("https://" + raw[idx:])

    return None


def _find_skill_dirs(root: Path) -> list[Path]:
    """Find directories containing SKILL.md under *root*."""
    found: list[Path] = []
    if (root / "SKILL.md").is_file():
        found.append(root)
    for path in root.rglob("SKILL.md"):
        if path.is_file() and path.parent not in found:
            # Skip deep vendor noise
            parts = set(path.parts)
            if parts & {".git", "node_modules", "__pycache__", ".venv"}:
                continue
            found.append(path.parent)
    return found


def _copy_skill(src_dir: Path, dest_dir: Path, *, source: str) -> dict[str, str]:
    """Copy a skill directory into dest_dir/<name>/ and write install meta."""
    text = (src_dir / "SKILL.md").read_text(encoding="utf-8")
    parsed = parse_skill_md(text, path=src_dir / "SKILL.md", directory_name=src_dir.name)
    if parsed is None:
        raise ValueError(f"Invalid SKILL.md in {src_dir}")

    name = parsed.name or src_dir.name
    target = dest_dir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        src_dir,
        target,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "node_modules", ".DS_Store"
        ),
    )
    meta = {
        "source": source,
        "name": name,
        "version": parsed.version,
        "author": parsed.author,
    }
    (target / ".kazma-install.json").write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )
    return {
        "name": name,
        "description": parsed.description,
        "path": str(target),
        "version": parsed.version,
        "author": parsed.author,
    }


async def _download_github_zip(
    owner: str,
    repo: str,
    ref: str = "",
) -> tuple[Path, Path]:
    """Download a GitHub repo zipball.

    Returns ``(repo_root, cleanup_dir)`` — caller must delete *cleanup_dir*.
    """
    import httpx

    candidates = []
    if ref:
        candidates.append(
            f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
        )
        candidates.append(
            f"https://codeload.github.com/{owner}/{repo}/zip/refs/tags/{ref}"
        )
        candidates.append(f"https://codeload.github.com/{owner}/{repo}/zip/{ref}")
    else:
        for branch in ("main", "master"):
            candidates.append(
                f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
            )
        candidates.append(f"https://api.github.com/repos/{owner}/{repo}/zipball")

    tmp = Path(tempfile.mkdtemp(prefix="kazma-skill-"))
    zip_path = tmp / "repo.zip"
    last_err: Exception | None = None

    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Kazma-AgentSkills/1.0",
            "Accept": "application/vnd.github+json",
        },
    ) as client:
        for url in candidates:
            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                zip_path.write_bytes(resp.content)
                break
            except Exception as exc:
                last_err = exc
                continue
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError(
                f"Could not download {owner}/{repo}"
                + (f" (last error: {last_err})" if last_err else "")
            )

    extract_dir = tmp / "extract"
    extract_dir.mkdir()
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # GitHub zipballs have a single top-level folder owner-repo-sha/
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    repo_root = children[0] if len(children) == 1 else extract_dir
    return repo_root, tmp


async def install_from_github(
    source: str,
    *,
    target_dir: Path | None = None,
    scope: str = "user",
) -> InstallResult:
    """Install one or more SKILL.md skills from a GitHub source string."""
    parsed = parse_github_source(source)
    if not parsed:
        return InstallResult(
            success=False,
            message="Unrecognized source",
            source=source,
            errors=[
                "Expected owner/repo, a GitHub URL, or `npx skills add owner/repo`. "
                f"Got: {source!r}"
            ],
        )

    owner, repo = parsed["owner"], parsed["repo"]
    ref, subpath = parsed["ref"], parsed["subpath"]
    label = (
        f"{owner}/{repo}"
        + (f"@{ref}" if ref else "")
        + (f"/{subpath}" if subpath else "")
    )

    dest = target_dir
    if dest is None:
        if scope == "project":
            try:
                from kazma_core.paths import get_project_root

                dest = get_project_root() / ".agents" / "skills"
            except Exception:
                dest = Path.cwd() / ".agents" / "skills"
        else:
            dest = user_agent_skills_dir()
    dest.mkdir(parents=True, exist_ok=True)

    cleanup: Path | None = None
    try:
        root, cleanup = await _download_github_zip(owner, repo, ref)
        search_root = root / subpath if subpath else root
        if not search_root.exists():
            return InstallResult(
                success=False,
                message="Subpath not found in repo",
                source=label,
                errors=[f"Path {subpath!r} does not exist in {owner}/{repo}"],
            )

        skill_dirs = _find_skill_dirs(search_root)
        if not skill_dirs:
            return InstallResult(
                success=False,
                message="No SKILL.md found",
                source=label,
                errors=[
                    f"Repository {owner}/{repo} has no Agent Skills (SKILL.md). "
                    "See https://agentskills.io/specification"
                ],
            )

        installed: list[dict[str, str]] = []
        errors: list[str] = []
        for skill_dir in skill_dirs:
            try:
                info = _copy_skill(skill_dir, dest, source=label)
                installed.append(info)
            except Exception as exc:
                errors.append(f"{skill_dir.name}: {exc}")

        if not installed:
            return InstallResult(
                success=False,
                message="Install failed",
                source=label,
                errors=errors or ["No skills copied"],
            )

        return InstallResult(
            success=True,
            message=f"Installed {len(installed)} skill(s)",
            installed=installed,
            source=label,
            errors=errors,
        )
    except Exception as exc:
        return InstallResult(
            success=False,
            message="Download failed",
            source=label,
            errors=[str(exc)],
        )
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def install_from_source(
    source: str | Path,
    *,
    target_dir: Path | None = None,
) -> InstallResult:
    """Install from a local filesystem path (sync)."""
    path = Path(source).expanduser().resolve()
    if not path.exists():
        return InstallResult(
            success=False,
            message="Path not found",
            source=str(path),
            errors=[f"No such path: {path}"],
        )

    dest = target_dir or user_agent_skills_dir()
    dest.mkdir(parents=True, exist_ok=True)

    if path.is_file() and path.name == "SKILL.md":
        skill_dirs = [path.parent]
    elif path.is_dir():
        skill_dirs = _find_skill_dirs(path)
    else:
        return InstallResult(
            success=False,
            message="Invalid path",
            source=str(path),
            errors=["Path must be a directory or a SKILL.md file"],
        )

    if not skill_dirs:
        return InstallResult(
            success=False,
            message="No SKILL.md found",
            source=str(path),
            errors=["No Agent Skills found at path"],
        )

    installed: list[dict[str, str]] = []
    errors: list[str] = []
    for skill_dir in skill_dirs:
        try:
            info = _copy_skill(skill_dir, dest, source=str(path))
            installed.append(info)
        except Exception as exc:
            errors.append(f"{skill_dir.name}: {exc}")

    return InstallResult(
        success=bool(installed),
        message=f"Installed {len(installed)} skill(s)" if installed else "Install failed",
        installed=installed,
        source=str(path),
        errors=errors,
    )


async def install_from_any(
    source: str,
    *,
    target_dir: Path | None = None,
    scope: str = "user",
) -> InstallResult:
    """Install from GitHub source or local path (auto-detect)."""
    raw = (source or "").strip()
    if not raw:
        return InstallResult(
            success=False,
            message="Empty source",
            errors=["Provide owner/repo, a GitHub URL, or a local path"],
        )

    # Local path?
    p = Path(raw).expanduser()
    if p.exists():
        return install_from_source(p, target_dir=target_dir)

    # file:// URL
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        return install_from_source(parsed.path, target_dir=target_dir)

    return await install_from_github(raw, target_dir=target_dir, scope=scope)


def uninstall_skill(name: str, *, target_dir: Path | None = None) -> InstallResult:
    """Remove an installed Agent Skill by name from the user skills dir."""
    dest = target_dir or user_agent_skills_dir()
    target = dest / name
    # Also check ~/.kazma/agent-skills
    alt = Path.home() / ".kazma" / "agent-skills" / name
    removed_from: list[str] = []
    for path in (target, alt):
        if path.is_dir() and (path / "SKILL.md").is_file():
            shutil.rmtree(path)
            removed_from.append(str(path))
    if not removed_from:
        return InstallResult(
            success=False,
            message="Skill not found",
            source=name,
            errors=[f"No installed skill named {name!r} under {dest}"],
        )
    return InstallResult(
        success=True,
        message=f"Uninstalled {name}",
        installed=[{"name": name, "path": p} for p in removed_from],
        source=name,
    )
