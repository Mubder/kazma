"""Deterministic application-graph harness for the unified turn block.

``docs/plans/UNIFIED_TURN_BLOCK.md`` Phase 0 requires a "deterministic
application graph harness supporting four approval/resume cycles", and §10
is specific about what does not count:

    Mock the external model/network at a supported provider boundary to make
    the graph deterministic. Do not mock the approval endpoint or manually
    change the UI to make sequential approval tests pass. The real graph must
    emit the first request, resume after the actual approval command, emit
    the next request, and finish.

``tests/e2e/test_hitl_view_model.py`` could not do this — its own docstring
records why: "F0 found that ``create_app()`` does not pause from preloaded
``tool_calls_pending``." It seeds registry rows instead, so it proves
rendering, never sequential approval. Incidents 1 and 4 have stayed
unclaimed since.

This harness closes that gap by scripting the **provider boundary** rather
than the graph, the approval route, or the DOM:

* ``LLMProvider.chat`` / ``chat_stream`` are patched at the class. AGENTS.md
  §3 names ``LLMProvider.chat()`` as the single OpenAI-compatible path every
  transport shares, so patching it leaves the supervisor, the tool worker,
  ``interrupt()``, the checkpointer, the gate registry, ``close_turn`` and
  both transports running exactly as they do in production.
* The script answers from the conversation itself — "how many tool results
  have come back?" — so a retry, a resume, or a replayed checkpoint produces
  the same answer. No call counter to get out of step with the graph.
* Every step asks for a tool from ``safety/hitl.py:CANONICAL_DANGER_TOOLS``,
  so the real gate fires. Nothing here tells the graph to pause.

The server fixture boots the real ``create_app()`` under in-process uvicorn
with an isolated ``KAZMA_DATA_DIR``. Plan §14.1: never use the operator's
active approvals as test fixtures.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# ══════════════════════════════════════════════════════════════════════════
# The script
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Step:
    """One gated tool call the scripted model will ask for."""

    tool: str
    args: dict[str, Any]
    #: Text the model narrates before asking. Optional; keeps the turn from
    #: being pure tool calls, which is what a real turn looks like.
    narration: str = ""


@dataclass
class Script:
    """What the model does, as a function of the conversation so far."""

    steps: list[Step]
    final: str = "Done."
    #: Every response the script produced, in order. Diagnostic only — the
    #: script never reads it to decide anything.
    calls: list[str] = field(default_factory=list)

    def respond(self, messages: list[dict[str, Any]]) -> Any:
        """Return the ``LLMResponse`` for this point in the conversation.

        Driven by the number of tool results already in ``messages``, not by
        a call counter: the supervisor may call the model again for the same
        step after a transient failure, and a resumed graph replays part of
        the conversation. Both must produce the same answer or the harness
        is testing its own bookkeeping.
        """
        from kazma_core.llm_provider import LLMResponse, ToolCall

        done = sum(
            1
            for m in messages or []
            if isinstance(m, dict)
            and str(m.get("role") or m.get("type") or "").lower() == "tool"
        )
        if done >= len(self.steps):
            self.calls.append("final")
            return LLMResponse(
                content=self.final, finish_reason="stop", model="harness"
            )
        step = self.steps[done]
        self.calls.append(step.tool)
        return LLMResponse(
            content=step.narration,
            tool_calls=[
                ToolCall(
                    id=f"call_{done + 1}_{step.tool}",
                    name=step.tool,
                    arguments=dict(step.args),
                )
            ],
            finish_reason="tool_calls",
            model="harness",
        )


def four_gate_script(tmp_dir: str) -> Script:
    """The agreed layout fixture, as a model script.

    ``tests/fixtures/unified_turn/layout/four_sequential_gates.json`` is the
    single description of this scenario; the tools and their order are read
    from it so the harness and the acceptance matrix cannot drift apart.
    Paths are rebased into *tmp_dir* — a gated tool that is approved really
    runs, and it must not run anywhere near the operator's files.
    """
    import pathlib

    fixture = json.loads(
        (
            pathlib.Path(__file__).resolve().parents[1]
            / "fixtures"
            / "unified_turn"
            / "layout"
            / "four_sequential_gates.json"
        ).read_text(encoding="utf-8")
    )
    steps: list[Step] = []
    for raw in fixture["script"]:
        args = dict(raw["args"])
        if "path" in args:
            args["path"] = os.path.join(tmp_dir, str(args["path"]).replace("/", os.sep))
        steps.append(
            Step(
                tool=str(raw["tool"]),
                args=args,
                narration=f"Step {raw['step']}: {raw['tool']}.",
            )
        )
    return Script(steps=steps, final=str(fixture["final_answer"]))


# ══════════════════════════════════════════════════════════════════════════
# The provider boundary
# ══════════════════════════════════════════════════════════════════════════


@contextlib.contextmanager
def scripted_provider(script: Script) -> Iterator[Script]:
    """Patch ``LLMProvider.chat``/``chat_stream`` for the duration.

    Both are patched. ``invoke_llm_chat`` prefers ``chat_stream`` whenever
    streaming is on (``llm_stream.py:invoke_llm_chat``), so patching only
    ``chat`` would leave the live token path unexercised — and the token
    path is half of what the unified turn block renders.
    """
    from kazma_core.llm_provider import LLMProvider
    from kazma_core.llm_stream import StreamDelta

    async def _chat(self: Any, messages: Any, *a: Any, **kw: Any) -> Any:
        return script.respond(list(messages or []))

    async def _chat_stream(self: Any, messages: Any, *a: Any, **kw: Any) -> Any:
        resp = script.respond(list(messages or []))
        text = str(getattr(resp, "content", "") or "")
        # Chunk on word boundaries: a single delta would never exercise the
        # append-exactly-once merge rules the projector is built on.
        for i in range(0, len(text), 24):
            yield StreamDelta(content=text[i : i + 24])
        yield StreamDelta(response=resp)

    orig_chat = LLMProvider.chat
    orig_stream = LLMProvider.chat_stream
    LLMProvider.chat = _chat  # type: ignore[assignment]
    LLMProvider.chat_stream = _chat_stream  # type: ignore[assignment]
    try:
        yield script
    finally:
        LLMProvider.chat = orig_chat  # type: ignore[assignment]
        LLMProvider.chat_stream = orig_stream  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════
# The application
# ══════════════════════════════════════════════════════════════════════════


#: The deterministic profile the harness runs on. A loopback base URL and
#: a real-shaped key, so the chat route's pre-stream check is satisfied
#: honestly; nothing dials it, because `scripted_provider` owns the
#: provider boundary above it.
HARNESS_BASE_URL = "http://127.0.0.1:1/v1"
HARNESS_API_KEY = "sk-unified-turn-harness"
HARNESS_MODEL = "harness-model"

#: Set while a harness server is up, so tests/e2e/conftest.py knows to
#: re-apply the seeds and no other e2e test has its provider rewritten.
HARNESS_ENV_FLAG = "KAZMA_UTB_HARNESS"


def seed_provider_config(config_store: Any = None) -> None:
    """Write the harness profile into the CURRENT config store.

    Called at boot and again before every test. The repeat is not
    belt-and-braces: the root conftest gives each test its own
    ConfigStore, and the app re-reads the store on every turn, so seeds
    written at boot are in a store nobody reads by the time a test runs.

    The HTTP harness never noticed, because it pins no model and the
    chat route then uses the agent's own provider. A browser pins the
    model on every send, which routes through ``get_client(model)`` ->
    registry lookup -> miss -> the shipped OpenAI profile -> "No API key
    configured". Same harness, same app, different branch.
    """
    if config_store is None:
        from kazma_core.config_store import get_config_store

        config_store = get_config_store()
    config_store.batch_set([
        ("llm.base_url", HARNESS_BASE_URL, "llm"),
        ("llm.api_key", HARNESS_API_KEY, "llm"),
        ("llm.model", HARNESS_MODEL, "llm"),
    ])
    # ...and as a real provider ENTRY, so a PINNED model resolves instead
    # of falling back to the active provider's URL.
    config_store.set(
        "providers.list",
        [{
            "name": "custom",
            "display_name": "Unified turn harness",
            "base_url": HARNESS_BASE_URL,
            "api_key": HARNESS_API_KEY,
            "models": [HARNESS_MODEL],
            "enabled": True,
        }],
        category="providers",
    )
    config_store.batch_set([
        ("registry.active_provider", "custom", "registry"),
        ("registry.active_model", HARNESS_MODEL, "registry"),
        ("registry.discovered_models", {"custom": [HARNESS_MODEL]}, "registry"),
    ])
    # The registry caches the active profile and its clients; a reseed
    # that leaves a stale client behind changes nothing.
    try:
        from kazma_core.model_registry import get_model_registry

        reg = get_model_registry()
        reg._active_provider = "custom"
        reg._active_model = HARNESS_MODEL
        reg._clients.clear()
    except Exception:  # noqa: BLE001 - the registry may not exist yet
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_live(base: str, timeout: float = 90.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/health/live", timeout=2.0)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"uvicorn did not become live: {last}")


@dataclass
class Harness:
    base: str
    data_dir: str
    script: Script


def isolated_config(tmp_dir: str) -> str:
    """Write an isolated ``kazma.yaml`` into *tmp_dir* and return its path.

    ``KAZMA_DATA_DIR`` alone is not isolation. The first run of this harness
    opened ``G:\\GitHubRepos\\kazma\\kazma-data\\checkpoints.db`` — the
    repository's own checkpoints — because ``storage.path`` in the shipped
    ``kazma.yaml`` is a relative path resolved against the working
    directory, and it started the workspace-bound MCP filesystem server
    against the repository root. Both are corrected here.

    Passing this path as ``config_path`` also moves the local-override
    lookup next to it, so the operator's ``kazma.local.yaml`` cannot leak in
    (``config_loader.resolve_config_paths``).
    """
    import yaml

    shipped = Path(__file__).resolve().parents[2] / "kazma.yaml"
    cfg: dict[str, Any] = {}
    if shipped.is_file():
        loaded = yaml.safe_load(shipped.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            cfg = loaded
    storage = dict(cfg.get("storage") or {})
    storage["engine"] = "sqlite"
    storage["path"] = os.path.join(tmp_dir, "checkpoints.db")
    cfg["storage"] = storage
    # No external processes. The shipped config launches a workspace-bound
    # filesystem MCP server; a deterministic harness does not get to depend
    # on npx being installed, or on what it would be pointed at.
    cfg["mcp"] = {"servers": []}
    # The chat route refuses to start a turn when the active profile is a
    # cloud endpoint with no usable key (``sse_chat/__init__.py:627``), and
    # it is right to. Point the profile at a loopback endpoint with a real
    # -shaped key: the gate is satisfied honestly, and nothing dials it
    # because ``scripted_provider`` owns the provider boundary above it.
    llm = dict(cfg.get("llm") or {})
    llm["base_url"] = "http://127.0.0.1:1/v1"
    llm["api_key"] = "sk-unified-turn-harness"
    llm["model"] = "harness-model"
    cfg["llm"] = llm
    out = Path(tmp_dir) / "kazma.yaml"
    out.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return str(out)


def _isolate_workspace_store(tmp_dir: str) -> None:
    """Point the WorkspaceStore singleton at the isolated tree.

    ``KAZMA_DATA_DIR`` is not enough. ``stores/workspaces.py`` computes
    its default database path at IMPORT time, so by the time a fixture
    sets the variable the singleton is already aimed at the operator's
    real ``kazma-data/workspaces.db`` -- and that store's active row is
    rung 2 of ``resolve_active_root()``, which outranks the
    ``KAZMA_WORKSPACE`` this harness sets at rung 4.

    Measured consequence: the agent's workspace was ``G:/GitHubRepos/kazma``.
    Every approved ``file_write`` was then refused for pointing outside
    it -- silently, because the refusal comes back as an ordinary return
    value (``path_policy.denied_message()``, which opens "Safety: ..."),
    so the worker logged ``error=False`` and the turn carried on having
    written nothing. The suite's one execution assertion survived on a
    file an earlier test had left behind in a shared directory.

    That silence was its own defect and is fixed separately, in
    ``tool_registry``'s failure classifier -- see
    ``tests/test_refused_tool_is_not_a_success.py``.

    An EMPTY isolated store is not enough either: given no rows, the
    store registers and activates the current working directory, so
    rung 2 comes back populated with the repo anyway (measured: a new
    row id, today's timestamp, ``root_path`` = the checkout). Hence the
    explicit create-and-activate below -- the harness names its own
    workspace rather than hoping the store stays quiet.
    """
    try:
        import kazma_core.stores.workspaces as _ws

        _ws.reset_workspace_store()
        store = _ws.WorkspaceStore(
            db_path=os.path.join(tmp_dir, "workspaces.db")
        )
        _ws._workspace_store = store
        created = store.create_workspace("unified-turn-harness", tmp_dir)
        store.set_active_workspace(str(created["id"]))
    except Exception:  # noqa: BLE001 - an un-isolated store is caught by
        # test_approved_tools_actually_execute, which is where it belongs.
        pass


@contextlib.contextmanager
def unified_turn_server(script: Script | None = None) -> Iterator[Harness]:
    """Boot the real app with an isolated data directory and a scripted model.

    Isolation is not politeness: plan §14.1 forbids using the operator's
    active approvals as fixtures, and an approved ``file_write`` in this
    harness really writes.
    """
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_ui.app import create_app

    orig_env = {
        k: os.environ.get(k)
        for k in ("KAZMA_SECRET", "KAZMA_DATA_DIR", "KAZMA_DB_BACKEND",
                  "KAZMA_WORKSPACE", HARNESS_ENV_FLAG)
    }
    os.environ.pop("KAZMA_SECRET", None)
    os.environ["KAZMA_DB_BACKEND"] = "sqlite"
    os.environ[HARNESS_ENV_FLAG] = "1"

    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        os.environ["KAZMA_DATA_DIR"] = tmp_dir
        # The agent's workspace, not just its database. Without this the
        # scripted paths sit OUTSIDE `resolve_active_root()`'s default
        # sandbox, `check_path_access` refuses every approved write, and
        # `file_write` reports the refusal by RETURNING "Error: ..." --
        # a normal return, so the worker logs `error=False` and the turn
        # sails on having written nothing. An approved danger tool that
        # writes nowhere is not isolation (plan §14.1); it is a no-op
        # wearing isolation's clothes, and it is what let
        # `test_approved_tools_actually_execute` pass for a month on a
        # file some earlier test had left in a shared directory.
        #
        # KAZMA_WORKSPACE is rung 4 of the ladder: per-task scope and the
        # WorkspaceStore row both outrank it, so this can never win a
        # fight with an operator's real "Switch Repo" choice.
        os.environ["KAZMA_WORKSPACE"] = tmp_dir
        cs = ConfigStore(db_path=os.path.join(tmp_dir, "utb_settings.db"))
        set_config_store(cs)
        # The model registry resolves the active profile from ConfigStore,
        # not from the YAML (``model_registry._resolve_provider_config``),
        # so the YAML alone left the profile on the shipped OpenAI default
        # and the chat route refused the turn. Write the same three keys
        # Settings > Models writes.
        seed_provider_config(cs)
        _isolate_workspace_store(tmp_dir)
        script = script or four_gate_script(tmp_dir)
        with scripted_provider(script):
            port = free_port()
            app = create_app(isolated_config(tmp_dir))
            config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="warning"
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}"
            try:
                wait_live(base)
                yield Harness(base=base, data_dir=tmp_dir, script=script)
            finally:
                server.should_exit = True
                thread.join(timeout=10.0)
                cs.close()
                _reset_process_singletons()
                for key, val in orig_env.items():
                    if val is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = val


def _reset_process_singletons() -> None:
    """Same teardown as the sibling e2e fixtures; each reset is optional."""
    resets = (
        ("kazma_core.shutdown", "uninstall_shutdown_signal_hooks"),
        ("kazma_core.shutdown", "reset_shutdown"),
        ("kazma_ui.session_manager", "reset_session_manager"),
        ("kazma_core.config_store", "reset_config_store"),
        ("kazma_core.model_registry", "reset_model_registry"),
        ("kazma_ui.delivery", "reset_turn_broker"),
        # ...and hand the operator's own workspace store back.
        ("kazma_core.stores.workspaces", "reset_workspace_store"),
    )
    for mod, fn in resets:
        try:
            module = __import__(mod, fromlist=[fn])
            getattr(module, fn)()
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass

    # ...and the PIN, which is a separate thing from the store.
    #
    # `resolve_active_root()` memoises: rung 2 assigns the active
    # WorkspaceStore row into `binding._WORKSPACE_ROOT` (~line 123), so
    # merely ASKING where the workspace is installs a process pin at
    # rung 3. Dropping the isolated store above then leaves rung 2 empty
    # and rung 3 answering with a temp directory that no longer exists.
    # Observed as test_file_write_workspace_not_drive_root failing in a
    # full run and passing alone.
    try:
        from kazma_core.workspace.binding import configure_workspace

        configure_workspace(None)  # None clears the pin
    except Exception:  # noqa: BLE001 - teardown is best-effort
        pass


# ══════════════════════════════════════════════════════════════════════════
# Driving a turn
# ══════════════════════════════════════════════════════════════════════════


def sse_frames(
    response: Any,
    limit_seconds: float = 120.0,
    *,
    keepalives: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield ``{"event": str, "data": dict}`` from a live SSE response.

    With ``keepalives=True`` a ``: keepalive`` comment is surfaced as
    ``{"event": "keepalive", "data": {}}`` instead of being skipped.

    That matters for one case only, and it is not cosmetic. An attach to
    a PAUSED thread never closes -- ``_sse_attach_stream`` holds it open
    on purpose, because approve will journal into the same tail -- so a
    reader that waits for a terminal frame waits for a human. A
    keepalive arriving after the replay, on a turn the handshake said is
    not running, IS the end of transmission. Without this the only way
    to observe it is to time out, which reports a stall where there is
    none.
    """
    deadline = time.monotonic() + limit_seconds
    event = ""
    for raw in response.iter_lines():
        if time.monotonic() > deadline:
            raise TimeoutError("SSE stream did not terminate in time")
        line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        line = line.rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            if keepalives:
                yield {"event": "keepalive", "data": {}}
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            try:
                data = json.loads(payload)
            except Exception:  # noqa: BLE001 - keepalives and plain text
                data = {"raw": payload}
            yield {"event": event or "message", "data": data}
            event = ""


@contextlib.contextmanager
def api_client(base: str) -> Iterator[Any]:
    """An httpx client authenticated the way a loopback browser is.

    ``auth.py:_mint_auth_cookie`` issues an opaque session cookie to a
    loopback request; a bare POST with no prior GET is a 401. Priming with
    the real page is the same handshake the browser performs, so the
    harness is not authenticating through a door the product does not have.
    """
    import httpx

    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        primed = client.get(f"{base}/chat")
        if primed.status_code >= 400:
            raise AssertionError(
                f"could not prime auth: GET /chat -> {primed.status_code}"
            )
        yield client


def start_turn(
    base: str, session_id: str, message: str, client: Any
) -> Any:
    """POST the real chat route and return the streaming response."""
    return client.stream(
        "POST",
        f"{base}/api/chat/stream",
        json={"message": message, "session_id": session_id},
        timeout=120.0,
    )


def submit_decision(
    base: str,
    thread_id: str,
    *,
    approve: bool,
    interrupt_id: str = "",
    scope: str = "once",
    client: Any,
) -> dict[str, Any]:
    """POST the real approval route. Never a shortcut around it.

    The route answers with JSON and resumes the graph in a background task
    whose frames go to the journal (``routes_direct/misc.py``, the
    ``running: True`` return). The caller therefore has to re-attach to see
    what the resume produced, exactly as ``chat.js:_attachJournal`` does
    after an Approve click.
    """
    body: dict[str, Any] = {
        "action": "approve" if approve else "deny",
        "scope": scope,
    }
    if interrupt_id:
        body["interrupt_id"] = interrupt_id
    resp = client.post(
        f"{base}/api/approve/{thread_id}", json=body, timeout=60.0
    )
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": resp.text}
    payload["_status"] = resp.status_code
    return payload


def attach_stream(
    base: str, session_id: str, last_seq: int, client: Any
) -> Any:
    """Re-attach to the journal from *last_seq*, the way the page does.

    A message-less POST to the same chat route is the attach handshake
    (``chat.js:_attachJournal`` -> ``sse_chat/_streaming.py:_sse_attach_stream``).
    Using it keeps the harness on the product's own recovery path instead
    of inventing a second one that could pass while the real one is broken.
    """
    return client.stream(
        "POST",
        f"{base}/api/chat/stream",
        json={"session_id": session_id, "last_event_id": int(last_seq)},
        timeout=180.0,
    )


HITL_EVENTS = (
    "hitl",
    "approval_needed",
    "approval_required",
    "paused_for_approval",
)


def gate_from_frame(frame: dict[str, Any]) -> str:
    """The interrupt id a HITL frame carries, or ``""``."""
    data = frame.get("data") or {}
    if not isinstance(data, dict):
        return ""
    iid = data.get("interrupt_id")
    if not iid:
        payload = data.get("payload")
        if isinstance(payload, dict):
            iid = payload.get("interrupt_id")
    return str(iid or "")


def new_session_id() -> str:
    return "utb-" + uuid.uuid4().hex[:12]


TERMINAL_EVENTS = ("done", "turn_complete", "capacity")


@dataclass
class Cycle:
    """One pause/resume leg of a turn."""

    gate_id: str
    tool: str
    thread_id: str
    decision: str = ""
    #: The approval route's own JSON answer — the only thing that may
    #: establish a decision (plan section 3).
    ack: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TurnRun:
    """Everything one driven turn produced."""

    session_id: str
    thread_id: str = ""
    cycles: list[Cycle] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    #: Highest delivery ``seq`` seen. The attach after each decision presents
    #: it as the cursor, so the resume is a resume and not a replay.
    last_seq: int = 0

    @property
    def gate_ids(self) -> list[str]:
        return [c.gate_id for c in self.cycles]

    def text(self) -> str:
        """Concatenated token content, in arrival order."""
        out = []
        for f in self.frames:
            if f["event"] == "token":
                out.append(str((f["data"] or {}).get("content") or ""))
        return "".join(out)

    def final_answer(self) -> str:
        for f in reversed(self.frames):
            if f["event"] in ("turn_complete", "done"):
                content = str((f["data"] or {}).get("content") or "")
                if content:
                    return content
        return ""


def _read_leg(
    response: Any, run: TurnRun, limit_seconds: float
) -> tuple[str, str, str]:
    """Consume one SSE leg. Returns ``(gate_id, tool, thread_id)``.

    Stops at the first pending gate — the stream stays open while the graph
    is parked, so reading to EOF would hang until the approval timeout. An
    empty gate id means the leg reached a terminal frame instead.

    Also tracks the delivery cursor, because the next leg is an attach and
    an attach without a cursor replays the whole turn.
    """
    for frame in sse_frames(response, limit_seconds=limit_seconds):
        run.frames.append(frame)
        data = frame["data"] if isinstance(frame["data"], dict) else {}
        try:
            seq = int(data.get("seq") or 0)
        except Exception:  # noqa: BLE001
            seq = 0
        if seq > run.last_seq:
            run.last_seq = seq
        if data.get("thread_id") and not run.thread_id:
            run.thread_id = str(data["thread_id"])
        if frame["event"] in HITL_EVENTS:
            state = str(data.get("state") or "pending")
            gate_id = gate_from_frame(frame)
            if state == "pending" and gate_id:
                return gate_id, str(data.get("tool") or ""), str(
                    data.get("thread_id") or run.thread_id
                )
        if frame["event"] in TERMINAL_EVENTS:
            run.finished = True
            return "", "", run.thread_id
    return "", "", run.thread_id


def drive_turn(
    harness: Harness,
    message: str,
    decisions: list[bool],
    *,
    session_id: str = "",
    scope: str = "once",
    leg_timeout: float = 120.0,
    max_cycles: int = 8,
) -> TurnRun:
    """Run one turn to completion, answering each gate through the real route.

    *decisions* is consumed one gate at a time; when it runs out the
    remaining gates are approved. ``max_cycles`` is a runaway guard, not an
    expectation — a harness that silently looped forever on a mis-scripted
    model would look like a hang, not a failure.
    """
    run = TurnRun(session_id=session_id or new_session_id())
    pending = list(decisions)
    with api_client(harness.base) as client:
        with start_turn(harness.base, run.session_id, message, client) as resp:
            assert resp.status_code == 200, (
                f"chat route refused the turn: HTTP {resp.status_code}"
            )
            gate_id, tool, thread_id = _read_leg(resp, run, leg_timeout)
        while gate_id and len(run.cycles) < max_cycles:
            approve = pending.pop(0) if pending else True
            cycle = Cycle(
                gate_id=gate_id,
                tool=tool,
                thread_id=thread_id or run.thread_id,
                decision="approve" if approve else "deny",
            )
            run.cycles.append(cycle)
            before = len(run.frames)
            ack = submit_decision(
                harness.base,
                cycle.thread_id,
                approve=approve,
                interrupt_id=gate_id,
                scope=scope,
                client=client,
            )
            cycle.ack = ack
            assert ack.get("_status") == 200, (
                f"approval route refused the decision: {ack}"
            )
            assert ack.get("ok") is True, f"approval not accepted: {ack}"
            # The route answered and handed the resume to a background task.
            # Everything it produces arrives on the journal, so re-attach —
            # the same move chat.js makes on `approve-json`.
            with attach_stream(
                harness.base, run.session_id, run.last_seq, client
            ) as resp:
                assert resp.status_code == 200, (
                    f"attach after decision failed: HTTP {resp.status_code}"
                )
                gate_id, tool, thread_id = _read_leg(resp, run, leg_timeout)
            cycle.frames = run.frames[before:]
            if gate_id and gate_id == cycle.gate_id:
                raise AssertionError(
                    f"the same gate {gate_id} was asked twice after a "
                    f"{cycle.decision} — the resume did not take"
                )
    return run
