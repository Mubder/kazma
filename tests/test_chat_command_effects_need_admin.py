"""A chat command that acts for everyone is a gateway admin's, or says why not
(2026-11-15).

``/x approve``, ``/x deny``, ``/x delete``, ``/x retry``, ``/x poll`` and
``/x roast`` published to, deleted from, drafted for and spent the read quota
of the owner's X account, open to any sender a chat app let in (every allowed
sender beside the admins in ``KAZMA_GATEWAY_ADMINS``, everyone with
``KAZMA_GATEWAY_OPEN_TO_EVERYONE``): ``slash_commands.changes_global_config``
had no ``/x`` branch. Now every ``/x`` word but help, ``list`` and
``subjects`` is admin-only.

The class: this file reads ``agent_handler/commands.py`` and finds every
branch of every ``_try_*_command`` handler (a test of ``sub``, ``cmd`` or
``action`` against words), and every call each branch makes. A call is local
(the reply, string and list work), a read (``READS``), or else it acts. A
branch that acts must be admin under ``changes_global_config`` for its words,
or be declared in ``DECLARED_OPEN`` with why any allowed sender may run it.
A new handler, a new branch or a new call the tables do not know is therefore
admin-only until someone says otherwise. Each rule has its control: the rule
without its ``/x`` branch, and a planted branch.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from kazma_gateway.slash_commands import changes_global_config

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "commands.py"

#: Each handler and the command it answers.
HEADS = {
    "_try_swarm_command": "/swarm",
    "_try_ide_command": "/ide",
    "_try_kb_command": "/kb",
    "_try_documents_command": "/documents",
    "_try_research_command": "/research",
    "_try_model_command": "/model",
    "_try_skill_command": "/skill",
    "_try_x_command": "/x",
}

#: The variables a handler tests its words with.
WORD_VARS = frozenset({"sub", "cmd", "action"})

#: Arguments after a branch's words: enough for every admin rule that needs
#: one (``/swarm config <x>``, ``/model default <x>``, ``/ide repo owner/repo``).
SAMPLE_ARGS = "owner/repo 1"

#: A handler whose code outside its word tests reads the rest of the command
#: itself, and acts on it: the commands it acts on there, each admin-only.
#: ``/skill <owner/repo>`` installs, like ``/skill install`` (it was open to
#: any allowed sender until 2026-11-15: this gate found it).
ROOT_SAMPLES: dict[str, tuple[str, ...]] = {
    "/skill": ("/skill owner/repo", "/skill https://github.com/owner/repo"),
}

#: Calls that act on nothing outside the reply: the chat's own reply, logs,
#: text and list work, Kazma's own pure helpers.
LOCAL_CALLS = frozenset({
    "_send", "_reply", "reply", "_send_model_reply", "_send_swarm_reply", "manager.send", "OutboundMessage",
    "_build_target_id", "TelegramAdapter.build_model_keyboard", "count_noun", "_slugify_library_id",
    "_find_worker_prompt_split", "parse_tweet_url", "safe_repo_dir_name", "tool_child_env", "clone_auth_env",
    "render_instruction", "store_errors", "_unresolved", "resolve", "_kb_acquisition_status", "_extract_swarm_task",
    "_sender_is_gateway_admin", "changes_global_config", "tenant_scope", "type", "format_ref",
    "TelegramAdapter.build_provider_keyboard", "_normalize_slash_text",
})
_BUILTINS = frozenset({"len", "str", "int", "isinstance", "getattr", "dict", "max", "min", "sum", "enumerate", "next",
                       "list", "sorted", "set", "tuple", "bool", "any", "all", "Path", "range", "repr", "float", "zip"})
_LOCAL_RECEIVERS = frozenset({"logger", "os", "re", "json", "time", "Path", "asyncio"})
_DATA_METHODS = frozenset({"join", "split", "strip", "get", "append", "lower", "upper", "partition", "startswith",
                           "endswith", "pop", "exists", "mkdir", "home", "items", "keys", "values", "replace",
                           "rstrip", "lstrip", "count", "extend", "add", "setdefault", "encode", "decode", "format",
                           "isdigit"})

#: Calls that read and change nothing.
READS = frozenset({
    "engine.get_worker", "ide.list_path", "ide.read_file", "ide.search", "list_coding_skills", "get_ide_service",
    "kb_store.list_libraries", "kb_store.get_library", "kb_store.library_id_for", "kb_index.search",
    "_kb_status_job",
    "get_knowledge_index", "get_knowledge_store", "kb_refusal",
    "svc.list_documents", "svc.job_status", "svc.jobs_for_document", "svc.get_content", "svc.pdf_info_document",
    "svc.search_library", "svc.health", "get_ingestion_service", "operator_for", "get_current_tenant_id",
    "resolve_tenant_id", "get_workspace_store", "get_workspace_store().get_active_workspace",
    "get_workspace_store().list_workspaces", "get_github_token", "list_agent_skills", "get_skill",
    "_x_read_command", "get_x_config", "get_reply_config", "XClient", "XClient(xcfg.credentials).get_tweet",
    "_get_visible_providers", "chat_model_for", "get_model_registry", "get_model_registry().get_active_profile",
})

#: Branches that act and are open to any allowed sender, with why.
DECLARED_OPEN: dict[tuple[str, ...], str] = {
    ("/swarm",): "dispatches a task; every tool a worker calls asks the approval bus (AGENTS.md §7B)",
    ("/swarm", "broadcast"): "dispatches a task; every tool a worker calls asks the approval bus (§7B)",
    ("/swarm", "pipeline"): "dispatches a task; every tool a worker calls asks the approval bus (§7B)",
    ("/swarm", "dispatch"): "dispatches a task; every tool a worker calls asks the approval bus (§7B)",
    ("/ide", "edit"): "IdeService writes through LocalToolRegistry.execute: file_write asks the approval bus (§10B)",
    ("/ide", "delete"): "IdeService deletes through LocalToolRegistry.execute: file_delete asks the bus (§10B)",
    ("/ide", "run"): "IdeService runs shell_exec through LocalToolRegistry.execute, which asks the bus (§10B)",
    ("/ide", "runfile"): "IdeService runs it through LocalToolRegistry.execute, which asks the bus (§10B)",
    ("/ide", "git"): "IdeService runs git through LocalToolRegistry.execute, which asks the bus (§10B)",
    ("/ide", "skill"): "sends a coding task to the swarm; its workers' tools ask the approval bus (§7B, §10C)",
    ("/ide", "swarm"): "sends a task to the swarm; its workers' tools ask the approval bus (§7B, §10C)",
    ("/documents", "convert"): "writes a new artifact the operator owns (§19 B4b); the document is unchanged",
    ("/documents", "redact"): "writes a new redacted artifact the operator owns; the original is unchanged",
    ("/research",): "a research run in this chat, as the agent's own research tool any allowed sender may ask for",
    ("/skill", "activate"): "arms a skill for this chat only (its session context)",
    ("/skill", "deactivate"): "clears this chat's armed skill only",
    ("/model",): "`/model <n|ref>` sets this chat's own model (providers stage 3), never the install's",
    ("/model", "reset"): "this chat's own model (providers stage 3), never the install's",
    ("/_models_select",): "this chat's own model (providers stage 3), never the install's",
}


def _word_test(test: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    """``(variable, words)`` *test* compares a word variable with, else None."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            found = _word_test(value)
            if found is not None:
                return found
        return None
    if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id in WORD_VARS
            and len(test.ops) == 1):
        return None
    var, right = test.left.id, test.comparators[0]
    if isinstance(test.ops[0], ast.Eq) and isinstance(right, ast.Constant) and isinstance(right.value, str):
        return var, (right.value,)
    if isinstance(test.ops[0], ast.In) and isinstance(right, ast.Tuple | ast.List | ast.Set):
        return var, tuple(e.value for e in right.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return None


def _callee(call: ast.Call) -> str:
    text = ast.unparse(call.func)
    if text.endswith("to_thread") and call.args:
        return ast.unparse(call.args[0])
    return text


def classify(callee: str) -> str:
    """``local``, ``read`` or ``acts``."""
    if callee in LOCAL_CALLS or callee in _BUILTINS or callee.startswith(("'", '"', "f'", 'f"', "lambda")):
        return "local"  # a lambda's own calls are visited where it stands
    receiver, _, attr = callee.rpartition(".")
    if receiver and (receiver.split(".")[0] in _LOCAL_RECEIVERS or attr in _DATA_METHODS):
        return "local"
    return "read" if callee in READS else "acts"


Path_ = tuple[tuple[str, tuple[str, ...]], ...]


def _narrow(path: Path_, var: str, words: tuple[str, ...]) -> Path_:
    """*path* inside a test of *var* against *words*: a second test of the
    same variable narrows its words (``sub == "dispatch"`` inside ``sub in
    (...)``), a test of another variable is a word after it."""
    for i, (seen, old) in enumerate(path):
        if seen == var:
            kept = tuple(w for w in words if w in old) or words
            return (*path[:i], (var, kept), *path[i + 1:])
    return (*path, (var, words))


def _exclude(path: Path_, var: str, words: tuple[str, ...]) -> Path_:
    """*path* in the ``else`` of that test: the variable's other words."""
    for i, (seen, old) in enumerate(path):
        if seen == var:
            kept = tuple(w for w in old if w not in words) or old
            return (*path[:i], (var, kept), *path[i + 1:])
    return path


def branches(source: str) -> dict[Path_, set[str]]:
    """``{((variable, words), ...): callees}`` for every handler in *source*;
    the first item is ``("head", (command,))``."""
    tree = ast.parse(source)
    found: dict[Path_, set[str]] = {}

    def visit(node: ast.AST, path: Path_) -> None:
        if isinstance(node, ast.If):
            test = _word_test(node.test)
            if test is not None:
                visit(node.test, path)
                for child in node.body:
                    visit(child, _narrow(path, *test))
                for child in node.orelse:
                    visit(child, _exclude(path, *test))
                return
        if isinstance(node, ast.Call):
            found.setdefault(path, set()).add(_callee(node))
        for child in ast.iter_child_nodes(node):
            visit(child, path)

    for fn in tree.body:
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef) and fn.name.startswith("_try_") \
                and fn.name.endswith("_command"):
            if fn.name not in HEADS:
                raise AssertionError(f"{fn.name}: a new chat command handler; name its command in HEADS")
            for stmt in fn.body:
                visit(stmt, (("head", (HEADS[fn.name],)),))
    return found


