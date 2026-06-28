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

    elif cmd == "completion":
        _run_completion(sys.argv[2:])

    elif cmd == "project":
        _run_project(sys.argv[2:])

    elif cmd in ("--help", "-h", "help"):
        print("Kazma CLI v0.1.0")
        print("Commands:")
        print("  status     Show Kazma status")
        print("  serve      Start the WebUI server (default port 8000)")
        print("  wizard     Start interactive skill installation wizard")
        print("  hub        Kazma Hub commands (search, install, list, etc.)")
        print("  docs       Documentation commands (build, serve)")
        print("  completion Shell tab completion (bash, zsh, powershell, install)")
        print("  project    Project-level config (.kazma/) — init, show, validate")
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


def _run_completion(args: list[str]) -> None:
    """Handle 'kazma completion' subcommands.

    Usage:
        kazma completion bash            — print bash completion script
        kazma completion zsh             — print zsh completion script
        kazma completion powershell      — print PowerShell completion script
        kazma completion install [bash|zsh|powershell]  — auto-install
        kazma completion --list-models   — print available model names
    """
    from kazma_cli.completions import generate_completions, install_completion, list_available_models

    if not args:
        print("Usage: kazma completion <bash|zsh|powershell|install>")
        print("")
        print("  bash         Print bash completion script")
        print("  zsh          Print zsh completion script")
        print("  powershell   Print PowerShell completion script")
        print("  install      Auto-detect shell and install completion")
        print("  install bash         Install bash completion")
        print("  install zsh          Install zsh completion")
        print("  install powershell   Install PowerShell completion")
        print("  --list-models        Print available model names")
        return

    subcmd = args[0]

    if subcmd == "--list-models":
        for model in list_available_models():
            print(model)
        return

    if subcmd == "bash":
        print(generate_completions("bash"))
        return

    if subcmd == "zsh":
        print(generate_completions("zsh"))
        return

    if subcmd in ("powershell", "pwsh", "ps"):
        print(generate_completions("powershell"))
        return

    if subcmd == "install":
        shell = args[1] if len(args) > 1 else _detect_shell()
        print(install_completion(shell))
        return

    print(f"Unknown completion command: {subcmd}")
    print("Available: bash, zsh, powershell, install")


def _run_project(args: list[str]) -> None:
    """Handle 'kazma project' subcommands.

    Usage:
        kazma project init       — create .kazma/ with default templates
        kazma project show       — display current project configuration
        kazma project validate   — check config validity
    """
    from kazma_cli.project import init_project, show_project, validate_project

    if not args:
        print("Usage: kazma project <init|show|validate>")
        return

    subcmd = args[0]

    if subcmd == "init":
        path = args[1] if len(args) > 1 else "."
        kazma_dir = init_project(path)
        print(f"Initialized .kazma/ project directory at {kazma_dir}")
        print("Created: rules.yaml, context.md, personality.yaml, tools.yaml, history/")

    elif subcmd == "show":
        path = args[1] if len(args) > 1 else "."
        print(show_project(path))

    elif subcmd == "validate":
        path = args[1] if len(args) > 1 else "."
        is_valid, issues = validate_project(path)
        if is_valid:
            print("Project config is valid.")
        else:
            print("Project config has issues:")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)

    else:
        print(f"Unknown project command: {subcmd}")
        print("Available: init, show, validate")


def _detect_shell() -> str:
    """Detect the current shell, defaulting to bash on POSIX / powershell on Windows."""
    import os
    import sys

    # On Windows, default to PowerShell
    if sys.platform == "win32":
        ps_module = os.environ.get("PSModulePath", "")
        if ps_module:
            return "powershell"
        return "powershell"

    shell_path = os.environ.get("SHELL", "")
    if "zsh" in shell_path:
        return "zsh"
    return "bash"


if __name__ == "__main__":
    main()
