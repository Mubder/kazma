#!/usr/bin/env python3
"""A soak of Spaces with many spaces: real work, restarts, and nothing crossing (S4-09).

Spaces puts several people on one Kazma, each in spaces of their own and in
shared ones. The space matrix (``tests/test_space_matrix.py``) proves every
route and tool once, on two spaces. This is the harness for the long run the
outside review (S4-10) reads: many spaces doing real work through the
product's own routes and stores, a restart after every cycle, and after every
cycle a check that nothing of one space reached another. It is the harness,
not the result.

**The install** is a throwaway one: its own data folder in a temporary
folder (``--work-dir`` names one to keep), never an install's, never the
network, never a real provider or the real embedder. SQLite by default;
``--database-url-from VAR`` names an environment variable holding a
Postgres address the run may fill (never the address itself on the command
line, AGENTS.md section 26 D). It is made the product's way: the switch's
own steps (``preconditions.apply``), the switch (``preconditions.switch_on``),
then ``--people`` people (``people.create_person``, each with a personal
space and a chat-app link of their own, ``chat_links.issue_code`` /
``redeem``) and ``--shared`` shared spaces (``people.create_shared_space``,
``people.add_member``), each with two or three members.

**A cycle** is one process: every restart is a real one (stores reopened
from disk, the app built again, the stored Spaces switch read again). Its
app is the product's routers behind the real tenant middleware -- chat
(``sse_chat``), Documents, the Knowledge Library, Scheduled and the Swarm
-- on the product's stores and checkpoint manager. Who a request is signed
in as is a header of the harness's own (sign-in is S1-03's and S4-02's,
checked by their tests); the space is named as the page names it
(``X-Kazma-Space``). In every space, one of its members, in turn:

* opens a chat (``POST /api/chat/sessions``) and runs a turn
  (``POST /api/chat/stream``), in English or in Arabic. The model is a stub:
  a real LangGraph graph on the product's state and checkpoint manager whose
  supervisor asks for tools, and whose tool worker runs them as the
  product's does after an approval (the turn's messages, thread and space
  bound as ``graph_tool_worker`` binds them): ``memory_store`` (the user's
  words ask to remember), ``memory_search`` (the fact just stored must be
  recalled), ``schedule_task`` (a reminder to the person's own linked chat,
  a month out), ``dispatch_swarm`` (a scripted worker) and a Knowledge page
  published into a library of the space (``publish_page``, as a crawl
  does: nothing is fetched);
* makes that library (``POST /api/kb/libraries``) and uploads a document
  (``POST /api/documents``; the parser workers are not started, so it
  stays received: its rows, version and stored bytes are what this checks).

Every row it writes carries a marker of its own (``ssk...``), and the
harness's ledger (``ledger.json`` in the work folder) knows each marker's
space.

**The check**, at the start of every cycle (what the cycle before left,
across the restart) and at its end:

* *every store*: every SQLite file in the data folder (or, on Postgres,
  every table of ``pg_backup.KAZMA_PG_TABLES``) that
  ``store_registry.SPACE_RULES`` names a ``row`` or ``derived`` store is
  read whole. A row holding a marker is told its space by its space column
  (``tenant_id``, ``space_id``, ``tenant``), a swarm task's metadata, or its
  thread's record (``thread_scopes.db``). A marker in a row of another
  space is a *crossing*, in a row of no space a *row with no space*, in a
  table whose rows cannot be told a space an *untold* row, and in a store
  the registry calls the install's an *install store with a space's words*.
  The files of the space folders (``spaces/<id>/``, the document store) are
  read the same way: a file's space is the folder it is under;
* *every read path*: each person, in each of their spaces, through the
  routes (chats and their messages, documents, Knowledge libraries and
  chunks, scheduled jobs, swarm tasks) and the memory tool
  (``memory_list_beliefs``): a marker of another space shown is a crossing;
* *a non-member*: each person naming a space they are not in is refused
  (403) and shown nothing of it;
* *nothing bound*: an API call naming no space is refused, and the
  in-process reads with no space bound (the chat store, Knowledge, memory)
  are refused or show nothing;
* *nothing lost, and no empty pass*: every marker of the cycles the check
  covers is found in a row of its own space and in its space's reads, and
  every space has rows of every kind of work. A store left empty fails:
  ``--control-no-work`` writes the ledger and sends nothing, and must fail.

Each violation names its kind, the store and table (or route), the row, the
marker and both spaces. The report (``--report``) is rewritten whole after
every cycle (``"status": "running"``), then once with ``passed`` or
``failed``; the run stops after the first cycle with a violation. Exit 0
when healthy, else 1.

Negative controls for the harness test: ``--plant-wrong-space DOMAINS``
makes one write of each named kind of work, in the last cycle, land in
another space of its writer (as a product bug filing a write under the
wrong space would); ``--check-only`` checks a kept work folder again, with
a fresh boot, after a test planted rows by hand.

Usage:
  python scripts/spaces_soak.py --cycles 24 --people 6 --shared 3 --report spaces-soak.json
  python scripts/spaces_soak.py --minutes 480 --people 8 --shared 4 --report spaces-soak.json
  python scripts/spaces_soak.py --cycles 6 --database-url-from KAZMA_SOAK_PG_URL --report soak.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
# The shared report helpers sit beside this file; a runner that does not put
# the script's folder on the path (runpy, a priority wrapper) still finds them.
sys.path.insert(0, str(_HERE))

from soak_report import RUNNING, finished, process_tree_sample, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
for _pkg in ("kazma-cli", "kazma-ui", "kazma-tui", "kazma-skills", "kazma-gateway", "kazma-core"):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)  # this checkout's packages, whatever is installed

logger = logging.getLogger(__name__)

#: The kinds of work a cycle does in every space, and the two letters each
#: kind's markers carry.
DOMAINS: tuple[str, ...] = ("chat", "memory", "document", "knowledge", "scheduled", "swarm")
_CODE = {"chat": "ch", "memory": "me", "document": "do", "knowledge": "kn", "scheduled": "sc", "swarm": "sw"}
_DOMAIN_OF = {code: name for name, code in _CODE.items()}

#: A marker: the run, the space's index, the cycle, the kind of work and a
#: counter. Lower-case letters and digits only, so it stays one word in an
#: id, a title, a file name and a search index.
MARK = re.compile(r"ssk[0-9a-f]{6}s\d{2}c\d{3}(?:ch|me|do|kn|sc|sw)\d{2}")

LEDGER = "ledger.json"
DATA = "data"
#: The header the harness signs a request in with (sign-in is not what this
#: measures; the space is named as the page names it).
PERSON_HEADER = "x-spaces-soak-person"
#: The scripted swarm worker.
WORKER = "probe"
#: The owner's key among the run's people.
OWNER = "owner"
#: A child's own deadline, seconds.
_CHILD_TIMEOUT_S = 900.0
#: The violation kinds, as the report names them.
CROSSED = "crossed"
NO_SPACE = "row_with_no_space"
UNTOLD = "untold_row"
INSTALL_WORDS = "install_store_holds_space_words"
READ_CROSSED = "read_shows_another_space"
NOT_REFUSED = "non_member_not_refused"
UNBOUND = "unbound_read_not_refused"
LOST = "own_row_missing"
EMPTY = "space_has_no_rows"
WORK_FAILED = "work_failed"
CHILD_FAILED = "cycle_process_failed"


# ── the ledger: what was written, in which space ────────────────────────


def _read_json(path: Path, default: Any) -> Any:
    """A file this run or its processes published (renamed into place); a
    sharing violation of a file just renamed is waited out (Windows)."""
    deadline = time.monotonic() + 10.0
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Ledger:
    """The run's people, spaces and every marker written (``ledger.json``)."""

    path: Path
    run: str = ""
    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    spaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    marks: dict[str, dict[str, Any]] = field(default_factory=dict)
    cycles_done: int = 0

    @classmethod
    def load(cls, work: Path) -> Ledger:
        raw = _read_json(work / LEDGER, {})
        return cls(path=work / LEDGER, run=raw.get("run", ""), people=raw.get("people", {}),
                   spaces=raw.get("spaces", {}), marks=raw.get("marks", {}),
                   cycles_done=int(raw.get("cycles_done", 0)))

    def save(self) -> None:
        _write_json(self.path, {"run": self.run, "people": self.people, "spaces": self.spaces,
                                "marks": self.marks, "cycles_done": self.cycles_done})

    def mark(self, space: str, domain: str, cycle: int, n: int) -> str:
        index = int(self.spaces[space]["index"])
        return f"ssk{self.run}s{index:02d}c{cycle:03d}{_CODE[domain]}{n:02d}"

    def note(self, mark: str, *, space: str, domain: str, cycle: int, by: str, lang: str) -> None:
        self.marks[mark] = {"space": space, "domain": domain, "cycle": cycle, "by": by, "lang": lang}

    def space_of_mark(self, mark: str) -> str | None:
        entry = self.marks.get(mark)
        return entry["space"] if entry else None

    def members(self, space: str) -> list[str]:
        return list(self.spaces[space]["members"])

    def spaces_of(self, person: str) -> list[str]:
        return [sid for sid, s in self.spaces.items() if person in s["members"]]


