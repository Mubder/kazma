"""Every model call is held to the person's daily limit, or declared (Spaces S2-02).

A person's daily limit (``kazma_core/spaces/daily_limits.py``) is asked at
three places: the supervisor at a turn's first step (``verdict``),
``resilient_chat`` before any provider (``call_refusal``: a swarm worker's,
research's), and X Studio's ``x_chat`` (``call_refusal``). A model call made
anywhere else for a person is spend the limit never stops: X Studio's calls
went to the client directly until the review of 22 November, so a member
with X Studio drafted past their limit.

So every product model call -- a client's ``.chat(`` or ``.chat_stream(``,
``invoke_llm_chat(``, and a client's ``.chat`` handed on to be called
later -- is in a function that asks ``daily_limits.call_refusal`` or
``daily_limits.verdict`` itself (or inside one that does), or is declared
here with why:

* ``TURN``: it runs in a turn whose first step asked the person's limit
  (the supervisor), or in a tool, which runs in such a turn or in a swarm
  worker whose every model call ``resilient_chat`` checks;
* ``KAZMA_OWN``: Kazma's own work after a turn that was allowed (the deep
  pass, entity resolution, the lessons, weekly summaries). Not refused
  until S2-07, which makes background work use its person's limit. Since
  the memory plan's finding 6 they go through the per-call ledger
  (``llm_ledger.ledgered_chat``), so they count toward the person's limit
  the next time it is asked;
* ``ADMIN``: the admin's own work, never a person's;
* ``CLIENT``: a client's own retries, fallbacks or delegation to the client
  it wraps; whoever calls the client is in this list;
* ``NOT_A_MODEL``: a ``.chat`` that is data (a chat's id), or a method
  compared, not called.

A declaration names how many such sites its function holds: one more is
refused until it is declared too. A declaration that matches nothing fails.
The controls: a planted direct call is found, and ``x_chat`` and
``resilient_chat`` without their checks are found.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: A client's methods that call a model.
_METHODS = frozenset({"chat", "chat_stream"})
#: Helpers that call a model: the streaming helper, and the ledger's
#: wrapper memory's own calls go through (finding 6).
_HELPERS = frozenset({"invoke_llm_chat", "ledgered_chat"})
#: The asks of the person's limit (``kazma_core.spaces.daily_limits``).
_CHECKS = frozenset({"call_refusal", "verdict"})
_LIMITS = "kazma_core.spaces.daily_limits"

TURN = ("runs in a turn whose first step asked the person's limit (the supervisor, "
        "graph_supervisor: verdict at iteration 0)")
TOOL = ("a tool's own model call: a tool runs in a turn the supervisor checked, or in a "
        "swarm worker whose every model call resilient_chat checks")
KAZMA_OWN = ("Kazma's own work after a turn that was allowed; not refused until S2-07; "
             "in the per-call ledger (ledgered_chat, finding 6), so counted toward the person's limit")
ADMIN = "the admin's own work, never a person's"
CLIENT = "a client's own call: its retries and fallbacks, or the client it wraps; its caller is listed here"
OWN_KEY = (CLIENT + " (Spaces S2-04: the call handed whole to the client carrying the person's own key; "
           "the limit was asked by the call's caller, and own-key spend is not counted)")

#: (file, function) -> (sites, why).
DECLARED: dict[tuple[str, str], tuple[int, str]] = {
    # ── in a checked turn ──
    ("kazma-core/kazma_core/agent/graph_respond.py", "respond_node"): (1, TURN + "; respond follows it"),
    ("kazma-core/kazma_core/agent/answer_format.py", "format_terminal_answer"): (
        1, TURN + "; respond_node's one-paragraph repair"),
    ("kazma-core/kazma_core/agent/intent/classify.py", "_refine_acts_llm"): (
        1, TURN + "; the intent engine runs after the limit check (classify_turn)"),
    ("kazma-core/kazma_core/agent/intent/handlers/document.py", "run_document"): (
        1, TURN + "; an intent handler the supervisor runs"),
    ("kazma-core/kazma_core/compaction.py", "CompactionEngine._summarize_with_llm"): (
        1, TURN + "; the summary of what a trim dropped (semantic_compact.inject_summary_of_dropped)"),
    ("kazma-core/kazma_core/tools/computer_use.py", "_default_chat"): (2, TOOL),
    ("kazma-core/kazma_core/tools/computer_use_planners.py", "_native_gemini._chat"): (1, TOOL),
    ("kazma-core/kazma_core/tools/vision_analyze.py", "analyze_image"): (1, TOOL),
    ("kazma-skills/kazma_skills/native/email_manager/analyze.py", "_llm_analyze"): (
        1, TOOL + " (email_analyze)"),
    # ── Kazma's own work after a turn ──
    ("kazma-core/kazma_core/memory/belief_extractor.py", "_llm_extract"): (1, KAZMA_OWN + " (the deep pass)"),
    ("kazma-core/kazma_core/memory/topic_summaries.py", "_default_chat._chat"): (
        1, KAZMA_OWN + " (weekly summaries and roll-ups: the chat handed to the writer)"),
    ("kazma-core/kazma_core/skills/self_improvement.py", "SelfImprovementSkill._analyze_success"): (
        1, KAZMA_OWN + " (a swarm worker's lessons)"),
    ("kazma-core/kazma_core/skills/self_improvement.py", "SelfImprovementSkill._analyze_failure"): (
        1, KAZMA_OWN + " (a swarm worker's lessons)"),
    ("kazma-core/kazma_core/skills/self_improvement.py", "analyze_and_apply_chat_turn"): (
        1, KAZMA_OWN + " (the lesson refiner, only on a signal)"),
    # ── the admin's own ──
    ("kazma-core/kazma_core/provider_check.py", "check_model"): (
        1, ADMIN + ": Settings' provider Test and the setup guide, admin routes; and a member's own "
                   "key before it is kept (S2-04), on their key, which no limit counts"),
    ("kazma-core/kazma_core/mcp/spec_client.py", "complete_mcp_sample"): (
        1, ADMIN + ": an MCP server's sampling request; MCP servers are Development (S1-20), and "
                   "each request is an approval card the admin answers"),
    ("kazma-core/kazma_core/memory/backfill_v2.py", "_llm_extract_beliefs_from_memories"): (
        1, ADMIN + ": the memory backfill run on the machine"),
    ("kazma-core/kazma_core/memory/benchmark.py", "_CapturingClient.chat"): (
        1, ADMIN + ": the benchmark's recorder, a wrapper of the client it records"),
    ("kazma-core/kazma_core/agent_evaluation.py", "evaluate_case.PinnedModel.chat"): (
        1, ADMIN + ": scripts/live_eval.py, a wrapper the real graph calls"),
    # ── a client's own ──
    ("kazma-core/kazma_core/llm_stream.py", "invoke_llm_chat"): (3, CLIENT + " (the streaming helper itself)"),
    ("kazma-core/kazma_core/observability/llm_ledger.py", "ledgered_chat"): (
        1, CLIENT + " (the ledger's wrapper: one call and its row)"),
    ("kazma-core/kazma_core/llm_stream.py", "_has_chat_stream"): (1, CLIENT + " (asks whether it streams)"),
    ("kazma-core/kazma_core/llm_provider.py", "LLMProvider._chat_stream_once"): (3, CLIENT),
    ("kazma-core/kazma_core/llm_provider.py", "LLMProvider.can_turn_off_reasoning"): (
        2, CLIENT + " (compares the class's own chat with LLMProvider's)"),
    ("kazma-core/kazma_core/llm_provider.py", "LLMProvider.chat"): (1, OWN_KEY),
    ("kazma-core/kazma_core/llm_provider.py", "LLMProvider.chat_stream"): (1, OWN_KEY),
    ("kazma-core/kazma_core/anthropic_llm.py", "AnthropicProvider.chat"): (1, OWN_KEY),
    ("kazma-core/kazma_core/anthropic_llm.py", "AnthropicProvider.chat_stream"): (
        3, CLIENT + "; one of them " + OWN_KEY),
    ("kazma-core/kazma_core/azure_llm.py", "AzureProvider.chat"): (1, CLIENT),
    ("kazma-core/kazma_core/azure_llm.py", "AzureProvider.chat_stream"): (1, CLIENT),
    ("kazma-core/kazma_core/bedrock_llm.py", "BedrockProvider.chat"): (1, OWN_KEY),
    ("kazma-core/kazma_core/bedrock_llm.py", "BedrockProvider.chat_stream"): (
        2, CLIENT + "; one of them " + OWN_KEY),
    ("kazma-core/kazma_core/agent_runner.py", "KazmaAgent._make_compaction_client._CompactionLLM.chat"): (
        1, CLIENT + " (the compaction engine's client, ContextAuthority's, which no product code runs)"),
    # ── no product caller ──
    ("kazma-core/kazma_core/summarizer.py", "summarize"): (
        1, "no product code calls summarize (the module's own example); a caller added asks the limit"),
}

#: ``.chat`` that is data, not a model: (file, function) -> (sites, why).
_TURN_CHAT = "a stored turn's chat id (_Turn.chat), not a model"
NOT_A_MODEL: dict[tuple[str, str], tuple[int, str]] = {
    ("kazma-core/kazma_core/memory/topic_summaries.py", "_meaning_groups"): (4, _TURN_CHAT),
    ("kazma-core/kazma_core/memory/topic_summaries.py", "_messages"): (1, _TURN_CHAT),
    ("kazma-core/kazma_core/memory/topic_summaries.py", "_store"): (2, _TURN_CHAT),
    ("kazma-core/kazma_core/memory/topic_summaries.py", "_topic_groups"): (2, _TURN_CHAT),
    ("kazma-core/kazma_core/memory/topic_summaries.py", "summarize_period.prepare"): (1, _TURN_CHAT),
    ("kazma-core/kazma_core/observability/ops_alerts.py", "_alerts_bot_send"): (
        1, "the alerts bot's Telegram chat id (AlertsBot.chat), not a model"),
    ("kazma-core/kazma_core/observability/ops_alerts.py", "pager_credentials"): (
        1, "the alerts bot's Telegram chat id (AlertsBot.chat), not a model"),
}


@dataclass(frozen=True)
class Site:
    file: str
    function: str
    line: int
    text: str
    checked: bool


def _limit_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """The names this module binds to ``daily_limits`` (module aliases) and
    to its checks (``from ... import call_refusal``)."""
    modules: set[str] = set()
    checks: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "kazma_core.spaces":
            modules.update(a.asname or a.name for a in node.names if a.name == "daily_limits")
        elif isinstance(node, ast.ImportFrom) and node.module == _LIMITS:
            checks.update(a.asname or a.name for a in node.names if a.name in _CHECKS)
        elif isinstance(node, ast.Import):
            modules.update(a.asname for a in node.names if a.name == _LIMITS and a.asname)
    return modules, checks


def _own_nodes(function: ast.AST):
    """The nodes of *function*'s own body: not of a function or class nested in it."""
    todo = list(ast.iter_child_nodes(function))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        todo.extend(ast.iter_child_nodes(node))