def _texts(path: Path_) -> list[tuple[tuple[str, ...], str]]:
    """Each (declared key, sample command text) a branch stands for. Every
    word of a test is checked (``/skill rm`` as ``/skill uninstall``); the
    key names each test by its first word."""
    combos: list[list[tuple[str, str]]] = [[]]
    for _var, words in path:
        combos = [[*c, (w, words[0])] for c in combos for w in words]
    out = []
    for combo in combos:
        if len(combo) > 1 and combo[1][0].startswith("/"):
            combo = combo[1:]  # `cmd == "/_models_select"`: the word is the command itself
        words = [w for w, _first in combo]
        key = tuple(first for _w, first in combo if first)
        out.append((key, " ".join([*words, SAMPLE_ARGS]) if len(words) > 1 else words[0]))
    return out


def findings(source: str, rule: Any = changes_global_config) -> list[str]:
    """Each branch that acts, open to any allowed sender, without a reason."""
    out = []
    for path, callees in branches(source).items():
        acting = sorted(c for c in callees if classify(c) == "acts")
        if not acting:
            continue
        head = path[0][1][0]
        if len(path) == 1 and head in ROOT_SAMPLES:
            # Outside every word test, where the handler reads the rest of
            # the command itself: what it does with ROOT_SAMPLES' arguments.
            out += [f"{text!r} calls {acting}" for text in ROOT_SAMPLES[head] if not rule(text)]
            continue
        for key, text in _texts(path):
            if not rule(text) and key not in DECLARED_OPEN:
                out.append(f"{text!r} calls {acting}")
    return sorted(set(out))