# ── the throwaway install's environment ─────────────────────────────────

_ENV_KEYS = ("KAZMA_DATA_DIR", "KAZMA_DB_BACKEND", "KAZMA_DATABASE_URL", "KAZMA_USER_HOME",
             "KAZMA_SKILLS_HOME", "KAZMA_PROCESS_PRIORITY", "KAZMA_SECRET", "KAZMA_SELF_IMPROVEMENT",
             "KAZMA_RATE_LIMIT_ENABLED", "KAZMA_SPACE_DOOR_REPORT")


def _child_env(work: Path, database_url: str | None) -> dict[str, str]:
    """The environment of a cycle's process: the install's own folders in the
    work folder, no shared secret (sign-in is not measured), no model."""
    # A process of the soak runs as an install does, also when the soak is
    # started by a test: pytest's variables put the chat store in test mode.
    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS and not k.startswith("PYTEST_")}
    data = work / DATA
    env.update({
        "KAZMA_DATA_DIR": str(data), "KAZMA_USER_HOME": str(data / "home"),
        "KAZMA_SKILLS_HOME": str(data / "home"), "KAZMA_PROCESS_PRIORITY": "keep",
        "KAZMA_SELF_IMPROVEMENT": "0", "KAZMA_RATE_LIMIT_ENABLED": "0",
        "KAZMA_DB_BACKEND": "postgres" if database_url else "sqlite",
        "PYTHONIOENCODING": "utf-8",
    })
    if database_url:
        env["KAZMA_DATABASE_URL"] = database_url
    return env


def _keep_models_out() -> None:
    """No embedder, no model package, no provider in this process: meaning
    search and the swarm's router fall back to keywords, a registry client
    refuses like a provider with no usable key."""
    from kazma_core.memory import embedder
    from kazma_core.swarm import semantic_router

    sys.modules.setdefault("sentence_transformers", None)  # type: ignore[arg-type]
    embedder.get_embedder = lambda: None  # type: ignore[assignment]
    with contextlib.suppress(ImportError):
        from kazma_core.memory import vector_store_global

        vector_store_global.get_embedder = lambda: None  # type: ignore[assignment]
    semantic_router._semantic_router = None

    class _NoModel:
        async def chat(self, *_a: Any, **_k: Any) -> Any:
            from kazma_core.llm_provider import LLMError

            raise LLMError("no model in the spaces soak", transient=False, kind="auth")

    from kazma_core import model_registry

    model_registry.get_model_registry().get_client = lambda *_a, **_k: _NoModel()  # type: ignore[method-assign]


# ── setting the install up (the first process) ──────────────────────────


def _setup(work: Path, args: argparse.Namespace) -> dict[str, Any]:
    """The install made the product's way: the switch's steps, the switch,
    the people with their personal spaces and links, the shared spaces."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import people, preconditions, scope

    ledger = Ledger(path=work / LEDGER, run=secrets.token_hex(3))
    store = get_config_store()
    # A throwaway install sends no copy anywhere (the switch refuses a plain
    # copy leaving the machine).
    store.set("backups.offsite.enabled", False, category="backup")
    preconditions.apply()
    preconditions.switch_on(by="spaces-soak")
    if not scope.spaces_enforced():
        raise RuntimeError("Spaces did not switch on")
    # The owner is a person too: the admin, in their own space and in every
    # shared space (the admin makes them), and the only one who starts swarm
    # work once Spaces is on (``swarm.single_operator.swarm_write_refusal``).
    ledger.people[OWNER] = {"account": scope.OWNER_ACCOUNT_ID, "name": OWNER,
                            "personal": scope.OWNER_SPACE_ID, "address": _link(scope.OWNER_ACCOUNT_ID, 0)}
    ledger.spaces[scope.OWNER_SPACE_ID] = {"index": 0, "kind": "owner", "members": [OWNER]}
    index = 1
    for n in range(args.people):
        key = f"p{n}"
        made = people.create_person(f"soak person {n}", "pw-" + secrets.token_urlsafe(16),
                                    by=scope.OWNER_ACCOUNT_ID)
        # Scheduled is off for a new person (S2-11): the admin turns it on.
        people.set_features(made["id"], {"scheduled": True}, by=scope.OWNER_ACCOUNT_ID)
        personal = str(made["personal_space"])
        ledger.people[key] = {"account": made["id"], "name": made["name"], "personal": personal,
                              "address": _link(made["id"], n + 1)}
        ledger.spaces[personal] = {"index": index, "kind": "personal", "members": [key]}
        index += 1
    keys = [key for key in ledger.people if key != OWNER]
    for n in range(args.shared):
        # Two or three members, overlapping: a person in several spaces is
        # the case a crossing would hide in.
        members = [keys[(n + k) % len(keys)] for k in range(2 + n % 2)]
        members = list(dict.fromkeys(members))
        # The admin makes a shared space and adds its members (plan 4.11).
        made = people.create_shared_space(f"soak shared {n}", by=scope.OWNER_ACCOUNT_ID)
        sid = str(made["id"])
        for key in members:
            people.add_member(sid, ledger.people[key]["account"], by=scope.OWNER_ACCOUNT_ID)
        ledger.spaces[sid] = {"index": index, "kind": "shared", "members": [OWNER, *members]}
        index += 1
    ledger.save()
    return {"people": len(ledger.people), "spaces": len(ledger.spaces),
            "shared": sum(1 for s in ledger.spaces.values() if s["kind"] == "shared")}


def _link(account: str, n: int) -> str:
    """A chat-app link of *account*'s own, made as a person links one (a
    code from Settings, sent from their Telegram): where their reminders go
    (S3-05). Nothing is sent: no chat app runs here."""
    from kazma_core.spaces import chat_links

    uid = str(700000000 + n)
    code = chat_links.issue_code(account, "telegram")
    chat_links.redeem("telegram", uid, code["code"], direct=True)
    return f"telegram:{uid}"


# ── the stub model's turn ───────────────────────────────────────────────

#: thread id -> what the stub model asks for in that thread's next turn.
_TURN_PLANS: dict[str, dict[str, Any]] = {}
#: thread id -> what the stub tool worker's calls answered.
_TURN_RESULTS: dict[str, dict[str, str]] = {}


def _registry() -> Any:
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    if not _REGISTRY:
        registry = LocalToolRegistry()
        register_builtin_tools(registry)
        _REGISTRY.append(registry)
    return _REGISTRY[0]


_REGISTRY: list[Any] = []


async def _tool(name: str, **arguments: Any) -> str:
    """The product's tool *name*, run in the context bound now, straight
    through its function: the check's own reads, never the turn's work."""
    import asyncio

    tool = _registry().get_tool(name)
    if tool is None:
        return f"Error: no tool {name}"
    result = tool.func(**arguments)
    if asyncio.iscoroutine(result):
        result = await result
    return str(result)


async def _approved_call(state: dict[str, Any], call: dict[str, Any]) -> str:
    """A turn's tool call as ``graph_tool_worker`` runs one its person
    approved: through the effect journal (``execute_effect``) and the
    registry's ``execute`` -- the skill switch, the space's tool and feature
    doors (``spaces.tools.tool_refusal``), the hooks, the commitment gate --
    with the graph as the call's gate and the approval covering this call."""
    from kazma_core.agent.effect_journal import execute_effect
    from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx, _hitl_approved_ctx, call_authority

    name = call["name"]
    with call_authority(_graph_hitl_gate_ctx, name), call_authority(_hitl_approved_ctx, name):
        result = await execute_effect(_registry(), state, call, dict(call["arguments"]))
    if isinstance(result, dict):
        text = str(result.get("content") or "")
        return f"Error: {text}" if result.get("is_error") and not text.startswith("Error") else text
    return str(result)


