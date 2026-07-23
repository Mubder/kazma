# Portability Guarantees

Kazma runs on any Unix machine (Linux, macOS, WSL) **or Windows** with zero modifications.
These are the rules that keep it that way.

## Invariants

1. **All user-state lives under `~/.kazma/`**
   `Path(...).expanduser()` resolves `~` to the correct home directory on any machine.
   Examples: `~/.kazma/hub/registry.db`, `~/.kazma/skills/`, `~/.kazma/vector_memory/`

2. **All data paths are relative to the repo root**
   Defaults like `kazma-data/settings.db` resolve against CWD.
   `run.sh` and the Dockerfile both anchor CWD to the repo root before launching.

3. **Every path is overridable via `kazma.yaml` or env var**
   Defaults are just defaults. Configure `storage.path`, `skills.path`,
   `KAZMA_DATA_DIR`, etc. for your deployment.

4. **No hardcoded home directories**
   Never write `/home/user/...` or `/Users/...` in shipped code.
   Use `Path.home()` or `os.path.expanduser("~")` instead.

5. **No OS-specific imports**
   Importing `platform`, `sys.platform` branching, or OS-specific modules
   (`_winreg`, `fcntl`) is forbidden.

6. **No architecture assumptions**
   No `amd64`/`arm64`/`x86` conditionals. Python + pip handle this.

7. **No committed binary blobs**
   Model weights live on Hugging Face Hub, not in git.
   Stubs under 1KB are acceptable as placeholders.

8. **Config over code**
   External service URLs (LLM providers, adapters, tracing backends) go in
   `kazma.yaml` or `.env`, never hardcoded into Python.

## Verifying

```bash
# Check for portability violations
./scripts/ci/lint-absolute-paths.sh

# Run on a clean machine
python -m pytest tests/ -q
```

## Deployment Targets

| Target        | How                                                  |
| :-----------: | :--------------------------------------------------- |
| Bare-metal Linux | `./setup.sh && ./run.sh`                          |
| macOS         | `pip install -e '.' && python -m uvicorn ...`        |
| WSL2          | `./setup.sh` (auto-detects WSL)                      |
| Windows       | `setup.ps1` + `uv run kazma-web` (native, no WSL)    |
| Docker        | `docker compose up`                                  |
| Kubernetes    | Use `kazma.yaml` with env overrides, no code changes |

> **IDE subsystem (v0.5.0):** the `kazma_core/ide/` coding backend, the Web IDE page (`/ide`), the TUI editor, and the `/ide` commands were designed cross-platform from the start and run identically on Windows and Unix. The IDE layer uses no `sys.platform` branching, so it obeys the same portability invariants below.

See [scripts/ci/lint-absolute-paths.sh](../scripts/ci/lint-absolute-paths.sh) for the
automated check, and [.github/workflows/ci.yml](../.github/workflows/ci.yml) for how
it runs in CI.
