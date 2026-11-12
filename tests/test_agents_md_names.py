"""Every code name AGENTS.md cites exists (2026-11, FA-152 and FA-154).

AGENTS.md is what a change is checked against, and the functional audit of
2026-11 found it naming functions that were renamed or removed: the
protected-file list guarded chat.js ``_serverGates`` (the code has
``_serverGatesAuth`` and ``_serverGateViews``) and ``_paintHitlFromDoc``
(gone), §12A named ``_ensure_streaming_graph`` (``get_streaming_graph``),
§19 ``pdf_layout.segments_around_tables`` (private), §38
``hardening._project_files`` (never existed). A reader looking for the thing
to protect found nothing.

Five shapes are read from every backticked span of AGENTS.md:

* ``module.name`` and dotted paths (``kazma_core.memory.forget.forget_episode``,
  ``memory.forget.rewind_memory``, ``agent_runner._ensure_graph``): the
  longest leading part that names a product module is the module, and the
  rest must be defined there -- at top level, as a class's member, as a
  function nested in it, or as a submodule. A span under a package's name
  that the package does not define (``memory.enabled``, ``cron.timezone``,
  ``backup.files_failed``) is a settings or alert key, not code.
* ``path/file.py:name``: the file must exist and define the name.
* A camelCase name (``renderTurn``, ``_hitlAlreadyClaimed``): it must occur in
  a page script or template, outside a comment.
* A bare private name (``_ensure_graph``, ``_time_travel_truncated``): some
  tracked Python file defines or assigns it, or holds it as a string key, or
  a page script names it.
* A test (``tests/test_x.py::test_y``, or a bare ``test_y``): the file defines
  it at top level (a test, a class or a constant), or some tracked test file
  does.

What reads like code and is not (an alert key whose first word is also a
module name, a settings key, a log event) is declared in ``NOT_CODE`` with
why; a declaration that matches nothing in AGENTS.md fails, so the list
cannot keep a stale entry either. Planted stale names are the negative
control.
"""

from __future__ import annotations

import ast
import functools
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
PACKAGES = (
    "kazma-core/kazma_core",
    "kazma-ui/kazma_ui",
    "kazma-gateway/kazma_gateway",
    "kazma-cli/kazma_cli",
    "kazma-tui/kazma_tui",
    "kazma-skills/kazma_skills",
)
SCRIPT_ROOTS = ("kazma-ui/kazma_ui/static", "kazma-ui/kazma_ui/templates")