def _build_graph(checkpointer: Any) -> Any:
    """A real LangGraph graph on the product's ``SupervisorState``: the stub
    supervisor asks for the turn's tools, then answers with what they
    returned; the stub tool worker runs them as the product's does once
    approved, with the turn's messages, thread and space bound."""
    from kazma_core.agent.state import NodeName, SupervisorState
    from langgraph.graph import END, StateGraph

    def _last_user(state: dict[str, Any]) -> str:
        for message in reversed(list(state.get("messages") or [])):
            role = message.get("role") if isinstance(message, dict) else getattr(message, "type", "")
            if role in ("user", "human"):
                return str(message.get("content") if isinstance(message, dict) else message.content)
        return ""

    def _thread(state: dict[str, Any], config: Any) -> str:
        return str(state.get("thread_id") or (config or {}).get("configurable", {}).get("thread_id") or "")

    async def supervisor(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        thread = _thread(state, config)
        messages = list(state.get("messages") or [])
        if state.get("tool_calls_done") or thread not in _TURN_PLANS:
            said = _TURN_RESULTS.get(thread, {})
            answer = "Done. " + " | ".join(f"{k}: {v[:600]}" for k, v in sorted(said.items()))
            messages.append({"role": "assistant", "content": answer})
            return {"iteration": int(state.get("iteration") or 0) + 1, "next_node": NodeName.RESPOND,
                    "tool_calls_pending": [], "messages": messages}
        plan = _TURN_PLANS[thread]
        calls = [{"id": f"call-{secrets.token_hex(4)}", "name": name, "arguments": arguments}
                 for name, arguments in plan["calls"]]
        messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": c["id"], "type": "function",
            "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
        } for c in calls]})
        return {"iteration": int(state.get("iteration") or 0) + 1, "next_node": NodeName.TOOL_WORKER,
                "messages": messages, "tool_calls_pending": calls, "tool_calls_done": []}

    async def tool_worker(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        from kazma_core.safety.hitl import (
            get_current_thread_id,
            reset_current_tenant_id,
            reset_current_thread_id,
            set_current_tenant_id,
            set_current_thread_id,
        )
        from kazma_core.spaces.scope import space_of_state
        from kazma_core.tools.export_session import (
            reset_current_session_messages,
            set_current_session_messages,
        )

        thread = _thread(state, config)
        plan = _TURN_PLANS.pop(thread, {"calls": []})
        messages = list(state.get("messages") or [])
        shown = [m if isinstance(m, dict) else {"role": getattr(m, "type", ""), "content": m.content}
                 for m in messages]
        tokens = [set_current_session_messages(shown)]
        tid_token = None if get_current_thread_id() else set_current_thread_id(thread)
        tenant_token = set_current_tenant_id(space_of_state(state))
        results: dict[str, str] = {}
        done: list[dict[str, Any]] = []
        try:
            for call in list(state.get("tool_calls_pending") or []):
                name = call["name"]
                try:
                    if name == "soak_publish_page":
                        content = await _publish_page(**call["arguments"])
                    else:
                        content = await _approved_call(state, call)
                except Exception as exc:  # noqa: BLE001 -- the tool's answer, as the tool worker gives it
                    content = f"Error: {type(exc).__name__}: {exc}"
                results[name] = content
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                done.append({"tool_call_id": call["id"], "name": name, "content": content})
        finally:
            reset_current_tenant_id(tenant_token)
            if tid_token is not None:
                reset_current_thread_id(tid_token)
            reset_current_session_messages(tokens[0])
        _TURN_RESULTS[thread] = results
        return {"tool_calls_done": done, "tool_calls_pending": [], "messages": messages,
                "next_node": NodeName.SUPERVISOR}

    def respond(state: dict[str, Any]) -> dict[str, Any]:
        return {"next_node": "end", "messages": list(state.get("messages") or [])}

    def route(state: dict[str, Any]) -> str:
        nxt = state.get("next_node")
        return nxt if nxt in (NodeName.TOOL_WORKER, NodeName.SUPERVISOR) else NodeName.RESPOND

    graph = StateGraph(SupervisorState)
    graph.add_node(NodeName.SUPERVISOR, supervisor)
    graph.add_node(NodeName.TOOL_WORKER, tool_worker)
    graph.add_node(NodeName.RESPOND, respond)
    graph.set_entry_point(NodeName.SUPERVISOR)
    graph.add_conditional_edges(NodeName.SUPERVISOR, route, {
        NodeName.TOOL_WORKER: NodeName.TOOL_WORKER, NodeName.SUPERVISOR: NodeName.SUPERVISOR,
        NodeName.RESPOND: NodeName.RESPOND})
    graph.add_edge(NodeName.TOOL_WORKER, NodeName.SUPERVISOR)
    graph.add_edge(NodeName.RESPOND, END)
    return graph.compile(checkpointer=checkpointer)


async def _publish_page(library_id: str, mark: str, lang: str) -> str:
    """A Knowledge page published into *library_id* as a crawl publishes one
    (``publish_page``), in the space bound now: nothing is fetched."""
    import asyncio
    import hashlib

    from kazma_core.stores.knowledge import get_knowledge_store

    url = f"https://{mark}.example/page"
    text = _words(lang, "page", mark)

    def publish() -> str:
        get_knowledge_store().publish_page(library_id, url, [{
            "library_id": library_id, "source_url": url, "chunk_index": 0, "content": text,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "document_title": f"page {mark}", "section_header": "S0",
        }])
        return f"published {url}"

    return await asyncio.to_thread(publish)


def _words(lang: str, what: str, mark: str) -> str:
    """A person's words around *mark*, in English or in Arabic."""
    english = {
        "remember": f"Please remember that {mark} is the code of the blue box.",
        "page": f"Pumps and valves: {mark} explains how the station is kept.",
        "document": f"Meeting notes {mark}: the pumps are checked every Monday.",
        "task": f"Summarise the station notes {mark}.",
        "reminder": f"Check the blue box {mark}.",
    }
    arabic = {
        "remember": f"تذكّر أن {mark} هو رمز الصندوق الأزرق.",
        "page": f"المضخات والصمامات: {mark} يشرح كيف تُصان المحطة.",
        "document": f"ملاحظات الاجتماع {mark}: تُفحص المضخات كل يوم اثنين.",
        "task": f"لخّص ملاحظات المحطة {mark}.",
        "reminder": f"افحص الصندوق الأزرق {mark}.",
    }
    return (arabic if lang == "ar" else english)[what]


# ── the app of one process ──────────────────────────────────────────────


class Swarm:
    """The scripted worker: its answer names the task's marker."""

    @staticmethod
    def workers() -> dict[str, Any]:
        from kazma_core.swarm.worker import SwarmWorker

        class _Probe(SwarmWorker):
            async def dispatch(self, task: str, context: Any = "") -> dict[str, Any]:
                return {"worker": self.name, "task_id": "", "status": "success",
                        "output": f"done: {task[:200]}", "error": None}

            async def start(self) -> None:
                self._running = True

            async def stop(self) -> None:
                self._running = False

        return {WORKER: _Probe(name=WORKER)}


class World:
    """The product's routers behind the real tenant middleware, on this
    install's stores, for one process (one cycle)."""

    def __init__(self, work: Path, ledger: Ledger) -> None:
        self.work = work
        self.ledger = ledger
        self.data = work / DATA
        self.client: Any = None
        self.held: dict[str, Any] = {}
        self._stack = contextlib.ExitStack()

    # ── building and closing ────────────────────────────────────────────

    def __enter__(self) -> World:
        try:
            self._open()
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stack.close()

    def _open(self) -> None:
        import asyncio
        from contextlib import asynccontextmanager

        import kazma_core.cron.scheduler as cron
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from fastapi.testclient import TestClient
        from kazma_core import paths
        from kazma_core.documents.config import DocumentConfig
        from kazma_core.documents.ingestion import DocumentIngestionService, set_ingestion_service
        from kazma_core.spaces import identity
        from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore, set_swarm_engine
        from kazma_gateway.stores.checkpoint import create_checkpoint_manager
        from kazma_ui import auth, documents_api
        from kazma_ui.kb_api import create_kb_router
        from kazma_ui.scheduled_api import create_scheduled_router
        from kazma_ui.services import reset_swarm_service
        from kazma_ui.sse_chat import create_sse_chat_router
        from kazma_ui.swarm_panel import create_swarm_router

        _keep_models_out()
        # The stored switch, read as a start reads it.
        identity.prime_enforcement()
        people = {key: _principal(p) for key, p in self.ledger.people.items()}
        auth.get_request_principal = lambda request: people.get(request.headers.get(PERSON_HEADER, ""))
        documents_api._active_workspace_id = lambda: "default"

        cron_store = cron.SQLiteCronStore(str(paths.data_dir() / "cron.db"))
        scheduler = cron.CronScheduler(store=cron_store, graph_builder=None)  # never started: nothing fires
        cron.set_cron_scheduler(scheduler)
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]),
                             task_store=TaskStore(paths.swarm_tasks_db()))
        engine._workers.update(Swarm.workers())
        set_swarm_engine(engine)
        reset_swarm_service()
        documents = DocumentIngestionService(config=DocumentConfig(
            storage_root=paths.data_dir() / "document-store", security_malware_scan="off",
            security_local_salvage=False, ocr_enabled=False))
        set_ingestion_service(documents)
        self.held.update(engine=engine, scheduler=scheduler, documents=documents)

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> Any:
            await cron_store.init()
            manager = await create_checkpoint_manager(paths.checkpoints_db())
            self.held["manager"] = manager
            self.held["graph"] = _build_graph(manager)
            try:
                yield
            finally:
                await manager.close()
                await cron_store.close()

        app = FastAPI(lifespan=lifespan)
        app.middleware("http")(auth.create_tenant_middleware())
        app.state.documents = documents
        app.include_router(create_sse_chat_router(graph_getter=lambda: self.held.get("graph")))
        app.include_router(create_kb_router())
        app.include_router(documents_api.create_documents_router())

        class _Agent:
            config: dict[str, Any] = {}

        class _PageData:
            def TemplateResponse(self, request: Any, name: str, context: dict[str, Any]) -> Any:  # noqa: N802
                from fastapi.responses import JSONResponse

                return JSONResponse({"template": name})

        templates = Jinja2Templates(directory=str(_ROOT / "kazma-ui" / "kazma_ui" / "templates"))
        app.include_router(create_scheduled_router(_Agent(), templates))
        app.include_router(create_swarm_router(_PageData()))
        loop = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
        self.client = self._stack.enter_context(TestClient(app, backend_options=loop))
        self._stack.callback(self._close_stores)

    def _close_stores(self) -> None:
        from kazma_core.documents.ingestion import set_ingestion_service
        from kazma_core.swarm import set_swarm_engine

        set_ingestion_service(None)
        set_swarm_engine(None)
        with contextlib.suppress(Exception):
            self.held["documents"].close()
        with contextlib.suppress(Exception):
            self.held["engine"]._task_store.close()

    # ── calls ───────────────────────────────────────────────────────────

    def call(self, person: str | None, space: str | None, method: str, path: str, **kwargs: Any) -> Any:
        from kazma_ui import space_binding

        headers = dict(kwargs.pop("headers", {}) or {})
        if person:
            headers[PERSON_HEADER] = person
        if space:
            headers[space_binding.SPACE_HEADER] = space
        return self.client.request(method, path, headers=headers, **kwargs)

    def on_loop(self, coroutine_fn: Any) -> Any:
        return self.client.portal.call(coroutine_fn)

    def as_(self, person: str, space: str) -> Any:
        from kazma_core.spaces import scope

        return scope.bind_scope(space, self.ledger.people[person]["account"])

    def settle_swarm(self) -> None:
        """Every swarm task this cycle started has ended."""
        import asyncio

        engine = self.held["engine"]

        async def wait() -> None:
            for _ in range(1500):
                handing = any(t.get_name().startswith("swarm-result:") for t in asyncio.all_tasks())
                if not handing and not engine.list_active_tasks():
                    return
                await asyncio.sleep(0.02)

        self.on_loop(wait)


