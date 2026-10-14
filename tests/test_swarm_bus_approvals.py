"""Swarm-bus approvals: one request, one id, one outcome (Swarm audit 2026-10-13, stage 1).

Each test inverts a witness in
``session-artifacts/audits-2026-10-13/test_swarm_surfaces_probes_2026_10_13.py``:

* SW-S02 -- an approval id came from the tool's own arguments, a settled
  "yes" was never removed, and one click approved every later request with
  that id. Now ``SafetyMiddleware.check`` mints the id per request, the row
  is opened once, decided once, bound to the tool and an arguments hash, and
  deleted when read.
* SW-S03 -- the card showed ~100 characters of the arguments, no mark of the
  cut. Now every argument name, a bounded value with "[N chars hidden]", and
  a hash; arguments that cannot be shown are refused.
* SW-S04 -- each adapter kept its own timer, a timeout left the card live and
  counted as a "reject". Now one deadline, and timeout and cancellation are
  outcomes of their own, shown on the card.
* SW-S05 -- under FanOut the card where Reject was pressed said REJECTED while
  another platform's Approve ran the tool. Now every card shows the one final
  outcome; a human Deny is final (owner decision D7).
* SW-S11 -- the bus gated only tiers danger/unsafe; the graph default-denies
  an unclassified tool. Now both ask ``hitl.approval_floor``.

No network: every platform call is an in-process recorder. Stores are the
test's own (the root conftest pins the data dir).
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
PLATFORMS = ("telegram", "discord", "slack")


# ── fakes ────────────────────────────────────────────────────────────────────


class _Card:
    """One platform adapter with its HTTP calls recorded."""

    def __init__(self, platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.platform = platform
        self.posts: list[str] = []
        self.buttons: list[str] = []
        self.edits: list[str] = []
        if platform == "telegram":
            from kazma_gateway.adapters.callback_store import decode_callback_data
            from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

            self.adapter = TelegramBusAdapter("demo-telegram-token", 1)

            async def post(payload: dict[str, Any], method: str = "sendMessage"):
                self.posts.append(payload["text"])
                for row in (payload.get("reply_markup") or {}).get("inline_keyboard", []):
                    self.buttons += [decode_callback_data(b["callback_data"]) for b in row]
                return {"ok": True, "result": {"message_id": len(self.posts)}}

            async def edit(message_id: Any, text: str, reply_markup: Any = None):
                self.edits.append(text)
                return {"ok": True}

            monkeypatch.setattr(self.adapter, "_post", post)
            monkeypatch.setattr(self.adapter, "_edit_message", edit)
        elif platform == "discord":
            from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

            self.adapter = DiscordBusAdapter("demo-discord-token", "1")

            async def post(payload: dict[str, Any]):
                self.posts.append(payload["content"])
                for row in payload.get("components", []):
                    self.buttons += [b["custom_id"] for b in row["components"]]
                return {"id": f"m{len(self.posts)}"}

            async def patch(message_id: str, payload: dict[str, Any]) -> None:
                self.edits.append(payload["content"])
                assert payload["components"] == [], "a decided card keeps no buttons"

            monkeypatch.setattr(self.adapter, "_post_message", post)
            monkeypatch.setattr(self.adapter, "_patch_message", patch)
        else:
            from kazma_gateway.adapters.slack_bus import SlackBusAdapter

            self.adapter = SlackBusAdapter("demo-slack-token", "C1")

            async def post(payload: dict[str, Any]):
                self.posts.append(payload["text"])
                for block in payload.get("blocks", []):
                    self.buttons += [e["value"] for e in block.get("elements", [])]
                return {"ts": f"{len(self.posts)}.0"}

            async def update(ts: str, payload: dict[str, Any]) -> None:
                self.edits.append(payload["text"])
                assert all(b.get("type") != "actions" for b in payload["blocks"]), "no buttons left"

            monkeypatch.setattr(self.adapter, "_post_message", post)
            monkeypatch.setattr(self.adapter, "_update_message", update)

    def approve_button(self, n: int = -1) -> str:
        return [b for b in self.buttons if b.startswith("swarm_approve_")][n]

    def reject_button(self, n: int = -1) -> str:
        return [b for b in self.buttons if b.startswith("swarm_reject_")][n]

    def click(self, data: str, user: str = "42") -> str | None:
        return self.adapter.handle_callback(data, actor=f"{self.platform}:{user}")


async def _until(cond, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def _id_of(button: str) -> str:
    return button.split("_", 2)[2]


def _middleware(monkeypatch: pytest.MonkeyPatch, adapter: Any, *, timeout: float = 10.0):
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.swarm.bus import SwarmMessageBus
    from kazma_core.swarm.safety import SafetyMiddleware

    class _Middleware(SafetyMiddleware):
        # A plain attribute in place of the live-Settings property (bounded to
        # 10-600 s there): these tests time out in fractions of a second.
        approval_timeout = 10.0

    bus = SwarmMessageBus(adapter)
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: bus)
    mw = _Middleware(enabled=True)
    mw.approval_timeout = timeout
    return mw


def _open(tool: str = "file_write", args: str = '{"path": "a.txt"}', *, deadline: float = 10.0) -> str:
    from kazma_core.swarm import shared_approvals as sa

    approval_id = "t-" + uuid.uuid4().hex
    sa.open_request(approval_id, tool=tool, args=args, deadline=time.time() + deadline)
    return approval_id


# ── SW-S02: an id is minted per request and decided once ───────────────────


def test_an_id_is_opened_once() -> None:
    from kazma_core.swarm import shared_approvals as sa

    approval_id = _open()
    with pytest.raises(ValueError):
        sa.open_request(approval_id, tool="git_push", args="{}", deadline=time.time() + 5)


def test_a_decision_on_an_unknown_or_closed_id_changes_nothing() -> None:
    from kazma_core.swarm import shared_approvals as sa

    unknown = "t-" + uuid.uuid4().hex
    assert sa.resolve(unknown, True, actor="telegram:1") is False
    assert sa.get(unknown) is None, "a click on an id nobody opened creates nothing"

    approval_id = _open()
    sa.close(approval_id)
    assert sa.resolve(approval_id, True) is False and sa.get(approval_id) is None

    decided = _open()
    assert sa.resolve(decided, False, actor="slack:1") is True
    assert sa.resolve(decided, True, actor="slack:2") is False, "decided once"
    assert sa.get(decided)["status"] == "denied"


def test_a_settled_approval_does_not_answer_a_later_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SW-S02 witness inverted: one click approved every later request."""
    from kazma_core.swarm import shared_approvals as sa

    card = _Card("telegram", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter)
    args = json.dumps({"path": "notes.txt", "content": "x"})

    async def first() -> bool:
        task = asyncio.create_task(mw.check("file_write", args, task_id="task-1"))
        await _until(lambda: card.buttons)
        card.click(card.approve_button())
        return await task

    assert asyncio.run(first()) is True
    first_id = _id_of(card.approve_button())
    assert sa.get(first_id) is None, "the row is gone once its owner read it"

    sa._waiters.clear()  # a later process: nothing in memory
    mw.approval_timeout = 0.5

    async def second() -> bool:
        return await mw.check("file_write", args, task_id="task-1")

    started = time.monotonic()
    assert asyncio.run(second()) is False, "nobody pressed anything"
    assert time.monotonic() - started >= 0.4, "it waited for its own deadline"
    assert len(card.posts) == 2 and _id_of(card.approve_button()) != first_id
    assert "TIMED OUT" in card.edits[-1]