def _asks_the_limit(function: ast.AST, modules: set[str], checks: set[str]) -> bool:
    """Whether *function*'s own body asks the person's limit: calls a check,
    or hands one to a thread (``asyncio.to_thread(daily_limits.call_refusal)``,
    as the loop's callers must)."""
    for node in _own_nodes(function):
        if (isinstance(node, ast.Attribute) and node.attr in _CHECKS
                and isinstance(node.value, ast.Name) and node.value.id in modules):
            return True
        if isinstance(node, ast.Name) and node.id in checks:
            return True
    return False


def _model_site(node: ast.AST, called: set[int]) -> str | None:
    """The model call or hand-on *node* is, or None."""
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in _METHODS:
            return ast.unparse(fn) + "("
        if isinstance(fn, ast.Name) and fn.id in _HELPERS:
            return fn.id + "("
        if isinstance(fn, ast.Attribute) and fn.attr in _HELPERS:
            return ast.unparse(fn) + "("
        if (isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and node.args[1].value in _METHODS):
            return ast.unparse(node)
    if (isinstance(node, ast.Attribute) and node.attr in _METHODS and id(node) not in called
            and isinstance(node.ctx, ast.Load)):
        return ast.unparse(node)
    return None


def model_call_sites(file: str, source: str) -> list[Site]:
    """Every model call and hand-on in *source*, with whether a function
    around it asks the person's limit."""
    tree = ast.parse(source)
    modules, checks = _limit_names(tree)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    found: list[Site] = []

    def visit(node: ast.AST, names: list[str], functions: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*names, child.name], [*functions, child])
                continue
            if isinstance(child, ast.ClassDef):
                visit(child, [*names, child.name], functions)
                continue
            text = _model_site(child, called)
            if text is not None:
                checked = any(_asks_the_limit(f, modules, checks) for f in functions)
                found.append(Site(file, ".".join(names) or "<module>", child.lineno, text, checked))
            visit(child, names, functions)

    visit(tree, [], [])
    return found