def _principal(person: dict[str, Any]) -> dict[str, Any]:
    """What sign-in gives a person (``auth.get_request_principal``'s shape):
    the owner signs in with the install's secret (the admin); anyone else is
    a local account of the People page."""
    if person["name"] == OWNER:
        return {"username": "operator", "role": "admin", "source": "secret"}
    return {"username": person["name"], "role": "operator", "user_id": person["account"],
            "actor": "login", "source": "session"}


def domains_of(ledger: Ledger, space: str) -> tuple[str, ...]:
    """The kinds of work a space gets: every kind, but swarm work only where
    the owner is a member -- once Spaces is on, only the install's admin
    starts swarm work (``swarm_write_refusal``), so a member's personal space
    holds no swarm task, which the check holds it to."""
    if OWNER in ledger.spaces[space]["members"]:
        return DOMAINS
    return tuple(d for d in DOMAINS if d != "swarm")


# ── the work of a cycle ─────────────────────────────────────────────────


@dataclass
class Slot:
    """One space's work in a cycle: who does it, in which language, where the
    ledger files it (``space``) and where each request is sent: the same
    space, but for a planted wrong space."""

    space: str
    person: str
    lang: str
    to: dict[str, str]


#: The request each kind of work rides on: the turn carries the chat, the
#: memory it stores and recalls, the reminder it books and the page it
#: publishes; the owner's own turn carries the swarm task.
_REQUEST_OF = {"chat": "turn", "memory": "turn", "scheduled": "turn", "knowledge": "library",
               "document": "document", "swarm": "swarm_turn"}


def _plan_cycle(ledger: Ledger, cycle: int, plant_wrong: set[str], last: bool) -> list[Slot]:
    slots: list[Slot] = []
    planted = False
    for n, (space, held) in enumerate(sorted(ledger.spaces.items(), key=lambda kv: kv[1]["index"])):
        members = held["members"]
        person = members[(cycle + n) % len(members)]
        to = {request: space for request in set(_REQUEST_OF.values())}
        if plant_wrong and last and not planted:
            # One write of each named kind lands in another space of the
            # person who makes it (the owner's, for the swarm's turn).
            writers = {_REQUEST_OF[d]: (OWNER if d == "swarm" else person) for d in plant_wrong}
            others = {r: [s for s in ledger.spaces_of(w) if s != space] for r, w in writers.items()}
            if all(others.values()) and (person != OWNER or "swarm" in domains_of(ledger, space)):
                for request, spaces in others.items():
                    to[request] = spaces[0]
                planted = True
        slots.append(Slot(space=space, person=person, lang="ar" if (cycle + n) % 2 else "en", to=to))
    if plant_wrong and last and not planted:
        raise RuntimeError("no person is in two spaces: a wrong space cannot be planted")
    return slots


def _work(world: World, ledger: Ledger, cycle: int, slots: list[Slot], *, control_no_work: bool,
          problems: list[dict[str, Any]]) -> Counter[str]:
    done: Counter[str] = Counter()
    for slot in slots:
        marks = {d: ledger.mark(slot.space, d, cycle, 1) for d in domains_of(ledger, slot.space)}
        for domain, mark in marks.items():
            by = OWNER if domain == "swarm" else slot.person
            ledger.note(mark, space=slot.space, domain=domain, cycle=cycle, by=by, lang=slot.lang)
        if control_no_work:
            continue
        for step in (_knowledge_library, _document, _turn, _swarm_turn):
            try:
                step(world, ledger, slot, marks, problems, done)
            except Exception as exc:  # noqa: BLE001 -- a failed step is a finding, never the end of the run
                problems.append({"kind": WORK_FAILED, "step": step.__name__.strip("_"), "space": slot.space,
                                 "person": slot.person, "error": f"{type(exc).__name__}: {exc}"[:300]})
    ledger.save()
    return done


