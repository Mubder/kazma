"""The threat model must stay true, or it is worse than not having one.

`docs/THREAT_MODEL.md` makes specific, checkable claims about what each boundary
does: which flags the container runs with, which binaries the shell allows,
which tools YOLO cannot bypass. A document like that rots silently — someone
removes `--read-only` for a debugging session, and the page still says the root
filesystem is immutable.

This is section 06 of the 0.11 audit applied to the document itself: *"put the
season's real invariants in CI"*. Every assertion here corresponds to a sentence
on that page. If one fails, either the code regressed or the page is now lying,
and both are worth a build failure.

Drafting it caught one error already — the page listed three `ALWAYS_HITL_TOOLS`
and there are four.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "docs" / "THREAT_MODEL.md"
_CODE_EXEC = _ROOT / "kazma-core" / "kazma_core" / "tools" / "code_exec.py"
_SYSTEM = _ROOT / "kazma-core" / "kazma_core" / "agent" / "tool_builtins" / "system.py"


@pytest.fixture(scope="module")
def doc() -> str:
    assert _DOC.exists(), "the threat model is gone; R-5 was undone"
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code_exec() -> str:
    return _CODE_EXEC.read_text(encoding="utf-8")


# ── the container flags the page enumerates ─────────────────────────────────


@pytest.mark.parametrize(
    "flag",
    [
        '"--network", "none"',
        '"--memory"',
        '"--memory-swap"',
        '"--cpus", "1"',
        '"--pids-limit", "64"',
        '"--read-only"',
        "noexec,nosuid",
        '"--user", "65534:65534"',
        '"python", "-I"',
    ],
)
def test_every_documented_container_flag_is_real(flag, code_exec):
    """The page prints this flag list as the protection. Losing one silently
    downgrades the jail while the document still advertises it."""
    assert flag in code_exec, f"THREAT_MODEL.md claims {flag} and the code no longer has it"


def test_the_workspace_is_mounted_read_only(code_exec):
    """The page says your code is visible, not writable. Written against the
    old `-v src:dst:ro` form; the `--mount` switch made `,ro` the spelling."""
    assert ",ro" in code_exec or ":ro" in code_exec


@pytest.mark.parametrize("flag", ["--cap-drop=ALL", "--security-opt=no-new-privileges"])
def test_the_hardening_flags_stay_on(flag, code_exec):
    """These were documented as MISSING when the page was written, and this
    test asserted their absence. Adding them made it fail -- which is the guard
    working, not a nuisance: the page and the code cannot drift apart without
    a build failure in one direction or the other. Now they are present, the
    assertion is inverted and protects them from silent removal."""
    assert flag in code_exec, (
        f"{flag} was removed: the container is weaker and THREAT_MODEL.md "
        "still lists it as part of the protection"
    )


# ── the approval gate ───────────────────────────────────────────────────────


def test_the_danger_tool_count_matches(doc):
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

    assert f"{len(CANONICAL_DANGER_TOOLS)} tools are in" in doc, (
        f"the page states a tool count that is no longer {len(CANONICAL_DANGER_TOOLS)}"
    )


def test_every_yolo_proof_tool_is_named(doc):
    """The page's claim is that YOLO cannot bypass these. Naming three of four
    is the error this file caught when it was written."""
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS

    for tool in ALWAYS_HITL_TOOLS:
        assert tool in doc, f"{tool} resists YOLO and the threat model omits it"


# ── the shell surface ───────────────────────────────────────────────────────


@pytest.mark.parametrize("interpreter", ["bash", "sh", "zsh", "powershell", "cmd"])
def test_no_shell_interpreter_is_allowlisted(interpreter):
    """'No shell interpreters' is the sentence that makes the rest of the shell
    section true -- one of these on the list and the allowlist is decorative,
    because everything else becomes reachable through it."""
    body = _SYSTEM.read_text(encoding="utf-8")
    block = body.split("_SAFE_BINARIES = {", 1)[1].split("}", 1)[0]
    assert f'"{interpreter}"' not in block, (
        f"{interpreter} is allowlisted; THREAT_MODEL.md says no interpreters are"
    )


def test_env_is_not_allowlisted():
    """The page says one approval must not become a credential dump."""
    body = _SYSTEM.read_text(encoding="utf-8")
    block = body.split("_SAFE_BINARIES = {", 1)[1].split("}", 1)[0]
    assert '"env"' not in block


# ── honesty about what it is not ────────────────────────────────────────────


def test_the_page_says_docker_shares_the_kernel(doc):
    """The entire reason this document exists. If this sentence goes, the page
    has become the overstatement it was written to replace."""
    low = doc.lower()
    assert "shares your kernel" in low or "shares the kernel" in low
    assert "not a security boundary" in low


def test_the_page_does_not_call_the_container_a_jail_without_qualifying_it(doc):
    """'Jail' is the word the audit flagged as overstated. It may appear -- the
    code calls it that -- but the page must dispute it on the same screen."""
    if "jail" in doc.lower():
        assert "wrong word" in doc.lower() or "not a security boundary" in doc.lower()


def test_local_fallback_is_described_as_unsandboxed(doc):
    low = doc.lower()
    assert "no isolation" in low or "there is no jail" in low


def test_approval_is_described_as_consent_not_containment(doc):
    assert "consent, not containment" in doc.lower()


# ── the mount syntax, which was broken on Windows for the jail's whole life ──


def test_mounts_use_the_mount_flag_not_dash_v(code_exec):
    r"""`-v src:dst:mode` cannot express a Windows path.

    `G:\work` contains a colon, so docker reported "too many colons" and the
    daemon refused with exit 125 -- every `python_exec` under
    `KAZMA_CODE_EXEC_DOCKER=force` failed on Windows, for as long as the jail
    has existed. The threat model recommends forcing Docker, so the
    recommendation was unfollowable on that OS.

    Found 2026-09-12 by running it rather than reading it, which is the only
    way this class of bug ever surfaces.
    """
    assert "--mount" in code_exec, "the jail is back on -v and is broken on Windows"
    assert 'type=bind' in code_exec
    assert f'{chr(34)}-v{chr(34)}, work_mount' not in code_exec


def test_the_workspace_mount_is_read_only(code_exec):
    assert "ro" in code_exec.split("type=bind")[1][:120], (
        "the workspace mount lost its read-only flag"
    )


def test_the_page_states_the_import_blocklist_applies_in_docker(doc):
    """It is stricter than "a container with a read-only mount" suggests -- a
    snippet cannot even `import os` -- and a threat model that overstates what
    a snippet can do is as wrong as one that overstates the boundary."""
    low = doc.lower()
    assert "import blocklist" in low
    assert "container as well as locally" in low or "in the container" in low


# ── MCP tool names are the server's claim about itself ──────────────────────


def test_a_safe_sounding_mcp_name_really_does_classify_safe(doc):
    """The page says a third-party server can pick a name that skips the gate
    in the default posture. That is only worth writing down if it is true, so
    it is checked rather than asserted.

    If a future change makes name-based classification stop returning `safe`,
    this fails and the page must stop claiming the weakness.
    """
    from kazma_core.mcp.manager import classify_mcp_tool

    assert classify_mcp_tool("mcp__evil__get_file") == "safe"
    assert classify_mcp_tool("mcp__evil__read_env") == "safe"
    assert "names its own tools" in doc, (
        "a hostile MCP server can still self-classify as safe and the threat "
        "model no longer says so"
    )


def test_production_is_documented_as_the_thing_that_closes_it(doc):
    """The mitigation has to be findable, or naming the weakness is just
    alarming without being useful."""
    low = doc.lower()
    assert "kazma_mcp_safe_allowlist" in low
    assert "kazma_production=1` closes it" in low or "closes it" in low


def test_unknown_mcp_tools_still_default_to_danger():
    """The fallback that makes the rest of the MCP surface safe: a name that
    matches nothing must not bleach to `safe`."""
    from kazma_core.mcp.manager import classify_mcp_tool

    assert classify_mcp_tool("mcp__evil__exfiltrate") == "unknown"
    assert classify_mcp_tool("mcp__evil__do_thing") == "unknown"
    assert classify_mcp_tool("mcp__evil__write_file") == "danger"