def test_an_argument_named_task_id_is_not_the_approval_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SW-S02b witness inverted: a pre-approved id in the arguments is ignored."""
    from kazma_core.swarm import shared_approvals as sa

    planted = "planted-" + uuid.uuid4().hex[:8]
    sa.open_request(planted, tool="git_push", args="{}", deadline=time.time() + 60)
    sa.resolve(planted, True, actor="someone")
    card = _Card("discord", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.5)

    ok = asyncio.run(mw.check("git_push", json.dumps({"task_id": planted}), task_id=planted))
    assert ok is False
    assert _id_of(card.approve_button()) != planted
    assert sa.get(planted)["status"] == "approved", "the planted row decided nothing else"


def test_two_cards_for_the_same_call_need_two_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _Card("slack", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=1.5)
    args = json.dumps({"command": "ls"})

    async def go() -> tuple[bool, bool]:
        a = asyncio.create_task(mw.check("shell_exec", args))
        b = asyncio.create_task(mw.check("shell_exec", args))
        await _until(lambda: len(card.posts) == 2)
        assert _id_of(card.approve_button(0)) != _id_of(card.approve_button(1))
        card.click(card.approve_button(0))
        return await a, await b

    first, second = asyncio.run(go())
    assert (first, second) == (True, False)


def test_the_gate_registry_records_the_human_and_the_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety.hitl_gates import gate_for, gate_registry_enabled

    if not gate_registry_enabled():
        pytest.skip("gate registry switched off in this environment")
    card = _Card("telegram", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter)

    async def approve() -> bool:
        task = asyncio.create_task(mw.check("file_write", '{"path": "b.txt"}'))
        await _until(lambda: card.buttons)
        card.click(card.approve_button(), user="7")
        return await task

    assert asyncio.run(approve()) is True
    row = gate_for(_id_of(card.approve_button()))
    assert row is not None and row.mechanism == "swarm_bus"
    assert (row.state, row.decision, row.actor) == ("settled", "approve", "telegram:7")
    assert json.loads(row.args_json) == {"path": "b.txt"}, "the whole call is on the record"

    mw.approval_timeout = 0.3
    assert asyncio.run(mw.check("file_write", '{"path": "c.txt"}')) is False
    timed_out = gate_for(_id_of(card.approve_button()))
    assert timed_out is not None and timed_out.state == "timeout", "a timeout is not a deny"


def _argument_derived_ids(tree: ast.AST) -> list[int]:
    """Lines where an approval request's ``task_id`` comes from the call's arguments."""
    found: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(
                isinstance(n, ast.Name) and n.id == "arguments" for n in ast.walk(node.value)
            ):
                tainted |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("check", "request_approval")):
                continue
            for kw in node.keywords:
                if kw.arg != "task_id":
                    continue
                names = {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
                if "arguments" in names or names & tainted:
                    found.append(node.lineno)
    return found


def test_no_caller_takes_the_approval_id_from_the_arguments() -> None:
    old = (
        "async def _execute_inner(self, tool_name, arguments):\n"
        "    task_id = str(arguments.get('task_id', ''))\n"
        "    approved = await safety.check(tool_name=tool_name, task_id=task_id)\n"
        "async def mcp(tool_name, arguments):\n"
        "    await safety.check(tool_name=tool_name, task_id=str(arguments.get('task_id', '')))\n"
    )
    assert len(_argument_derived_ids(ast.parse(old))) == 2, "negative control: the old callers"

    bad: list[str] = []
    for pkg in sorted(REPO.glob("kazma-*/kazma_*")):
        for path in pkg.rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bad += [f"{path.relative_to(REPO)}:{line}" for line in _argument_derived_ids(tree)]
    assert not bad, f"an approval id taken from the tool's own arguments: {bad}"
    safety_src = (REPO / "kazma-core/kazma_core/swarm/safety.py").read_text(encoding="utf-8")
    assert "approval_id = task_id" not in safety_src


# ── SW-S03: the card shows every argument ───────────────────────────────────


def test_the_card_shows_every_argument_name_and_marks_a_cut() -> None:
    from kazma_core.swarm.approval_card import render_arguments

    args = {"content": "Meeting notes: " + "a" * 3000, "path": "/home/op/.ssh/authorized_keys"}
    old_view = json.dumps(args, default=str)[:200]
    assert "authorized_keys" not in old_view, "negative control: the old card"

    card = render_arguments(json.dumps(args))
    assert card.complete
    assert "path: /home/op/.ssh/authorized_keys" in card.text
    assert "chars hidden]" in card.text and f"arguments sha256: {card.digest}" in card.text
    assert len(card.text) <= 1350


def test_the_ordering_case_reaches_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _Card("telegram", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    args = json.dumps({"content": "Meeting notes: " + "a" * 1500, "path": "/home/op/.ssh/authorized_keys"})
    assert asyncio.run(mw.check("file_write", args)) is False
    assert "authorized_keys" in card.posts[0] and "chars hidden]" in card.posts[0]


def test_arguments_that_cannot_be_shown_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm.approval_card import render_arguments

    many = json.dumps({f"argument_number_{i:03d}": "v" for i in range(60)})
    assert render_arguments(many).complete is False
    card = _Card("discord", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter)
    assert asyncio.run(mw.check("file_write", many)) is False
    assert card.posts == [], "no card the operator cannot read"


def test_every_danger_tools_arguments_reach_every_platforms_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Property: for every registered danger tool, every schema property name,
    with long values, is on the Telegram, Discord and Slack card."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.approval_card import render_arguments
    from kazma_core.swarm.bus import ApprovalRequest

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    tools = sorted(set(CANONICAL_DANGER_TOOLS) & set(registry._tools))
    assert len(tools) >= 5, tools
    cards = {p: _Card(p, monkeypatch) for p in PLATFORMS}

    async def go() -> list[str]:
        missing: list[str] = []
        for tool in tools:
            props = list((registry._tools[tool].input_schema or {}).get("properties") or {})
            args = json.dumps({p: "v" * 400 for p in props})
            shown = render_arguments(args)
            assert shown.complete, tool
            for platform, card in cards.items():
                approval_id = _open(tool, args)
                task = asyncio.create_task(card.adapter.request_approval(
                    ApprovalRequest(f"mcp:{'w' * 60}", f"Tool: {tool}", shown.text, task_id=approval_id),
                    timeout=10,
                ))
                count = len(card.posts)
                await _until(lambda c=card, n=count: len(c.posts) > n)
                await asyncio.to_thread(sa.settle, approval_id, "cancelled")
                assert await task is False
                posted = card.posts[-1]
                missing += [f"{platform}:{tool}:{p}" for p in props if f"{p}: " not in posted]
                if f"arguments sha256: {shown.digest}" not in posted:
                    missing.append(f"{platform}:{tool}:<hash>")
        return missing

    assert asyncio.run(go()) == []


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_card_links_to_the_request_on_the_web(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """SW-S03's last part: the card links to the whole call on the Web
    dashboard, outside the code block (a link inside it cannot be opened)
    and before it (no platform cut reaches it)."""
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example/")
    card = _Card(platform, monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    assert asyncio.run(mw.check("file_write", '{"path": "a.txt"}')) is False
    posted = card.posts[0]
    link = f"https://kazma.example/swarm?approval={_id_of(card.approve_button())}"
    assert link in posted, posted
    assert posted.index(link) < posted.index("```")


def test_the_widest_discord_card_keeps_its_link_and_its_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the link added, the widest argument block still fits Discord's
    2,000 characters: nothing of the card is cut."""
    from kazma_core.swarm.approval_card import render_arguments

    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example/a-long-public-path-of-an-install")
    card = _Card("discord", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    widest = json.dumps({f"argument_{i:02d}": "v" * 900 for i in range(14)})
    assert asyncio.run(mw.check(f"mcp__{'s' * 60}__write_file", widest)) is False
    posted = card.posts[0]
    assert len(posted) < 2000, len(posted)
    assert f"swarm?approval={_id_of(card.approve_button())}>" in posted
    assert f"arguments sha256: {render_arguments(widest).digest}" in posted


def test_without_a_public_address_the_card_names_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """An address that is not http(s) is no link: the card names the page."""
    from kazma_core.swarm.approval_card import web_link

    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    assert web_link("g-1") == "/swarm?approval=g-1"
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "kazma.example")
    assert web_link("g 1/x") == "/swarm?approval=g%201%2Fx"
    card = _Card("slack", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter, timeout=0.3)
    asyncio.run(mw.check("file_write", '{"path": "a.txt"}'))
    assert f"`/swarm?approval={_id_of(card.approve_button())}`" in card.posts[0]