def _failed(problems: list[dict[str, Any]], step: str, slot: Slot, answer: Any) -> None:
    problems.append({"kind": WORK_FAILED, "step": step, "space": slot.space, "person": slot.person,
                     "status": getattr(answer, "status_code", None),
                     "answer": (answer.text if hasattr(answer, "text") else str(answer))[-400:]})


def _knowledge_library(world: World, ledger: Ledger, slot: Slot, marks: dict[str, str],
                       problems: list[dict[str, Any]], done: Counter[str]) -> None:
    mark = marks["knowledge"]
    made = world.call(slot.person, slot.to["library"], "POST", "/api/kb/libraries",
                      json={"id": f"lib{mark}", "name": f"library {mark}"})
    if made.status_code != 200 or not made.json().get("ok"):
        _failed(problems, "knowledge_library", slot, made)
        return
    ledger.marks[mark]["library"] = made.json()["library"]["id"]
    done["knowledge_library"] += 1


def _document(world: World, ledger: Ledger, slot: Slot, marks: dict[str, str],
              problems: list[dict[str, Any]], done: Counter[str]) -> None:
    mark = marks["document"]
    body = _words(slot.lang, "document", mark).encode("utf-8")
    made = world.call(slot.person, slot.to["document"], "POST", "/api/documents", content=body,
                      headers={"X-Document-Filename": f"notes-{mark}.txt", "Content-Type": "text/plain"})
    if made.status_code not in (200, 201, 202):
        _failed(problems, "document", slot, made)
        return
    done["document"] += 1


def _chat_turn(world: World, person: str, space: str, message: str,
               calls: list[tuple[str, dict[str, Any]]]) -> tuple[Any, dict[str, str] | None]:
    """A chat opened in *space* and one turn in it, the stub model asking
    for *calls*: the stream's answer and what each tool answered."""
    session_id = f"ssk{secrets.token_hex(8)}"
    opened = world.call(person, space, "POST", "/api/chat/sessions", json={"session_id": session_id})
    if opened.status_code != 200:
        return opened, None
    thread = opened.json()["thread_id"]
    _TURN_PLANS[thread] = {"calls": calls}
    answer = world.call(person, space, "POST", "/api/chat/stream",
                        json={"session_id": session_id, "message": message})
    world.settle_swarm()
    _TURN_PLANS.pop(thread, None)
    return answer, _TURN_RESULTS.pop(thread, None)


def _judge_tools(problems: list[dict[str, Any]], done: Counter[str], slot: Slot, answer: Any,
                 results: dict[str, str] | None, expected: dict[str, str]) -> None:
    """The turn ended, and each tool's answer says it did its work: a
    refusal is a failed write, said."""
    if answer.status_code != 200 or "event: error" in answer.text or results is None:
        _failed(problems, "chat_turn", slot, answer)
        return
    done["chat_turn"] += 1
    for name, expect in expected.items():
        said = results.get(name)
        if said is None or expect not in said:
            problems.append({"kind": WORK_FAILED, "step": name, "space": slot.space, "person": slot.person,
                             "answer": (said or "(not called)")[:400]})
        else:
            done[name] += 1


def _turn(world: World, ledger: Ledger, slot: Slot, marks: dict[str, str],
          problems: list[dict[str, Any]], done: Counter[str]) -> None:
    """The space's chat turn: the stub model stores a fact, recalls it,
    books a reminder and publishes a Knowledge page."""
    space = slot.to["turn"]
    calls: list[tuple[str, dict[str, Any]]] = [
        ("memory_store", {"text": _words(slot.lang, "remember", marks["memory"])}),
        ("memory_search", {"query": marks["memory"], "limit": 5}),
        ("schedule_task", {"timing": "30d", "prompt": _words(slot.lang, "reminder", marks["scheduled"])}),
    ]
    expected = {"memory_store": "Stored memory", "memory_search": marks["memory"], "schedule_task": '"ok": true'}
    library = ledger.marks[marks["knowledge"]].get("library")
    if library and slot.to["library"] == space:
        calls.append(("soak_publish_page", {"library_id": library, "mark": marks["knowledge"],
                                            "lang": slot.lang}))
        expected["soak_publish_page"] = "published"
    said = f"{_words(slot.lang, 'remember', marks['memory'])} ({marks['chat']})"
    answer, results = _chat_turn(world, slot.person, space, said, calls)
    _judge_tools(problems, done, slot, answer, results, expected)


def _swarm_turn(world: World, ledger: Ledger, slot: Slot, marks: dict[str, str],
                problems: list[dict[str, Any]], done: Counter[str]) -> None:
    """The owner's turn in a space they are in: one swarm task."""
    if "swarm" not in marks:
        return
    said = _words(slot.lang, "task", marks["swarm"])
    answer, results = _chat_turn(world, OWNER, slot.to["swarm_turn"], said,
                                 [("dispatch_swarm", {"prompt": said, "worker": WORKER})])
    _judge_tools(problems, done, slot, answer, results, {"dispatch_swarm": ""})


# ── the check ───────────────────────────────────────────────────────────


@dataclass
class Check:
    """What one pass found: violations, and what it read."""

    stage: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    reads: Counter[str] = field(default_factory=Counter)
    found_in_store: dict[str, set[str]] = field(default_factory=dict)
    seen_by_space: dict[str, set[str]] = field(default_factory=dict)

    def add(self, kind: str, **detail: Any) -> None:
        self.violations.append({"kind": kind, "stage": self.stage, **detail})


#: Tables a marker may sit in whose rows are not told a space by a column of
#: their own: a full-text index of a table that is (its own rows are read
#: and told), or SQLite's own.
def _skipped_table(name: str) -> bool:
    lowered = name.lower()
    return (lowered.startswith("sqlite_") or "_fts" in lowered or lowered.endswith("fts")
            or lowered.endswith(("_data", "_idx", "_docsize", "_config", "_content")) and "fts" in lowered)


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8", "ignore")
    return str(value)


def _marks_in(values: Any) -> set[str]:
    return set(MARK.findall(" ".join(_text_of(v) for v in values)))


#: The columns that name a row's space, in the order they are asked.
_SPACE_COLUMNS = ("tenant_id", "space_id", "tenant")


@dataclass
class Owners:
    """What tells a row with no space column its space: its thread's record
    (``thread_scopes.db``), its Knowledge library."""

    threads: dict[str, str] = field(default_factory=dict)
    libraries: dict[str, str] = field(default_factory=dict)


def _json_of(value: Any) -> dict[str, Any]:
    """A JSON column's object: text on SQLite, already decoded from
    ``jsonb`` on Postgres."""
    if isinstance(value, dict):
        return value
    held = json.loads(_text_of(value) or "{}")
    return held if isinstance(held, dict) else {}


def _row_space(table: str, row: dict[str, Any], owners: Owners) -> tuple[str, str]:
    """``(how, space)``: how the row is told its space, and the space ("" for
    none). ``how`` is ``""`` when the row cannot be told one."""
    for column in _SPACE_COLUMNS:
        if column in row:
            return column, _text_of(row[column]).strip()
    # A table keeps its name on SQLite and takes Kazma's prefix on Postgres
    # (``kazma_swarm_tasks``).
    if "metadata" in row and table.endswith("swarm_tasks"):
        meta = _json_of(row["metadata"])
        return "metadata.tenant_id", str(meta.get("tenant_id") or "").strip()
    if "payload_json" in row and table.endswith("memory_task_queue"):
        payload = _json_of(row["payload_json"])
        return "payload.tenant_id", str(payload.get("tenant_id") or "").strip()
    if "thread_id" in row:
        return "thread record", owners.threads.get(_text_of(row["thread_id"]), "")
    if "library_id" in row:
        # A chunk belongs to its library, which carries the space (SPACE_RULES).
        return "library", owners.libraries.get(_text_of(row["library_id"]), "")
    return "", ""