#: Spans that look like code and are something else. Each says what it is.
NOT_CODE: dict[str, str] = {
    "chat.models": "a settings key (kazma_core.chat_model_choice.KEY), not the kazma_ui.chat module",
    "health.dependency_down": "a guard event in guard.log",
    "health.needs_setup": "a guard event in guard.log",
    "health.setup_done": "a guard event in guard.log",
    "health.ready_again": "a guard event and alert key (guard.log, the alert catalog)",
    "health.wait_over": "a guard event in guard.log",
    "health.waiting_for": "a guard event in guard.log",
    "install.requirements_unmet": "an ops-alert key (not the kazma_cli.install module)",
    "setup.cmd": "the Windows setup batch file at the repository root (not kazma_cli.setup)",
    "turn.stuck_open": "an ops-alert key",
    "turn.timed_out": "an ops-alert key",
    "notify.failed": "a guard event (the pager)",
    "notify.sent": "a guard event (the pager)",
    "notify.skipped": "a guard event (the pager)",
    "runs.progress": "the engine event a swarm task's progress stamps",
    "maintenance.active": "a guard event",
    "maintenance.resumed": "a guard event",
    "gateway.rate_limits": "a settings key",
    "gateway.suggestions.enabled": "a settings key",
    "gateway.start": "GatewayManager.start, named as the gateway's own step",
    "gateway.stop": "GatewayManager.stop, named as the gateway's own step",
    "graph.ainvoke": "LangGraph's own call on a compiled graph",
    "state.get": "a dict read on the graph state",
    "store.transact": "SessionManager.transact, named through its receiver",
    "system.start": "Chroma's own system object",
    "task.workers": "a SwarmTask field",
    "worker.dispatch": "SwarmWorker.dispatch, named through its receiver",
    "safety.check": "the swarm bus's SafetyGate.check, named through its receiver",
    "jobs.enqueue": "the document job store's enqueue, named through its receiver",
    "repository.gc_mark": "the document repository's gc_mark, named through its receiver",
    "llm_provider.chat": "LLMProvider.chat, named through its module",
    "postgres_pool.execute": "a stall-dump frame named by module and function",
    "vault.store": "SecretVault.store, named through its receiver",
    "agent.is_running": "the Agents page's old flag, named as removed",
    "sessions.messages": "a SQLite column",
    "workspaces.root_path": "a SQLite column",
    "entities.embedding": "a SQLite column",
    "entities.metadata_json": "a SQLite column",
    "voice.webm": "a file name",
    "documents.parsers": "the parser package, named in a list of what the UI must not import",
    "documents.ocr": "the OCR package, named in a list of what the UI must not import",
    "documents.renderers": "the renderer package, named in a list of what the UI must not import",
    "acquisition.canary_failed": "an ops-alert key",
    "acquisition.canary_recovered": "an ops-alert key",
    "acquisition.monitor_recovered": "an ops-alert key",
    "time_travel.prune_not_sticking": "an ops-alert key",
    "context.max_context_tokens": "a settings key",
    "documents.capacity.max_concurrent_uploads": "a settings key",
    "notifications.lifecycle.events": "a settings key",
    "notifications.ops.channels": "a settings key",
    "notifications.ops.notices_to_chats": "a settings key",
    "notifications.watch.email_to": "a settings key",
    "providers.list": "a settings key",
    "self_improvement.agent_evolution": "a settings key",
    "safety.hitl.require_approval_for": "a kazma.yaml key",
    "auth.value": "an MCP server's configuration field",
    "addMapping": "ReportLab's own function",
    "httpHostHeader": "a cloudflared setting",
    "xLoaded": "a placeholder for a page's own loaded flag",
    "confirmLabel": "the option a page passed that no dialog read, named as the bug",
    "dual_write.mirror_belief": "named as deleted",
    "graph_builder._failover_clients": "named as the cache that moved (the bug's own name)",
    "_save_config": "named as removed",
    "documents.worker.lease_seconds": "a legacy settings alias",
    "auth.oidc.pending": "a settings key",
    "ensureProgressPanel": "named as deleted",
    "tokenAccum": "named as the dual paint that must not come back",
}

_SPAN = re.compile(r"`([^`\n]+)`")
_DOTTED = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?:\(.*\))?$")
_FILE_NAME = re.compile(r"^([\w/]+\.py):([A-Za-z_]\w*)(?:\(.*\))?$")
_CAMEL = re.compile(r"^_?[a-z][a-z0-9]*[A-Z]\w*(?:\(.*\))?$")
_PRIVATE = re.compile(r"^_[a-z][a-z0-9_]*(?:\(.*\))?$")
_TEST_IN_FILE = re.compile(r"^(tests/[\w/]+\.py)::(\w+)(?:[.:\[].*)?$")
_BARE_TEST = re.compile(r"^(test_\w+)(?:\[.*\])?$")
_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE_COMMENT = re.compile(r"(?:^|(?<=\s))//[^\n]*")
_FILE_SUFFIXES = {
    "py", "db", "yaml", "yml", "json", "md", "js", "html", "toml", "txt", "log",
    "env", "css", "sh", "ps1", "webm", "wav", "mp4", "pdf", "docx", "xlsx", "npz",
    "jsonl", "sqlite3", "dump", "zip", "exe", "pass", "token",
}


@functools.cache
def _module_files() -> dict[tuple[str, ...], tuple[Path, ...]]:
    """Every dotted suffix of every product module -> its files."""
    index: dict[tuple[str, ...], list[Path]] = {}
    for pkg in PACKAGES:
        base = ROOT / pkg
        for path in base.rglob("*.py"):
            rel = path.relative_to(base.parent).with_suffix("")
            parts = rel.parts if rel.name != "__init__" else rel.parts[:-1]
            for start in range(len(parts)):
                index.setdefault(tuple(parts[start:]), []).append(path)
    return {key: tuple(paths) for key, paths in index.items()}