def test_every_acting_chat_command_is_admin_or_says_why() -> None:
    found = findings(SOURCE.read_text(encoding="utf-8"))
    assert not found, (
        "a chat command branch acts for everyone and any allowed sender may run it: make it admin in "
        "kazma_gateway.slash_commands.changes_global_config, classify its calls (READS), or declare it in "
        f"DECLARED_OPEN with why: {found}")


def test_no_declaration_outlives_its_branch() -> None:
    acting = set()
    for path, callees in branches(SOURCE.read_text(encoding="utf-8")).items():
        if any(classify(c) == "acts" for c in callees):
            acting |= {key for key, _text in _texts(path)}
    stale = sorted(set(DECLARED_OPEN) - acting)
    assert not stale, f"declared open but no such acting branch: {stale}"


def test_x_publishing_words_are_admin_and_reading_stays_open() -> None:
    for text in ("/x approve s1 r1", "/x deny s1 r1", "/x delete s1 r1", "/x retry s1", "/x poll",
                 "/x roast https://x.com/a/status/1 | hi", "/x something-new"):
        assert changes_global_config(text), text
    for text in ("/x", "/x help", "/x ?", "/x list", "/x subjects", "/xyz"):
        assert not changes_global_config(text), text


def test_negative_control_the_old_rule_fails() -> None:
    """The rule as it was: no ``/x`` branch, and ``/skill`` admin only for
    its install and uninstall words."""
    def old_rule(text: str) -> bool:
        parts = text.split()
        if parts[0].lower() == "/x":
            return False
        if parts[0].lower() == "/skill":
            return len(parts) > 1 and parts[1].lower() in ("install", "add", "uninstall", "remove", "rm")
        return changes_global_config(text)

    found = findings(SOURCE.read_text(encoding="utf-8"), rule=old_rule)
    for word in ("approve", "deny", "delete", "retry", "poll", "roast"):
        assert any(f"'/x {word} " in f for f in found), (word, found)
    assert any(f.startswith("'/skill owner/repo'") for f in found), found


