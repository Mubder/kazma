"""Shell tab completion generators for the kazma CLI.

Provides bash and zsh completion scripts, auto-install, and dynamic
model-name completion for --model.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SUBCMDS = ["serve", "status", "help", "completion", "wizard", "hub", "docs", "project", "gateway", "swarm", "update"]
FLAGS = ["--model", "--provider", "--yolo", "--verbose", "--no-banner", "--help", "-h"]


def generate_completions(shell: str = "bash") -> str:
    """Generate a shell completion script for *shell* (``bash``, ``zsh``, or ``powershell``)."""
    if shell == "bash":
        return _bash_completion_script()
    if shell == "zsh":
        return _zsh_completion_script()
    if shell in ("powershell", "pwsh", "ps"):
        return _powershell_completion_script()
    raise ValueError(f"Unsupported shell: {shell}")


def install_completion(shell: str = "bash") -> str:
    """Write the completion script to the standard location for *shell*."""
    script = generate_completions(shell)

    if shell == "bash":
        target = _find_or_create_dir(
            [
                Path.home() / ".local" / "share" / "bash-completion" / "completions",
                Path.home() / ".bash_completion.d",
            ],
            Path.home() / ".local" / "share" / "bash-completion" / "completions",
        ) / "kazma"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        return (
            f"Bash completion installed to {target}\n"
            f"   Source it with:  source {target}"
        )

    if shell == "zsh":
        target = _find_or_create_dir(
            [
                Path.home() / ".zsh" / "completions",
                Path.home() / ".local" / "share" / "zsh" / "site-functions",
            ],
            Path.home() / ".zsh" / "completions",
        ) / "_kazma"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script)
        return (
            f"Zsh completion installed to {target}\n"
            f"   Ensure {target.parent} is in your fpath."
        )

    if shell in ("powershell", "pwsh", "ps"):
        # PowerShell profile directory (works for both Windows PowerShell and pwsh)
        target = _powershell_profile_dir() / "kazma_completion.ps1"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        profile_line = f". '{target}'"
        return (
            f"PowerShell completion installed to {target}\n"
            f"   Add this line to your $PROFILE:\n"
            f"       {profile_line}\n"
            f"   Or run:  kazma completion install powershell"
        )

    raise ValueError(f"Unsupported shell: {shell}")


def list_available_models() -> list[str]:
    """Return deduplicated, sorted model names from the registry or config."""
    try:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
        models = registry.get_discovered_models()
        if models:
            return sorted(set(models))
    except (RuntimeError, ImportError) as exc:
        logger.debug("Model registry unavailable for completions: %s", exc)

    # Fallback: read from ConfigStore/YAML
    models = []
    try:
        from kazma_cli.banner import _load_config

        config = _load_config()
        models_cfg = config.get("models", {})
        if isinstance(models_cfg, dict):
            for key in ("available", "model_list"):
                val = models_cfg.get(key, [])
                if isinstance(val, list):
                    models.extend(val)
            providers = models_cfg.get("providers", {})
            if isinstance(providers, dict):
                for provider_models in providers.values():
                    if isinstance(provider_models, list):
                        models.extend(provider_models)
        llm = config.get("llm", {})
        if isinstance(llm, dict) and "model" in llm:
            models.append(llm["model"])
    except Exception as exc:
        logger.debug("Config model list parse failed: %s", exc)

    if not models:
        models = ["deepseek-chat", "gpt-4o-mini", "claude-sonnet-4"]

    return sorted(set(models))


def list_available_providers() -> list[str]:
    """Return sorted provider names from the registry."""
    try:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
        providers = registry.list_providers()
        return sorted(p.get("name", "") for p in providers if p.get("name"))
    except (RuntimeError, ImportError):
        return ["deepseek", "openai", "anthropic", "google", "ollama"]


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

def _bash_completion_script() -> str:
    """Return a self-contained bash completion script."""
    subcmds_str = " ".join(SUBCMDS)
    flags_str = " ".join(FLAGS)

    return f"""# Kazma CLI bash completion
# Generated by: kazma completion bash

_kazma_completion() {{
    local cur prev words cword split
    _init_completion -s || return

    # Handle "kazma completion ..."
    for ((i=1; i < cword; i++)); do
        if [[ "${{words[i]}}" == "completion" ]]; then
            COMPREPLY=($(compgen -W "bash zsh install" -- "$cur"))
            return
        fi
    done

    # --model → dynamic model list
    if [[ "$prev" == "--model" ]]; then
        local models
        models=$(kazma completion --list-models 2>/dev/null || true)
        if [[ -n "$models" ]]; then
            COMPREPLY=($(compgen -W "$models" -- "$cur"))
        fi
        return
    fi

    # --provider → dynamic provider list
    if [[ "$prev" == "--provider" ]]; then
        local providers
        providers=$(kazma completion --list-providers 2>/dev/null || true)
        if [[ -n "$providers" ]]; then
            COMPREPLY=($(compgen -W "$providers" -- "$cur"))
        fi
        return
    fi

    # Current word starts with '-' → flags
    if [[ "$cur" == -* ]]; then
        COMPREPLY=($(compgen -W "{flags_str}" -- "$cur"))
        return
    fi

    # Otherwise → subcommands
    COMPREPLY=($(compgen -W "{subcmds_str}" -- "$cur"))
}}