def _row_name(table: str, row: dict[str, Any]) -> str:
    for key in ("id", "session_id", "thread_id", "job_id", "task_id", "document_id", "library_id",
                "checkpoint_id", "belief_id", "rowid"):
        if key in row and row[key] is not None:
            return f"{table}.{key}={_text_of(row[key])[:80]}"
    return f"{table}.rowid=?"


def _read_pairs(path: Path, sql: str) -> dict[str, str]:
    if not path.is_file():
        return {}
    with contextlib.closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
        return {str(k): _text_of(v).strip() for k, v in conn.execute(sql)}


def _owners(data: Path) -> Owners:
    return Owners(
        threads=_read_pairs(data / "thread_scopes.db", "SELECT thread_id, space_id FROM thread_scopes"),
        libraries=_read_pairs(data / "knowledge.db", "SELECT id, tenant_id FROM knowledge_libraries"))


def _store_rule(path: Path, data: Path) -> tuple[str, str]:
    """``(store name, rule kind)`` of a file of the data folder; the rule is
    ``"undeclared"`` for a file the registry does not name."""
    from kazma_core import store_registry

    name = store_registry.store_name_for(path)
    if name is None:
        return path.relative_to(data).as_posix(), "undeclared"
    return name, store_registry.space_rule(name).kind


def _judge_row(check: Check, ledger: Ledger, *, store: str, rule: str, table: str, row: dict[str, Any],
               how: str, space: str) -> None:
    marks = _marks_in(row.values())
    known = [m for m in marks if m in ledger.marks]
    if not known:
        return
    where = {"store": store, "table": table, "row": _row_name(table, row)}
    for mark in known:
        mark_space = ledger.marks[mark]["space"]
        if rule not in ("row", "derived", "folder"):
            check.add(INSTALL_WORDS, **where, mark=mark, mark_space=mark_space, row_space="(install)")
        elif not how:
            check.add(UNTOLD, **where, mark=mark, mark_space=mark_space, row_space="(untold)")
        elif not space:
            check.add(NO_SPACE, **where, mark=mark, mark_space=mark_space, row_space="", told_by=how)
        elif space != mark_space:
            check.add(CROSSED, **where, mark=mark, mark_space=mark_space, row_space=space, told_by=how)
        else:
            check.found_in_store.setdefault(mark_space, set()).add(mark)


def _scan_sqlite(check: Check, ledger: Ledger, data: Path) -> None:
    owners = _owners(data)
    for path in sorted(data.rglob("*")):
        if not path.is_file() or path.suffix not in (".db", ".sqlite", ".sqlite3"):
            continue
        store, rule = _store_rule(path, data)
        seen = check.stores.setdefault(store, {"rule": rule, "rows": 0, "marked_rows": 0, "tables": {}})
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        except sqlite3.Error as exc:
            seen["error"] = str(exc)
            continue
        with contextlib.closing(conn):
            conn.row_factory = sqlite3.Row
            try:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            except sqlite3.DatabaseError as exc:
                seen["error"] = str(exc)
                continue
            for table in tables:
                if _skipped_table(table):
                    continue
                told = Counter()
                for raw in conn.execute(f'SELECT rowid AS "rowid", * FROM "{table}"'):  # noqa: S608 -- a table name read from the file itself
                    row = dict(raw)
                    seen["rows"] += 1
                    if not _marks_in(row.values()):
                        continue
                    seen["marked_rows"] += 1
                    how, space = _row_space(table, row, owners)
                    told[how or "(untold)"] += 1
                    _judge_row(check, ledger, store=store, rule=rule, table=table, row=row, how=how,
                               space=space)
                if told:
                    seen["tables"][table] = dict(told)


def _scan_postgres(check: Check, ledger: Ledger, url: str, data: Path) -> None:
    """Kazma's own tables on Postgres, read whole as the install's login. A
    thread's record and a Knowledge library are kept in the data folder's
    SQLite files on either backend: rows are told their space from there."""
    import psycopg
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        owners = _owners(data)
        present = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
        for table in sorted(set(KAZMA_PG_TABLES) & present):
            store = f"postgres:{table}"
            seen = check.stores.setdefault(store, {"rule": "row", "rows": 0, "marked_rows": 0, "tables": {}})
            told: Counter[str] = Counter()
            for row in conn.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 -- a name from Kazma's own list
                seen["rows"] += 1
                if not _marks_in(row.values()):
                    continue
                seen["marked_rows"] += 1
                how, space = _row_space(table, row, owners)
                told[how or "(untold)"] += 1
                _judge_row(check, ledger, store=store, rule="row", table=table, row=row, how=how, space=space)
            if told:
                seen["tables"][table] = dict(told)