@functools.cache
def _definitions(path: Path) -> tuple[frozenset[str], dict[str, frozenset[str]], frozenset[str]]:
    """Top-level names of *path*, each class's member names, and every
    function or class defined anywhere in it (a closure such as
    ``graph_builder._supervisor``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top: set[str] = set()
    members: dict[str, frozenset[str]] = {}
    nested = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )

    def bind(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top.add(node.name)
        elif isinstance(node, ast.ClassDef):
            top.add(node.name)
            names: set[str] = set()
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(item.name)
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
                        elif isinstance(target, ast.Attribute):
                            names.add(target.attr)
            members[node.name] = frozenset(names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        top.add(name.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                top.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for child in ast.iter_child_nodes(node):
                bind(child)
            for handler in getattr(node, "handlers", ()):
                for child in handler.body:
                    bind(child)

    for node in tree.body:
        bind(node)
    return frozenset(top), members, nested


def _defined_in(path: Path, names: list[str]) -> bool:
    top, members, nested = _definitions(path)
    first = names[0]
    if first in top:
        if len(names) == 1 or first not in members:
            return True
        return names[1] in members[first]
    # A method or attribute named through its module (``agent_runner._ensure_graph``),
    # or a closure (``graph._handler_body``).
    if any(first in names_of_class for names_of_class in members.values()):
        return True
    if len(names) == 1 and first in nested:
        return True
    # A submodule of a package (``kazma_core.memory`` + ``forget``).
    if path.name == "__init__.py":
        return (path.parent / f"{first}.py").is_file() or (path.parent / first).is_dir()
    return False


def dotted_problem(dotted: str) -> str | None:
    """Why *dotted* names nothing, or None (it resolves, or is not code)."""
    parts = dotted.split(".")
    if parts[-1] in _FILE_SUFFIXES:
        return None
    index = _module_files()
    for cut in range(len(parts), 0, -1):
        files = index.get(tuple(parts[:cut]))
        if not files:
            continue
        rest = parts[cut:]
        if cut == 1 and not parts[0].startswith("kazma_") and any(f.name == "__init__.py" for f in files):
            # ``memory.enabled``, ``cron.timezone``: a settings namespace that is
            # also a package. Only a module inside it would make it code.
            return None
        if not rest:
            return None
        camel = [segment for segment in rest if _CAMEL.match(segment)]
        if camel:
            # ``chat.applyJournalFrame``: a page script's name, through its object.
            return camel_problem(camel[-1])
        if any(_defined_in(path, rest) for path in files):
            return None
        if not parts[0].startswith("kazma_") and any(f.name == "__init__.py" for f in files):
            # ``documents.ocr.engine``, ``backup.files_failed``: a settings key or an
            # alert key under a package's name. A module inside it would have matched.
            return None
        return f"{'.'.join(parts[:cut])} defines no {'.'.join(rest)}"
    return None


def file_name_problem(rel: str, name: str) -> str | None:
    suffix = tuple(Path(rel).with_suffix("").parts)
    files = _module_files().get(suffix)
    if not files:
        return f"no product file {rel}"
    if any(_defined_in(path, [name]) for path in files):
        return None
    return f"{rel} defines no {name}"


@functools.cache
def _script_text() -> str:
    """Every page script and template, comments left out: a name that is
    only in a comment (``_paintHitlFromDoc``) is not code."""
    chunks = []
    for root in SCRIPT_ROOTS:
        for path in (ROOT / root).rglob("*"):
            if path.suffix in (".js", ".html") and "vendor" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="replace")
                text = _JS_BLOCK_COMMENT.sub(" ", text)
                chunks.append(_JS_LINE_COMMENT.sub(" ", text))
    return "\n".join(chunks)


@functools.cache
def _python_names() -> frozenset[str]:
    """Every name a tracked Python file defines, assigns, imports, takes as a
    parameter or keyword, or holds as an identifier-shaped string key."""
    listed = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    names: set[str] = set()
    for rel in listed:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((alias.asname or alias.name).split(".")[-1] for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                names.add(node.value)
    return frozenset(names)


@functools.cache
def _top_level_of(rel: str) -> frozenset[str]:
    path = ROOT / rel
    if not path.is_file():
        return frozenset()
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return frozenset(names)


@functools.cache
def _test_names() -> frozenset[str]:
    listed = subprocess.run(
        ["git", "ls-files", "*test*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    names: set[str] = set()
    for rel in listed:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue
        names.update(
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    return frozenset(names)


def cited_test_problem(rel: str | None, name: str) -> str | None:
    if rel is not None:
        if not (ROOT / rel).is_file():
            return f"no test file {rel}"
        return None if name in _top_level_of(rel) else f"{rel} defines no {name}"
    return None if name in _test_names() else f"no tracked test is named {name}"


def private_problem(name: str) -> str | None:
    bare = name.split("(")[0]
    if bare in _python_names():
        return None
    if re.search(rf"(?<![\w$]){re.escape(bare)}(?![\w$])", _script_text()):
        return None
    return f"nothing tracked defines or names {bare}"


def camel_problem(name: str) -> str | None:
    bare = name.split("(")[0]
    if re.search(rf"(?<![\w$]){re.escape(bare)}(?![\w$])", _script_text()):
        return None
    return f"no page script or template names {bare}"


@functools.cache
def _catalog_keys() -> frozenset[str]:
    from kazma_ui.i18n import TRANSLATIONS

    return frozenset(TRANSLATIONS)


def problems(text: str) -> list[str]:
    found: list[str] = []
    for span in _SPAN.findall(text):
        span = span.strip()
        bare = span.split("(")[0]
        if bare in NOT_CODE or bare in _catalog_keys():
            continue
        file_match = _FILE_NAME.match(span)
        test_match = _TEST_IN_FILE.match(span)
        if test_match:
            why = cited_test_problem(test_match.group(1), test_match.group(2))
        elif _BARE_TEST.match(span):
            why = cited_test_problem(None, _BARE_TEST.match(span).group(1))
        elif file_match:
            why = file_name_problem(file_match.group(1), file_match.group(2))
        elif _DOTTED.match(span):
            why = dotted_problem(_DOTTED.match(span).group(1))
        elif _CAMEL.match(span):
            why = camel_problem(span)
        elif _PRIVATE.match(span):
            why = private_problem(span)
        else:
            why = None
        if why:
            found.append(f"`{span}`: {why}")
    return sorted(set(found))


def test_every_code_name_agents_md_cites_exists():
    found = problems(AGENTS.read_text(encoding="utf-8"))
    assert not found, (
        "AGENTS.md names code that does not exist; correct the sentence, or declare a span "
        "that is not code in NOT_CODE with why:\n" + "\n".join(found)
    )


def test_every_declaration_matches_a_span():
    text = AGENTS.read_text(encoding="utf-8")
    stale = [key for key in NOT_CODE if f"`{key}`" not in text and f"`{key}(" not in text]
    assert not stale, f"NOT_CODE entries AGENTS.md no longer has: {stale}"


@pytest.mark.parametrize(
    "planted",
    [
        "the protected-file list names chat.js `_serverGates`",
        "the streaming graph (`_ensure_streaming_graph`)",
        "tables first (`pdf_layout.segments_around_tables`)",
        "one pruning walk, `hardening._project_files`",
        "the gate is `graph_tool_worker.py:tool_gate_node`",
        "the painter `_paintHitlFromDoc`",  # only in a comment now
        "a renamed `kazma_core.memory.forget.forget_everything`",
        "under pytest via `_skip_real_adapters`",
        "`crawl_jobs.Job.finish`",
        "`test_the_memory_path_reaches_no_helper_through_another_module`",
        "`tests/test_static_gates.py::test_no_such_gate`",
    ],
)
def test_negative_control_a_planted_stale_name_is_found(planted: str):
    assert problems(planted), planted


@pytest.mark.parametrize(
    "fine",
    [
        "`memory.enabled`",  # a settings key
        "`cron.timezone`",
        "`kazma.yaml`",
        "`get_streaming_graph`",  # a bare name is not checked
        "`agent_runner._ensure_graph`",  # a method named through its module
        "`memory.forget.rewind_memory`",
        "`graph_tool_worker.py:tool_worker_node`",
        "`renderTurn`",
        "`graph._handler_body`",  # a closure
        "`_time_travel_truncated`",  # a string key
        "`_ensure_graph`",
        "`tests/test_swarm_boundaries_and_signals.py::_QUIET_HANDLERS`",
        "`test_an_area_reaches_no_helper_through_another_module`",
    ],
)
def test_real_names_and_non_code_pass(fine: str):
    assert not problems(fine), problems(fine)