complete -F _kazma_completion kazma
"""


# ---------------------------------------------------------------------------
# Zsh
# ---------------------------------------------------------------------------

def _zsh_subcmd_descs() -> list[str]:
    """Return zsh-style subcommand descriptions (``"name[desc]"``)."""
    return [
        "serve[Start the WebUI server]",
        "status[Show Kazma status]",
        "help[Show help text]",
        "completion[Manage shell completions]",
        "wizard[Interactive skill installation wizard]",
        "hub[Kazma Hub commands]",
        "docs[Documentation commands]",
        "project[Project-level config]",
        "gateway[Gateway control]",
        "swarm[Swarm orchestration]",
        "update[Check for and install CLI updates]",
    ]


def _zsh_completion_script() -> str:
    """Return a self-contained zsh completion script."""
    subcmd_descs = "\n        ".join(_zsh_subcmd_descs())

    return f"""#compdef kazma

# Kazma CLI zsh completion
# Generated by: kazma completion zsh

_kazma() {{
    local -a flags
    flags=(
        '--model[Model name to use]:model:->models'
        '--provider[Provider to use]:provider:->providers'
        '--yolo[Skip confirmation prompts]'
        '--verbose[Enable verbose output]'
        '--no-banner[Suppress startup banner]'
        '--help[Show help]'
        '-h[Show help]'
    )

    _arguments -C \\
        $flags \\
        '1: :_kazma_subcmds' \\
        '*:: :->args'

    case $state in
        models)
            local -a model_list
            model_list=($(kazma completion --list-models 2>/dev/null || echo "gpt-4o gpt-4o-mini claude-sonnet-4 deepseek-chat"))
            _describe 'model' model_list
            ;;
        providers)
            local -a provider_list
            provider_list=($(kazma completion --list-providers 2>/dev/null || echo "openai deepseek anthropic"))
            _describe 'provider' provider_list
            ;;
    esac
}}

_kazma_subcmds() {{
    local -a subcommands
    subcommands=(
        {subcmd_descs}
    )
    _describe 'subcommand' subcommands
}}

_kazma "$@"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_or_create_dir(candidates: list[Path], fallback: Path) -> Path:
    """Return the first existing candidate directory, or *fallback*."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback


def _powershell_profile_dir() -> Path:
    """Return the directory where PowerShell expects profile scripts.

    Uses the ``PROFILE`` env var when available (set inside a PowerShell
    session), otherwise falls back to the standard user documents location.
    Works on Windows PowerShell (5.x) and PowerShell Core (7.x, ``pwsh``).
    """
    profile_env = Path.home() / "Documents" / "WindowsPowerShell"
    # PowerShell Core (pwsh) uses a different folder name
    pwsh_profile = Path.home() / "Documents" / "PowerShell"
    if pwsh_profile.exists():
        profile_env = pwsh_profile
    return profile_env


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------

def _powershell_completion_script() -> str:
    """Return a self-contained PowerShell completion script."""
    subcmds_str = " ".join(SUBCMDS)
    flags_str = " ".join(FLAGS)

    return f"""# Kazma CLI PowerShell completion
# Generated by: kazma completion powershell

Register-ArgumentCompleter -Native -CommandName kazma -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)

    $subcommands = '{subcmds_str}' -split '\\s+'
    $flags = '{flags_str}' -split '\\s+'

    # If completing a flag (starts with -)
    if ($wordToComplete -match '^--?.*') {{
        $flags | Where-Object {{ $_ -like "$wordToComplete*" }} |
            ForEach-Object {{
                [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
            }}
        return
    }}

    # If the previous word is --model, complete with model names
    $tokens = $commandAst.CommandElements
    $prev = ''
    foreach ($t in $tokens) {{
        if ($t.Extent.StartOffset -lt $cursorPosition) {{
            $prev = $t.Value
        }}
    }}
    if ($prev -eq '--model') {{
        $models = (kazma completion --list-models 2>$null) -split "`n"
        if (-not $models) {{
            $models = @('gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4', 'deepseek-chat')
        }}
        $models | Where-Object {{ $_ -like "$wordToComplete*" }} |
            ForEach-Object {{
                [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
            }}
        return
    }}

    if ($prev -eq '--provider') {{
        $providers = (kazma completion --list-providers 2>$null) -split "`n"
        if (-not $providers) {{
            $providers = @('openai', 'deepseek', 'anthropic')
        }}
        $providers | Where-Object {{ $_ -like "$wordToComplete*" }} |
            ForEach-Object {{
                [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
            }}
        return
    }}

    # Otherwise complete subcommands
    $subcommands | Where-Object {{ $_ -like "$wordToComplete*" }} |
        ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }}
}}
"""