def _scan_folders(check: Check, ledger: Ledger, data: Path) -> None:
    """The files of the space folders: a file's space is the folder it is
    under (``spaces/<id>/``, at the data folder's root or a store's, as the
    document store keeps its spaces); a file outside one is the owner's."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    seen = check.stores.setdefault("(space folders)", {"rule": "folder", "rows": 0, "marked_rows": 0,
                                                       "tables": {}})
    for path in sorted(data.rglob("*")):
        if not path.is_file() or path.suffix in (".db", ".sqlite", ".sqlite3", ".json", ".log") \
                or path.name.endswith(("-wal", "-shm", "-journal")) or path.stat().st_size > 4 << 20:
            continue
        parts = path.relative_to(data).parts
        at = parts.index("spaces") if "spaces" in parts[:-2] else -1
        space = parts[at + 1] if at >= 0 else OWNER_SPACE_ID
        seen["rows"] += 1
        marks = {m for m in MARK.findall(path.name + " " + path.read_bytes().decode("utf-8", "ignore"))
                 if m in ledger.marks}
        if not marks:
            continue
        seen["marked_rows"] += 1
        for mark in marks:
            mark_space = ledger.marks[mark]["space"]
            if space != mark_space:
                check.add(CROSSED, store="(space folders)", table=parts[0],
                          row=path.relative_to(data).as_posix(), mark=mark, mark_space=mark_space,
                          row_space=space, told_by="folder")
            else:
                check.found_in_store.setdefault(mark_space, set()).add(mark)


# ── the read paths ──────────────────────────────────────────────────────


def _read_space(world: World, person: str, space: str) -> dict[str, str]:
    """Everything *person* is shown in *space*, by read path."""
    shown: dict[str, str] = {}
    chats = world.call(person, space, "GET", "/api/chat/sessions")
    shown["GET /api/chat/sessions"] = chats.text
    if chats.status_code == 200:
        parts = []
        for row in chats.json():
            sid = row.get("session_id") or row.get("id")
            if sid:
                parts.append(world.call(person, space, "GET", f"/api/chat/sessions/{sid}/messages").text)
        shown["GET /api/chat/sessions/{id}/messages"] = "\n".join(parts)
    shown["GET /api/documents"] = world.call(person, space, "GET", "/api/documents").text
    libraries = world.call(person, space, "GET", "/api/kb/libraries")
    shown["GET /api/kb/libraries"] = libraries.text
    if libraries.status_code == 200:
        parts = []
        for lib in libraries.json().get("libraries") or []:
            parts.append(world.call(person, space, "GET", f"/api/kb/libraries/{lib['id']}/chunks").text)
        shown["GET /api/kb/libraries/{id}/chunks"] = "\n".join(parts)
    shown["GET /api/scheduled/tasks"] = world.call(person, space, "GET", "/api/scheduled/tasks").text
    shown["GET /api/swarm/tasks"] = world.call(person, space, "GET", "/api/swarm/tasks",
                                               params={"pageSize": 100}).text

    async def beliefs() -> str:
        with world.as_(person, space):
            return await _tool("memory_list_beliefs", q="", limit=500)

    shown["tool:memory_list_beliefs"] = world.on_loop(beliefs)
    return shown


#: Which read path shows which kind of work, for "nothing lost".
_SHOWN_BY = {
    "chat": ("GET /api/chat/sessions/{id}/messages",),
    "memory": ("tool:memory_list_beliefs",),
    "document": ("GET /api/documents",),
    "knowledge": ("GET /api/kb/libraries", "GET /api/kb/libraries/{id}/chunks"),
    "scheduled": ("GET /api/scheduled/tasks",),
    "swarm": ("GET /api/swarm/tasks",),
}


def _check_reads(check: Check, world: World, ledger: Ledger) -> None:
    for space, held in sorted(ledger.spaces.items(), key=lambda kv: kv[1]["index"]):
        for person in held["members"]:
            shown = _read_space(world, person, space)
            for route, text in shown.items():
                check.reads[route] += 1
                for mark in set(MARK.findall(text)):
                    mark_space = ledger.space_of_mark(mark)
                    if mark_space is None:
                        continue
                    if mark_space != space:
                        check.add(READ_CROSSED, route=route, person=person, read_space=space, mark=mark,
                                  mark_space=mark_space)
                    else:
                        check.seen_by_space.setdefault(f"{space}|{person}|{ledger.marks[mark]['domain']}",
                                                       set()).add(mark)
    # A non-member naming a space is refused, and shown nothing of it.
    for person in ledger.people:
        mine = set(ledger.spaces_of(person))
        for space in sorted(set(ledger.spaces) - mine)[:3]:
            for route in ("/api/chat/sessions", "/api/documents", "/api/kb/libraries", "/api/swarm/tasks"):
                answer = world.call(person, space, "GET", route)
                check.reads["non-member " + route] += 1
                leaked = [m for m in set(MARK.findall(answer.text)) if ledger.space_of_mark(m) == space]
                if answer.status_code != 403 or leaked:
                    check.add(NOT_REFUSED, route=route, person=person, read_space=space,
                              status=answer.status_code, marks=sorted(leaked)[:5])


def _check_unbound(check: Check, world: World, ledger: Ledger) -> None:
    """A call naming no space runs in the caller's one space, or is refused
    when they have several (``space_binding.space_for_request``): it shows
    no other space's rows. With nothing bound, the in-process reads are
    refused (``NoSpaceBound``) or show nothing."""
    from kazma_core.spaces.scope import NoSpaceBound

    for person in ledger.people:
        mine = ledger.spaces_of(person)
        for route in ("/api/chat/sessions", "/api/documents", "/api/kb/libraries", "/api/swarm/tasks",
                      "/api/scheduled/tasks"):
            answer = world.call(person, None, "GET", route)
            check.reads["names no space " + route] += 1
            shown = {m: ledger.space_of_mark(m) for m in set(MARK.findall(answer.text)) if m in ledger.marks}
            if len(mine) == 1:
                wrong = sorted(m for m, sp in shown.items() if sp != mine[0])
                if wrong:
                    check.add(READ_CROSSED, route=route + " (no space named)", person=person,
                              read_space=mine[0], mark=wrong[0], mark_space=shown[wrong[0]])
            elif answer.status_code < 400 or shown:
                check.add(UNBOUND, route=route, person=person, status=answer.status_code,
                          marks=sorted(shown)[:5])

    def chat_store() -> str:
        from kazma_ui.session_manager import get_session_manager

        return json.dumps([s.to_summary() for s in get_session_manager().list_all()], default=str)

    def knowledge() -> str:
        from kazma_core.stores.knowledge import get_knowledge_store

        return json.dumps(get_knowledge_store().list_libraries(), default=str)

    async def memory() -> str:
        return await _tool("memory_list_beliefs", q="", limit=500)

    for name, read in (("chat store", chat_store), ("knowledge store", knowledge),
                       ("tool:memory_list_beliefs", lambda: world.on_loop(memory))):
        try:
            text = read()
        except NoSpaceBound:
            check.reads["nothing bound, refused: " + name] += 1
            continue
        check.reads["nothing bound, answered: " + name] += 1
        shown = [m for m in set(MARK.findall(text)) if m in ledger.marks]
        if shown:
            check.add(UNBOUND, route=name, status="answered", marks=sorted(shown)[:5])


def _check_kept(check: Check, ledger: Ledger, covers: range) -> None:
    """Nothing lost and no empty pass: every marker of the cycles *covers*
    is in a row of its own space and in its space's reads; every space has
    rows of every kind of work."""
    by_space: dict[str, Counter[str]] = {s: Counter() for s in ledger.spaces}
    for mark, entry in ledger.marks.items():
        if entry["cycle"] not in covers:
            continue
        space, domain = entry["space"], entry["domain"]
        by_space[space][domain] += 1
        if mark not in check.found_in_store.get(space, set()):
            check.add(LOST, where="store", mark=mark, mark_space=space, domain=domain)
        for person in ledger.spaces[space]["members"]:
            if mark not in check.seen_by_space.get(f"{space}|{person}|{domain}", set()):
                check.add(LOST, where="reads", route=" or ".join(_SHOWN_BY[domain]), person=person,
                          mark=mark, mark_space=space, domain=domain)
    if not covers:
        return
    for space, counts in by_space.items():
        for domain in domains_of(ledger, space):
            if not counts[domain]:
                check.add(EMPTY, mark_space=space, domain=domain)


def _check_all(stage: str, world: World, ledger: Ledger, covers: range, database_url: str | None) -> Check:
    check = Check(stage=stage)
    data = world.data
    _scan_sqlite(check, ledger, data)
    _scan_folders(check, ledger, data)
    if database_url:
        _scan_postgres(check, ledger, database_url, data)
    _check_reads(check, world, ledger)
    _check_unbound(check, world, ledger)
    _check_kept(check, ledger, covers)
    return check


def _check_summary(check: Check) -> dict[str, Any]:
    return {"stage": check.stage, "violations": len(check.violations),
            "stores": {name: {k: v for k, v in s.items() if k != "tables" or v}
                       for name, s in sorted(check.stores.items()) if s.get("marked_rows") or s.get("error")},
            "reads": dict(sorted(check.reads.items())),
            "marks_found_in_store": sum(len(v) for v in check.found_in_store.values())}


# ── one process: a cycle ────────────────────────────────────────────────


def _child(args: argparse.Namespace) -> int:
    work = Path(args.work_dir)
    out = work / f"cycle-{args.cycle:04d}.json"
    database_url = os.environ.get("KAZMA_DATABASE_URL") or None
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    started = time.monotonic()
    result: dict[str, Any] = {"cycle": args.cycle, "mode": args.child, "violations": [], "checks": []}
    try:
        if args.child == "setup":
            result["setup"] = _setup(work, args)
        else:
            ledger = Ledger.load(work)
            plant = {d for d in (args.plant_wrong_space or "").split(",") if d}
            with World(work, ledger) as world:
                covers = range(max(0, args.cycle - args.recent_cycles), args.cycle)
                before = _check_all("after_restart", world, ledger, covers, database_url)
                result["checks"].append(_check_summary(before))
                result["violations"] += before.violations
                if args.child == "cycle":
                    slots = _plan_cycle(ledger, args.cycle, plant, args.cycle == args.last_cycle)
                    problems: list[dict[str, Any]] = []
                    t0 = time.monotonic()
                    result["work"] = dict(_work(world, ledger, args.cycle, slots,
                                                control_no_work=args.control_no_work, problems=problems))
                    result["work_seconds"] = round(time.monotonic() - t0, 2)
                    result["violations"] += [{"stage": "work", **p} for p in problems]
                    ledger.cycles_done = args.cycle + 1
                    ledger.save()
                    covers = range(max(0, args.cycle + 1 - args.recent_cycles), args.cycle + 1)
                    after = _check_all("after_work", world, ledger, covers, database_url)
                    result["checks"].append(_check_summary(after))
                    result["violations"] += after.violations
    except Exception as exc:  # noqa: BLE001 -- said in the cycle's result, which the run reads
        import traceback

        result["violations"].append({"kind": CHILD_FAILED, "error": f"{type(exc).__name__}: {exc}"[:500],
                                     "trace": traceback.format_exc()[-3000:]})
    result["seconds"] = round(time.monotonic() - started, 2)
    result["process"] = process_tree_sample()
    _write_json(out, result)
    return 0


# ── the run (the parent) ────────────────────────────────────────────────


def _run_child(work: Path, env: dict[str, str], mode: str, cycle: int, args: argparse.Namespace,
               *, last_cycle: int) -> dict[str, Any]:
    out = work / f"cycle-{cycle:04d}.json"
    out.unlink(missing_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--child", mode, "--cycle", str(cycle),
               "--work-dir", str(work), "--last-cycle", str(last_cycle),
               "--recent-cycles", str(args.recent_cycles), "--people", str(args.people),
               "--shared", str(args.shared)]
    if args.plant_wrong_space:
        command += ["--plant-wrong-space", args.plant_wrong_space]
    if args.control_no_work:
        command.append("--control-no-work")
    log = work / f"cycle-{cycle:04d}.log"
    with log.open("wb") as handle:
        try:
            done = subprocess.run(command, env=env, stdout=handle, stderr=subprocess.STDOUT,  # noqa: S603 -- this script, by its own path
                                  timeout=_CHILD_TIMEOUT_S, check=False)
            code: int | str = done.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    result = _read_json(out, None)
    if result is None:
        tail = log.read_bytes()[-2000:].decode("utf-8", "replace")
        return {"cycle": cycle, "mode": mode, "violations": [{
            "kind": CHILD_FAILED, "exit": code, "error": "the cycle's process wrote no result", "log": tail}]}
    result["exit"] = code
    result["logged"] = _logged(log)
    return result


#: Ids and numbers in a log line, so lines that differ only by them count as one.
_VARIES = re.compile(r"\b(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{6,}|ssk[0-9a-z]+|\d+)\b")


def _logged(log: Path) -> dict[str, int]:
    """The process's WARNING and ERROR lines, counted by their text with ids
    and numbers taken out: what the review reads beside the verdict."""
    counts: Counter[str] = Counter()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("WARNING ", "ERROR ", "CRITICAL ")):
            counts[_VARIES.sub("#", line)[:240]] += 1
    return dict(counts)


def _report_of(args: argparse.Namespace, work: Path, ledger: Ledger, cycles: list[dict[str, Any]],
               started: float) -> dict[str, Any]:
    violations = [v for c in cycles for v in c.get("violations", [])]
    by_kind = Counter(v["kind"] for v in violations)
    work_done: Counter[str] = Counter()
    logged: Counter[str] = Counter()
    for c in cycles:
        work_done.update(c.get("work") or {})
        logged.update(c.get("logged") or {})
    checks = [check for c in cycles for check in c.get("checks", [])]
    last = checks[-1] if checks else {}
    return {
        "harness": "spaces_soak", "item": "S4-09", "status": RUNNING, "updated_at": utc_stamp(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (time.monotonic() - started))),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "work_dir": str(work), "database": "postgres" if args.database_url_from else "sqlite",
        "options": {"cycles": args.cycles, "minutes": args.minutes, "people": args.people, "shared": args.shared,
                    "recent_cycles": args.recent_cycles, "plant_wrong_space": args.plant_wrong_space or "",
                    "control_no_work": bool(args.control_no_work), "check_only": bool(args.check_only)},
        "spaces": {"total": len(ledger.spaces),
                   "personal": sum(1 for s in ledger.spaces.values() if s["kind"] == "personal"),
                   "shared": sum(1 for s in ledger.spaces.values() if s["kind"] == "shared"),
                   "people": len(ledger.people)},
        "cycles_done": sum(1 for c in cycles if c.get("mode") == "cycle"),
        "restarts": sum(1 for c in cycles if c.get("mode") in ("cycle", "check")),
        "marks_written": len(ledger.marks),
        "marks_by_domain": dict(Counter(m["domain"] for m in ledger.marks.values())),
        "marks_by_language": dict(Counter(m["lang"] for m in ledger.marks.values())),
        "work": dict(sorted(work_done.items())),
        "checks_run": len(checks),
        # What the last check read: every store holding a marker, with its
        # rule and how its rows were told their space; every read path.
        "last_check": last,
        "logged": dict(logged.most_common(40)),
        "cycles": [{k: v for k, v in c.items() if k not in ("violations", "checks", "logged")}
                   | {"violations": len(c.get("violations", [])),
                      "checks": [{k: v for k, v in ch.items() if k != "stores"} for ch in c.get("checks", [])]}
                   for c in cycles],
        "violation_count": len(violations), "violations_by_kind": dict(by_kind),
        "violations": violations[:500],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--cycles", type=int, default=6, help="cycles of work, each a process (a restart)")
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="run cycles until this many minutes have passed (overrides --cycles)")
    parser.add_argument("--people", type=int, default=4, help="people, each with a personal space")
    parser.add_argument("--shared", type=int, default=2, help="shared spaces, each with two or three members")
    parser.add_argument("--recent-cycles", type=int, default=3,
                        help="the cycles whose markers 'nothing lost' asks for in every check")
    parser.add_argument("--work-dir", default=None, help="default: a temporary folder, removed when the run passes")
    parser.add_argument("--report", default=None)
    parser.add_argument("--database-url-from", default=None,
                        help="the NAME of an environment variable holding a throwaway Postgres address")
    parser.add_argument("--check-only", action="store_true",
                        help="check a kept work folder again, with a fresh boot, and do no work")
    parser.add_argument("--plant-wrong-space", default="",
                        help="negative control: comma-separated kinds of work whose write, in the last cycle, "
                             f"lands in another space of its writer ({', '.join(DOMAINS)})")
    parser.add_argument("--control-no-work", action="store_true",
                        help="negative control: the ledger is written and no request is sent")
    # A cycle's own process.
    parser.add_argument("--child", choices=("setup", "cycle", "check"), help=argparse.SUPPRESS)
    parser.add_argument("--cycle", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--last-cycle", type=int, default=-1, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return _child(args)

    unknown = {d for d in args.plant_wrong_space.split(",") if d} - set(DOMAINS)
    if unknown:
        parser.error(f"--plant-wrong-space: no kind of work {sorted(unknown)}")
    if args.people < 2:
        parser.error("--people: at least two")
    database_url = None
    if args.database_url_from:
        database_url = os.environ.get(args.database_url_from) or None
        if not database_url:
            parser.error(f"--database-url-from: {args.database_url_from} is not set")
    keep = bool(args.work_dir)
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="kazma-spaces-soak-"))
    work.mkdir(parents=True, exist_ok=True)
    (work / DATA).mkdir(exist_ok=True)
    env = _child_env(work, database_url)
    started = time.monotonic()
    deadline = started + args.minutes * 60 if args.minutes else None
    cycles: list[dict[str, Any]] = []
    ledger = Ledger.load(work)

    def save(status_ended: bool = False) -> dict[str, Any]:
        report = _report_of(args, work, Ledger.load(work), cycles, started)
        if not status_ended:
            _write_report(args.report, report)
        return report

    if args.check_only:
        if not ledger.marks:
            parser.error("--check-only: the work folder holds no run (no ledger)")
        cycles.append(_run_child(work, env, "check", ledger.cycles_done, args, last_cycle=-1))
    else:
        if not ledger.spaces:
            cycles.append(_run_child(work, env, "setup", 0, args, last_cycle=-1))
            save()
        first = Ledger.load(work).cycles_done
        total = args.cycles
        cycle = first
        while not any(c.get("violations") for c in cycles):
            if deadline is None and cycle >= first + total:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            last = first + total - 1 if deadline is None else -1
            cycles.append(_run_child(work, env, "cycle", cycle, args, last_cycle=last))
            save()
            cycle += 1
        if not any(c.get("violations") for c in cycles):
            # The last restart: what the last cycle left, checked by a fresh boot.
            cycles.append(_run_child(work, env, "check", cycle, args, last_cycle=-1))
    report = save(status_ended=True)
    healthy = report["violation_count"] == 0 and report["cycles_done"] + int(bool(args.check_only)) > 0
    finished(report, healthy)
    _write_report(args.report, report)
    print(json.dumps({k: report[k] for k in ("status", "cycles_done", "restarts", "marks_written",
                                              "violation_count", "violations_by_kind")}, ensure_ascii=True))
    if not keep:
        if healthy:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"work folder kept: {work}", file=sys.stderr)
    return 0 if healthy else 1


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
