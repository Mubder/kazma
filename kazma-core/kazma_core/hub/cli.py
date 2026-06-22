"""Kazma Hub — CLI commands for skill registry management."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
import yaml

from kazma_core.hub.manifest_schema import SkillManifest
from kazma_core.hub.registry import KazmaHub
from kazma_core.hub.validator import SkillValidator

# ─── Helpers ──────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine from a sync click command."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio

            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _format_table(headers: list[str], rows: list[list[str]], col_widths: list[int]) -> str:
    """Format rows into a fixed-width table."""
    if not rows:
        return ""

    lines = []
    header_line = "".join(h.ljust(w) for h, w in zip(headers, col_widths))
    lines.append(header_line)
    lines.append("-" * sum(col_widths))

    for row in rows:
        line = "".join(str(cell).ljust(w) for cell, w in zip(row, col_widths))
        lines.append(line)

    return "\n".join(lines)


def _http_request(
    method: str,
    url: str,
    *,
    json_data: dict | None = None,
    timeout: float = 10.0,
    retries: int = 3,
) -> dict | None:
    """Make an HTTP request with retry and timeout.

    Uses httpx for async-capable HTTP calls, wrapped synchronously.
    Returns parsed JSON dict or None on failure.
    """
    import time

    import httpx

    last_error = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                if method.upper() == "GET":
                    resp = client.get(url)
                elif method.upper() == "POST":
                    resp = client.post(url, json=json_data)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in (404, 422):
                return None
            if attempt < retries - 1:
                time.sleep(2**attempt)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2**attempt)
        except Exception as exc:
            last_error = exc
            break

    click.echo(f"HTTP error: {last_error}", err=True)
    return None


# ─── CLI Group ────────────────────────────────────────────────────────────


@click.group()
@click.option(
    "--registry-path",
    default="~/.kazma/hub/registry.db",
    help="Path to the SQLite registry database.",
    envvar="KAZMA_HUB_DB",
)
@click.option(
    "--hub-url",
    default=None,
    help="Hub API base URL (overrides KAZMA_HUB_URL env var).",
    envvar="KAZMA_HUB_URL",
)
@click.pass_context
def hub(ctx, registry_path: str, hub_url: str | None) -> None:
    """Kazma Hub — skill registry commands."""
    ctx.ensure_object(dict)
    ctx.obj["registry_path"] = registry_path
    ctx.obj["hub_url"] = hub_url or "https://hub.kazma.dev"


# ─── register ─────────────────────────────────────────────────────────────


@hub.command()
@click.argument("path", type=click.Path(exists=True))
@click.pass_context
def register(ctx, path: str) -> None:
    """Register a skill from a directory."""
    skill_dir = Path(path)
    manifest_path = skill_dir / "skill_manifest.yaml"

    if not manifest_path.exists():
        click.echo(f"Error: No skill_manifest.yaml found in {path}", err=True)
        sys.exit(1)

    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
        manifest = SkillManifest.from_dict(data)
        vr = manifest.validate()
        if not vr.passed:
            click.echo("Manifest validation failed:")
            for err in vr.errors:
                click.echo(f"  - {err}")
            sys.exit(1)
    except Exception as exc:
        click.echo(f"Error reading manifest: {exc}", err=True)
        sys.exit(1)

    # Register in hub
    hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])
    skill_id = _run(hub_instance.register(manifest))
    _run(hub_instance.close())

    click.echo(f"Registered: {skill_id}")


# ─── search ───────────────────────────────────────────────────────────────


@hub.command()
@click.argument("query", required=False, default=None)
@click.option("--capabilities", help="Filter by capabilities (comma-separated).")
@click.option("--tags", help="Filter by tags (comma-separated).")
@click.option("--author", help="Filter by author.")
@click.pass_context
def search(ctx, query: str | None, capabilities: str | None, tags: str | None, author: str | None) -> None:
    """Search for skills in the registry."""
    hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])

    cap_list = [c.strip() for c in capabilities.split(",")] if capabilities else None
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    results = _run(hub_instance.search(query=query, capabilities=cap_list, tags=tag_list, author=author))
    _run(hub_instance.close())

    if not results:
        click.echo("No skills found.")
        return

    headers = ["NAME", "AUTHOR", "VERSION", "DESCRIPTION"]
    col_widths = [24, 16, 10, 40]
    rows = []
    for m in results:
        d = m.data
        rows.append(
            [
                d.get("name", ""),
                d.get("author", ""),
                d.get("version", ""),
                (d.get("description", "") or "")[:40],
            ]
        )

    click.echo(_format_table(headers, rows, col_widths))


# ─── install ──────────────────────────────────────────────────────────────


@hub.command()
@click.argument("skill_id")
@click.pass_context
def install(ctx, skill_id: str) -> None:
    """Install a skill (marks as installed in registry)."""
    try:
        hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)

    try:
        manifest = _run(hub_instance.get(skill_id))
        if manifest is None:
            click.echo(f"Skill not found: {skill_id}")
            _run(hub_instance.close())
            return

        install_path = _run(hub_instance.install(skill_id))
        _run(hub_instance.close())
        click.echo(f"Installed {skill_id} to {install_path}")
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)


# ─── list ─────────────────────────────────────────────────────────────────


@hub.command(name="list")
@click.pass_context
def list_installed(ctx) -> None:
    """List installed skills."""
    hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])
    results = _run(hub_instance.list_installed())
    _run(hub_instance.close())

    if not results:
        click.echo("No installed skills.")
        return

    headers = ["NAME", "AUTHOR", "VERSION", "DESCRIPTION"]
    col_widths = [24, 16, 10, 40]
    rows = []
    for m in results:
        d = m.data
        rows.append(
            [
                d.get("name", ""),
                d.get("author", ""),
                d.get("version", ""),
                (d.get("description", "") or "")[:40],
            ]
        )

    click.echo(_format_table(headers, rows, col_widths))


# ─── info ─────────────────────────────────────────────────────────────────


@hub.command()
@click.argument("skill_id")
@click.pass_context
def info(ctx, skill_id: str) -> None:
    """Show detailed info about a skill."""
    try:
        hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)

    try:
        manifest = _run(hub_instance.get(skill_id))
        _run(hub_instance.close())
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)

    if manifest is None:
        click.echo(f"Skill not found: {skill_id}")
        return

    d = manifest.data
    capabilities = d.get("capabilities", [])
    cap_str = ", ".join(capabilities) if capabilities else "None"
    installed = "Yes" if d.get("installed_path") else "No"

    click.echo(f"Skill: {d.get('name')}@{d.get('version')}")
    click.echo(f"Author: {d.get('author')}")
    click.echo(f"License: {d.get('license')}")
    click.echo(f"Description: {d.get('description')}")
    click.echo(f"Capabilities: {cap_str}")
    click.echo(f"Installed: {installed}")


# ─── validate ─────────────────────────────────────────────────────────────


@hub.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
def validate(path: str, output_json: bool = False) -> None:
    """Validate a skill directory."""
    skill_dir = Path(path)
    validator = SkillValidator()
    result = _run(validator.validate(skill_dir))

    if output_json:
        click.echo(
            json.dumps(
                {
                    "path": str(path),
                    "passed": result.passed,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "score": result.score,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Validation Results for {path}")
    click.echo("=" * 40)

    # Group errors and warnings by type
    has_errors = bool(result.errors)
    has_warnings = bool(result.warnings)

    if has_errors:
        for err in result.errors:
            click.echo(f"[FAIL] {err}")

    if has_warnings:
        for warn in result.warnings:
            click.echo(f"[WARN] {warn}")

    if not has_errors and not has_warnings:
        click.echo("[PASS] Manifest structure valid")
        click.echo("[PASS] Entry point found")
        click.echo("[PASS] MCP servers configured")
        click.echo("[PASS] No security issues found")

    click.echo(f"Security Score: {result.score:.0f}/100")


# ─── uninstall ────────────────────────────────────────────────────────────


@hub.command()
@click.argument("skill_id")
@click.pass_context
def uninstall(ctx, skill_id: str) -> None:
    """Uninstall a skill from the registry."""
    try:
        hub_instance = KazmaHub(registry_path=ctx.obj["registry_path"])
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)

    try:
        removed = _run(hub_instance.unregister(skill_id))
        _run(hub_instance.close())
    except ValueError as exc:
        click.echo(f"Invalid skill ID: {exc}", err=True)
        sys.exit(1)

    if removed:
        click.echo(f"Uninstalled: {skill_id}")
    else:
        click.echo(f"Skill not found: {skill_id}")


# ─── submit ───────────────────────────────────────────────────────────────


@hub.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.option("--source-url", default="", help="Source repository URL.")
@click.pass_context
def submit(ctx, path: str, output_json: bool = False, source_url: str = "") -> None:
    """Submit a skill for certification.

    Reads skill_manifest.yaml from the given path, validates locally,
    then POSTs to the hub API. Returns the submission ID.
    """
    skill_dir = Path(path)
    manifest_path = skill_dir / "skill_manifest.yaml"

    if not manifest_path.exists():
        click.echo(f"Error: No skill_manifest.yaml found in {path}", err=True)
        sys.exit(1)

    # Load and validate manifest locally first
    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
        manifest = SkillManifest.from_dict(data)
        vr = manifest.validate()
        if not vr.passed:
            click.echo("Local validation failed:")
            for err in vr.errors:
                click.echo(f"  - {err}")
            sys.exit(1)
    except Exception as exc:
        click.echo(f"Error reading manifest: {exc}", err=True)
        sys.exit(1)

    hub_url = ctx.obj["hub_url"]
    url = f"{hub_url}/api/v1/skills/submit"
    payload = {
        "manifest": data,
        "source_url": source_url,
        "submitter_id": "cli-user",
    }

    result = _http_request("POST", url, json_data=payload)
    if result is None:
        click.echo("Failed to submit skill to hub.", err=True)
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Submission ID: {result['submission_id']}")
        click.echo(f"Skill ID:      {result['skill_id']}")
        click.echo(f"Status:        {result['status']}")
        click.echo(f"Message:       {result['message']}")


# ─── status ───────────────────────────────────────────────────────────────


@hub.command()
@click.argument("submission_id")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def status(ctx, submission_id: str, output_json: bool = False) -> None:
    """Check submission certification status.

    Queries the hub API for the status of a submission.
    """
    hub_url = ctx.obj["hub_url"]
    url = f"{hub_url}/api/v1/skills/{submission_id}/certification"

    result = _http_request("GET", url)
    if result is None:
        click.echo(f"No status found for submission: {submission_id}")
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Skill ID: {result.get('skill_id', 'N/A')}")
        click.echo(f"Level:    {result.get('level', 'none')}")
        if result.get("issued_at"):
            click.echo(f"Issued:   {result['issued_at']}")
        if result.get("expires_at"):
            click.echo(f"Expires:  {result['expires_at']}")
        reqs = result.get("requirements_met", {})
        if reqs:
            click.echo("Requirements:")
            for k, v in reqs.items():
                status_mark = "OK" if v else "FAIL"
                click.echo(f"  [{status_mark}] {k}")


# ─── badge ────────────────────────────────────────────────────────────────


@hub.command()
@click.argument("skill_ref")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def badge(ctx, skill_ref: str, output_json: bool = False) -> None:
    """View badge for a certified skill.

    Shows badge level, issued date, and requirements met.
    skill_ref should be 'author/skill-name'.
    """
    hub_url = ctx.obj["hub_url"]
    url = f"{hub_url}/api/v1/skills/kazma-hub://{skill_ref}/certification"

    result = _http_request("GET", url)
    if result is None:
        click.echo(f"No badge found for: {skill_ref}")
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(result, indent=2))
    else:
        level = result.get("level", "none")
        click.echo(f"Skill:     {skill_ref}")
        click.echo(f"Badge:     {level.upper()}")
        if result.get("issued_at"):
            click.echo(f"Issued:    {result['issued_at']}")
        if result.get("expires_at"):
            click.echo(f"Expires:   {result['expires_at']}")
        reqs = result.get("requirements_met", {})
        if reqs:
            click.echo("Requirements:")
            for k, v in reqs.items():
                status_mark = "MET" if v else "UNMET"
                click.echo(f"  [{status_mark}] {k}")


# ─── certified ────────────────────────────────────────────────────────────


@hub.command()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def certified(ctx, output_json: bool = False) -> None:
    """List all certified skills.

    Shows a table of certified skills with their badge levels.
    """
    hub_url = ctx.obj["hub_url"]
    url = f"{hub_url}/api/v1/skills?certified=true"

    result = _http_request("GET", url)
    if result is None:
        click.echo("Failed to fetch certified skills.")
        sys.exit(1)

    items = result.get("items", [])

    if output_json:
        click.echo(json.dumps(result, indent=2))
        return

    if not items:
        click.echo("No certified skills found.")
        return

    headers = ["NAME", "AUTHOR", "VERSION", "BADGE"]
    col_widths = [24, 16, 10, 12]
    rows = []
    for item in items:
        rows.append(
            [
                item.get("name", ""),
                item.get("author", ""),
                item.get("version", ""),
                "CERTIFIED" if item.get("certified") else "-",
            ]
        )

    click.echo(_format_table(headers, rows, col_widths))


# ─── stats ────────────────────────────────────────────────────────────────


@hub.command()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def stats(ctx, output_json: bool = False) -> None:
    """View hub statistics.

    Shows total skills, certified count, category breakdown,
    and recent submissions.
    """
    hub_url = ctx.obj["hub_url"]
    url = f"{hub_url}/api/v1/stats"

    result = _http_request("GET", url)
    if result is None:
        click.echo("Failed to fetch hub statistics.")
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(result, indent=2))
        return

    click.echo("Hub Statistics")
    click.echo("=" * 40)
    click.echo(f"Total Skills:     {result.get('total_skills', 0)}")
    click.echo(f"Certified Count:  {result.get('certified_count', 0)}")
    click.echo("")

    by_category = result.get("by_category", {})
    if by_category:
        click.echo("By Category:")
        for cat, count in sorted(by_category.items()):
            click.echo(f"  {cat}: {count}")


# ─── check-certification ──────────────────────────────────────────────────


@hub.command("check-certification")
@click.argument("path", type=click.Path(exists=True))
@click.option("--json", "output_json", is_flag=True, help="Output as JSON.")
def check_certification(path: str, output_json: bool = False) -> None:
    """Check certification requirements for a skill.

    Shows which certification requirements are met and unmet
    for the skill at the given path.
    """

    skill_dir = Path(path)
    manifest_path = skill_dir / "skill_manifest.yaml"

    if not manifest_path.exists():
        click.echo(f"Error: No skill_manifest.yaml found in {path}", err=True)
        sys.exit(1)

    # Validate manifest
    try:
        with open(manifest_path) as f:
            data = yaml.safe_load(f)
        manifest = SkillManifest.from_dict(data)
        vr = manifest.validate()
    except Exception as exc:
        click.echo(f"Error reading manifest: {exc}", err=True)
        sys.exit(1)

    # Run full validation
    validator = SkillValidator()
    val_result = _run(validator.validate(skill_dir))

    # Build requirements check
    requirements = {}

    # Manifest validation
    requirements["manifest_valid"] = vr.passed

    # Security lint
    requirements["security_lint_pass"] = val_result.passed and val_result.score >= 80

    # No critical vulnerabilities (score above threshold)
    requirements["no_critical_vulnerabilities"] = val_result.score >= 50

    # Check entry point exists
    entry_point = data.get("entry_point")
    if entry_point:
        module_path = entry_point.split(":")[0] if ":" in entry_point else entry_point
        ep_file = skill_dir / f"{module_path}.py"
        requirements["entry_point_exists"] = ep_file.exists()
    else:
        requirements["entry_point_exists"] = True  # no entry point declared = OK

    # Check description length
    description = data.get("description", "")
    requirements["description_complete"] = len(description) >= 20

    # Check license present
    license_val = data.get("license", "")
    requirements["license_present"] = bool(license_val and str(license_val).strip())

    if output_json:
        click.echo(
            json.dumps(
                {
                    "path": str(path),
                    "requirements": requirements,
                    "score": val_result.score,
                    "errors": val_result.errors,
                    "warnings": val_result.warnings,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Certification Check: {path}")
    click.echo("=" * 40)

    met_count = sum(1 for v in requirements.values() if v)
    total_count = len(requirements)

    for req_name, met in requirements.items():
        status_mark = "MET" if met else "UNMET"
        click.echo(f"  [{status_mark}] {req_name}")

    click.echo("")
    click.echo(f"Result: {met_count}/{total_count} requirements met")
    click.echo(f"Security Score: {val_result.score:.0f}/100")

    # Show which badge levels this qualifies for
    click.echo("")
    click.echo("Qualification:")
    if met_count >= 3 and val_result.score >= 80:
        click.echo("  [YES] basic badge")
    else:
        click.echo("  [NO]  basic badge")
    if val_result.score >= 90 and met_count >= total_count:
        click.echo("  [YES] standard badge")
    else:
        click.echo("  [NO]  standard badge")


# ─── Entry points ─────────────────────────────────────────────────────────


def main():
    """Kazma Hub CLI entry point."""
    hub(standalone_mode=True)


if __name__ == "__main__":
    main()