def test_the_skill_shorthand_installs_only_for_an_admin(monkeypatch) -> None:
    from kazma_core.agent_skills import tools

    from kazma_gateway.agent_handler.commands import _try_skill_command
    from kazma_gateway.gateway import IncomingMessage

    done: list[str] = []

    async def fake_install(source: str, scope: str = "user") -> str:
        done.append(source)
        return "installed"

    monkeypatch.setattr(tools, "install_agent_skill", fake_install)
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    msg = IncomingMessage(platform="slack", sender_id="slack:U42", text="/skill shadcn/improve")
    manager = _Manager()
    assert asyncio.run(_try_skill_command(msg, _Store(), manager, "gw-slack-u42"))
    assert done == [] and any("admin-only" in t for t in manager.sent)
    assert changes_global_config("/skill shadcn/improve") and changes_global_config("/skill https://x.y/z")
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "slack:U42")
    assert asyncio.run(_try_skill_command(msg, _Store(), _Manager(), "gw-slack-u42"))
    assert done == ["shadcn/improve"]


def test_negative_control_a_planted_branch_and_handler_are_found() -> None:
    planted = (
        "async def _try_x_command(msg, store, manager, thread_id):\n"
        "    sub = 'x'\n"
        "    if sub in ('list', 'subjects'):\n"
        "        return await _x_read_command(sub, cfg, _send)\n"
        "    if sub == 'list-all':\n"
        "        from kazma_core.x_api.reply import forget_summon\n"
        "        await forget_summon('s', approval_token='t')\n"
    )
    found = findings(planted, rule=lambda text: text.split()[1:2] not in (["list"], ["subjects"], ["list-all"]))
    assert found == ["'/x list-all owner/repo 1' calls ['forget_summon']"]
    with pytest.raises(AssertionError, match="new chat command handler"):
        branches("async def _try_wipe_command(msg):\n    if sub == 'all':\n        await wipe()\n")


# ── the handler itself refuses a non-admin, as graph.py's gate does ────


class _Store:
    async def get(self, _thread: str) -> dict[str, Any]:
        return {"chat_id": 7}


class _Manager:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound.text)
        return True


@pytest.fixture
def x_effects(monkeypatch):
    from kazma_core.x_api import reply

    calls: list[str] = []

    async def record(name: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(name)
        from types import SimpleNamespace

        return SimpleNamespace(ok=True, url="https://x.com/i/1", tweet_id="1", reason="done", action="posted")

    for name in ("approve_summon", "deny_summon", "forget_summon", "retry_summon"):
        monkeypatch.setattr(reply, name, lambda *a, _n=name, **k: record(_n, *a, **k))
    monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: object())
    monkeypatch.delenv("KAZMA_GATEWAY_ADMINS", raising=False)
    return calls


def _x(text: str) -> Any:
    from kazma_gateway.gateway import IncomingMessage

    return IncomingMessage(platform="telegram", sender_id="telegram:42", text=text, context_metadata={"chat_id": 42})


@pytest.mark.parametrize("text", ["/x approve s1 tok", "/x deny s1 tok", "/x delete s1 tok", "/x retry s1"])
def test_a_chat_member_cannot_publish_or_delete_on_x(x_effects, text) -> None:
    from kazma_gateway.agent_handler.commands import _try_x_command

    manager = _Manager()
    assert asyncio.run(_try_x_command(_x(text), _Store(), manager, "gw-tg-42"))
    assert x_effects == []
    assert any("admin-only" in t for t in manager.sent), manager.sent


def test_an_admin_publishes_on_x(x_effects, monkeypatch) -> None:
    from kazma_gateway.agent_handler.commands import _try_x_command

    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:42")
    manager = _Manager()
    assert asyncio.run(_try_x_command(_x("/x approve s1 tok"), _Store(), manager, "gw-tg-42"))
    assert x_effects == ["approve_summon"]
