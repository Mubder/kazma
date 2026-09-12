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
    assert ":ro" in code_exec, "the page says your code is visible, not writable"


@pytest.mark.parametrize("gap", ["cap-drop", "no-new-privileges"])
def test_the_documented_gaps_are_still_gaps(gap, code_exec):
    """The page names these two as missing. If someone adds them — good — the
    page must stop claiming they are absent."""
    assert gap not in code_exec, (
        f"{gap} was added: update THREAT_MODEL.md, it still lists this as a gap"
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