# ── SW-S04: one deadline; timeout and cancellation are shown ───────────────


def _no_stray_tasks_or_errors(loop_errors: list[dict[str, Any]]) -> None:
    current = asyncio.current_task()
    stray = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    assert stray == [], stray
    assert loop_errors == [], loop_errors


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_timeout_edits_the_card_and_is_not_a_deny(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.bus import ApprovalRequest

    card = _Card(platform, monkeypatch)
    approval_id = _open(deadline=0.3)

    async def go() -> bool:
        errors: list[dict[str, Any]] = []
        asyncio.get_running_loop().set_exception_handler(lambda _l, ctx: errors.append(ctx))
        ok = await card.adapter.request_approval(
            ApprovalRequest("w", "Tool: file_write", "path: a.txt", task_id=approval_id), timeout=0.3)
        await asyncio.sleep(0.05)
        _no_stray_tasks_or_errors(errors)
        return ok

    assert asyncio.run(go()) is False
    assert "TIMED OUT" in card.edits[-1] and "APPROVAL REQUIRED" not in card.edits[-1]
    assert sa.get(approval_id)["status"] == "timeout"
    assert card.adapter.pending_count == 0


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_cancelled_wait_edits_the_card_cancelled(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.bus import ApprovalRequest

    card = _Card(platform, monkeypatch)
    approval_id = _open()

    async def go() -> None:
        task = asyncio.create_task(card.adapter.request_approval(
            ApprovalRequest("w", "Tool: file_write", "path: a.txt", task_id=approval_id), timeout=10))
        await _until(lambda: card.posts)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert "CANCELLED" in card.edits[-1]
    assert sa.get(approval_id)["status"] == "cancelled"


def test_a_swarm_task_cancelled_while_asking_ends_the_request_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.safety.hitl_gates import gate_for, gate_registry_enabled
    from kazma_core.swarm import shared_approvals as sa

    card = _Card("discord", monkeypatch)
    mw = _middleware(monkeypatch, card.adapter)

    async def go() -> None:
        task = asyncio.create_task(mw.check("git_push", '{"remote": "origin"}'))
        await _until(lambda: card.posts)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    approval_id = _id_of(card.approve_button())
    assert "CANCELLED" in card.edits[-1]
    assert sa.get(approval_id) is None, "closed by its owner"
    if gate_registry_enabled():
        row = gate_for(approval_id)
        assert row is not None and (row.state, row.decision) == ("settled", "cancelled")


# ── SW-S05: every FanOut card shows the one outcome ─────────────────────────


@pytest.mark.parametrize("ending", ["approve", "deny", "deny_then_approve", "timeout", "cancel"])
def test_every_card_shows_the_final_outcome(ending: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.swarm import shared_approvals as sa
    from kazma_core.swarm.bus import ApprovalRequest, FanOutBusAdapter

    cards = [_Card(p, monkeypatch) for p in PLATFORMS]
    tg, dc, sl = cards
    fan = FanOutBusAdapter([c.adapter for c in cards])
    approval_id = _open(deadline=0.5 if ending == "timeout" else 10)

    async def go() -> bool:
        task = asyncio.create_task(fan.request_approval(
            ApprovalRequest("w", "Tool: git_push", "remote: origin", task_id=approval_id), timeout=10))
        await _until(lambda: all(c.posts for c in cards))
        if ending == "approve":
            dc.click(dc.approve_button())
        elif ending == "deny":
            dc.click(dc.reject_button())
        elif ending == "deny_then_approve":
            dc.click(dc.reject_button())
            await _until(lambda: (sa.get(approval_id) or {}).get("status") == "denied")
            tg.click(tg.approve_button(), user="99")
        elif ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return False
        return await asyncio.wait_for(task, 5)

    result = asyncio.run(go())
    expected = {
        "approve": "APPROVED by discord:42",
        "deny": "DENIED by discord:42",
        "deny_then_approve": "DENIED by discord:42",
        "timeout": "TIMED OUT",
        "cancel": "CANCELLED",
    }[ending]
    assert result is (ending == "approve")
    for card in cards:
        assert card.edits and expected in card.edits[-1], (card.platform, card.edits)
    assert sa.get(approval_id)["status"] == {
        "approve": "approved", "deny": "denied", "deny_then_approve": "denied",
        "timeout": "timeout", "cancel": "cancelled",
    }[ending]


def test_a_platform_button_decides_with_the_user_who_pressed() -> None:
    from kazma_core.swarm import shared_approvals as sa
    from kazma_gateway.adapters import discord_callbacks, slack_callbacks

    a = _open()
    assert discord_callbacks.route_swarm_bus(f"swarm_approve_{a}", actor="discord:42") == a
    assert (sa.get(a)["status"], sa.get(a)["actor"], sa.get(a)["platform"]) == ("approved", "discord:42", "discord")
    b = _open()
    assert slack_callbacks.route_swarm_bus(f"swarm_reject_{b}", actor="slack:U1") == b
    assert (sa.get(b)["status"], sa.get(b)["actor"]) == ("denied", "slack:U1")
    assert discord_callbacks.route_swarm_bus("hitl:approve:x") is None


def test_no_click_handler_writes_its_own_outcome_label() -> None:
    """The click handlers used to replace the card with "Approved"/"Rejected"
    from the button pressed, whatever the request ended as (SW-S05)."""
    old = 'action_label = "✅ Approved" if "approve" in custom_id else "❌ Rejected"'
    assert '"approve" in' in old, "negative control: the old label rule"
    for name in ("discord.py", "slack.py", "telegram.py"):
        src = (REPO / "kazma-gateway/kazma_gateway/adapters" / name).read_text(encoding="utf-8")
        assert '"✅ Approved" if "approve" in' not in src, name
        assert "Task approval: {label}" not in src, name


# ── SW-S11: the bus gates what the graph gates ─────────────────────────────


def test_the_bus_gates_what_the_graph_gates() -> None:
    from kazma_core.safety.hitl import TOOL_TIERS, get_hitl_config, get_tool_tier, requires_approval
    from kazma_core.swarm.safety import SafetyMiddleware

    cfg = get_hitl_config({})
    mw = SafetyMiddleware()
    names = [*TOOL_TIERS, "probe_tool_nobody_classified", "mcp__probe__write_file", "probe_runtime_added"]

    def old_classifier(name: str) -> bool:
        listed = set(cfg.get("require_approval_for") or ()) | set(mw._danger_tools)
        return name in listed or get_tool_tier(name) in ("danger", "unsafe")

    assert old_classifier("probe_tool_nobody_classified") is False, "negative control: the old bus"
    assert requires_approval("probe_tool_nobody_classified", cfg) is True

    differ = [n for n in names if mw.is_danger_tool(n) != requires_approval(n, cfg)]
    assert differ == [], differ