def _product_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/kazma_*/**.py"], cwd=ROOT, capture_output=True,
                            text=True, check=True).stdout.split()
    return [f for f in listed if "/tests/" not in f and "_tests/" not in f]


@pytest.fixture(scope="module")
def sites() -> list[Site]:
    out: list[Site] = []
    for file in _product_files():
        source = (ROOT / file).read_text(encoding="utf-8")
        if not any(word in source for word in ("chat", *_HELPERS)):
            continue
        out.extend(model_call_sites(file, source))
    return out


def _unchecked(found: list[Site]) -> dict[tuple[str, str], list[Site]]:
    out: dict[tuple[str, str], list[Site]] = {}
    for site in found:
        if not site.checked:
            out.setdefault((site.file, site.function), []).append(site)
    return out


def test_every_model_call_asks_the_limit_or_is_declared(sites) -> None:
    declared = {**DECLARED, **NOT_A_MODEL}
    problems = []
    for key, found in sorted(_unchecked(sites).items()):
        if key not in declared:
            problems.extend(f"{s.file}:{s.line} {s.function}: {s.text}" for s in found)
        elif len(found) != declared[key][0]:
            problems.append(f"{key[0]} {key[1]}: {len(found)} model call(s), declared {declared[key][0]}")
    assert not problems, (
        "A model call outside the person's daily limit (Spaces S2-02). Ask "
        "daily_limits.call_refusal() before it (as resilient_chat and x_chat do), "
        "or declare it here with why:\n  " + "\n  ".join(problems))


def test_no_declaration_is_stale(sites) -> None:
    unchecked = _unchecked(sites)
    stale = [f"{f} {fn}" for (f, fn) in {**DECLARED, **NOT_A_MODEL} if (f, fn) not in unchecked]
    assert not stale, "declared but holds no unchecked model call:\n  " + "\n  ".join(stale)


def test_the_checked_places_are_found_checked(sites) -> None:
    checked = {(s.file, s.function) for s in sites if s.checked}
    for place in [("kazma-core/kazma_core/x_api/model_selection.py", "x_chat"),
                  ("kazma-core/kazma_core/agent/resilient_chat.py", "resilient_chat"),
                  ("kazma-core/kazma_core/agent/graph_supervisor.py", "supervisor_node"),
                  ("kazma-core/kazma_core/agent/graph_supervisor.py", "supervisor_node._call_llm_with_retry")]:
        assert place in checked, place


def test_x_chat_and_resilient_chat_are_not_declared() -> None:
    names = {fn for (_f, fn) in DECLARED}
    assert "x_chat" not in names and "resilient_chat" not in names


# ── controls ─────────────────────────────────────────────────────────────


_PLANTED = '''
async def draft_for_them(client, messages):
    return await client.chat(messages, max_tokens=200)

def hand_on(client):
    return client.chat
'''


def test_control_a_planted_direct_call_and_hand_on_are_found() -> None:
    found = model_call_sites("kazma-core/kazma_core/x_api/planted.py", _PLANTED)
    assert [(s.function, s.checked) for s in found] == [("draft_for_them", False), ("hand_on", False)]
    assert _unchecked(found)


def test_control_the_same_call_asking_the_limit_is_checked() -> None:
    asking = _PLANTED.replace(
        "    return await client.chat(messages, max_tokens=200)",
        "    from kazma_core.spaces import daily_limits\n"
        "    if daily_limits.call_refusal():\n"
        "        raise ValueError('over')\n"
        "    return await client.chat(messages, max_tokens=200)")
    found = model_call_sites("kazma-core/kazma_core/x_api/planted.py", asking)
    assert [(s.function, s.checked) for s in found] == [("draft_for_them", True), ("hand_on", False)]


def test_control_a_check_in_a_nested_function_does_not_count() -> None:
    nested = '''
async def draft(client):
    def later():
        from kazma_core.spaces import daily_limits
        return daily_limits.call_refusal()
    return await client.chat([])
'''
    [site] = model_call_sites("planted.py", nested)
    assert not site.checked


@pytest.mark.parametrize(("file", "function", "check"), [
    ("kazma-core/kazma_core/x_api/model_selection.py", "x_chat", "daily_limits.call_refusal"),
    ("kazma-core/kazma_core/agent/resilient_chat.py", "resilient_chat", "daily_limits.call_refusal"),
])
def test_control_without_its_check_the_call_is_found(file, function, check) -> None:
    source = (ROOT / file).read_text(encoding="utf-8")
    assert check in source
    found = model_call_sites(file, source.replace(check, "daily_limits.may_apply"))
    missing = _unchecked(found).get((file, function))
    assert missing and (file, function) not in DECLARED
