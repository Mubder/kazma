"""Kazma CLI main entry point."""

from __future__ import annotations

import sys

from kazma_cli.banner import check_config, show_banner, show_help_brief, show_status


def main() -> None:
    """CLI entry point — supports wizard, hub, and docs commands."""
    # Parse --no-banner early (consume it if present)
    show_banner_flag = True
    args = sys.argv[1:]
    filtered: list[str] = []
    for a in args:
        if a == "--no-banner":
            show_banner_flag = False
        else:
            filtered.append(a)

    # No subcommand → startup experience
    if not filtered:
        _print_startup(show_banner_flag)
        return

    cmd = filtered[0]
    # Rebuild argv so subcommand handlers see a clean list
    sys.argv = [sys.argv[0]] + filtered

    if cmd == "status":
        print("Kazma status: OK")

    elif cmd == "serve":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        _run_serve(port)

    elif cmd == "wizard":
        _run_wizard()

    elif cmd == "hub":
        _run_hub(sys.argv[2:])

    elif cmd == "docs":
        _run_docs(sys.argv[2:])

    elif cmd in ("--help", "-h", "help"):
        print("Kazma CLI v0.1.0")
        print("Commands:")
        print("  status     Show Kazma status")
        print("  serve      Start the WebUI server (default port 8000)")
        print("  wizard     Start interactive skill installation wizard")
        print("  hub        Kazma Hub commands (search, install, list, etc.)")
        print("  docs       Documentation commands (build, serve)")
        print("")
        print("Options:")
        print("  serve [port]  Start server on specified port (default: 8000)")

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'kazma help' for available commands.")
        sys.exit(1)


def _run_serve(port: int) -> None:
    """Start the Kazma WebUI server."""
    try:
        import uvicorn
        from kazma_ui.app import create_app
    except ImportError as e:
        print(f"Error: WebUI dependencies not installed: {e}")
        print("Install with: pip install 'kazma[ui]' or pip install jinja2 python-multipart")
        sys.exit(1)

    app = create_app()
    print(f"Starting Kazma WebUI on http://0.0.0.0:{port}")
    print("Press Ctrl+C to stop")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", timeout_graceful_shutdown=15)


def _run_wizard() -> None:
    """Run the interactive skill installation wizard."""
    from kazma_core.cli.wizard import SkillInstallationWizard

    wizard = SkillInstallationWizard()
    success = wizard.run_sync()
    sys.exit(0 if success else 1)


def _run_hub(args: list[str]) -> None:
    """Run hub commands."""
    from kazma_core.hub.cli import hub as hub_cli

    hub_cli(args, standalone_mode=True)


def _run_docs(args: list[str]) -> None:
    """Run documentation commands."""
    if not args:
        print("Usage: kazma docs <build|serve>")
        return

    subcmd = args[0]

    if subcmd == "build":
        _docs_build()

    elif subcmd == "serve":
        port = int(args[1]) if len(args) > 1 else 3000
        _docs_serve(port)

    else:
        print(f"Unknown docs command: {subcmd}")
        print("Available: build, serve")


def _docs_build() -> None:
    """Build documentation site."""
    import subprocess
    from pathlib import Path

    docs_dir = Path(__file__).parent.parent.parent / "docs"
    if not docs_dir.exists():
        print("Error: docs/ directory not found")
        sys.exit(1)

    print("Installing dependencies...")
    result = subprocess.run(
        ["npm", "install"],
        cwd=str(docs_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"npm install failed: {result.stderr}")
        sys.exit(1)

    print("Building documentation...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(docs_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Build failed: {result.stderr}")
        sys.exit(1)

    print("Documentation built successfully!")


def _docs_serve(port: int = 3000) -> None:
    """Serve documentation locally."""
    import subprocess
    from pathlib import Path

    docs_dir = Path(__file__).parent.parent.parent / "docs"
    if not docs_dir.exists():
        print("Error: docs/ directory not found")
        sys.exit(1)

    print(f"Starting documentation server on port {port}...")
    subprocess.run(
        ["npm", "run", "start", "--", "--port", str(port)],
        cwd=str(docs_dir),
    )


def _print_startup(show_banner_flag: bool = True) -> None:
    """Print the full startup experience: banner, status, config checks, help hint."""
    # 1. Banner
    print(show_banner(suppress=not show_banner_flag))

    # 2. Config checks
    warnings = check_config()
    if warnings:
        for w in warnings:
            print(w)
        print()

    # 3. Status overview
    print(show_status())

    # 4. Quick help hint
    print(show_help_brief())


if __name__ == "__main__":
    main()
