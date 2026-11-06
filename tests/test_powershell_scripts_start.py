"""Every PowerShell script Kazma ships can start.

setup.ps1 declared its own ``-Debug`` switch under ``[CmdletBinding()]``. The
attribute makes a script "advanced", and an advanced script gets PowerShell's
common parameters, ``-Debug`` among them; PowerShell then refuses to invoke
it ("A parameter with the name 'Debug' was defined multiple times for the
command"), on 5.1 and 7 alike, before its first line runs. From 2026-06-27
to 2026-11-06 the Windows install's first step could not be run at all; it
was found setting up the drill VM. Nothing caught it because nothing ran the
script: parsing it, ``Get-Command`` and its parameter sets all succeed, and
only an invocation fails.

The gate's first run found a second one: scripts/industry_smoke.ps1 did not
parse on Windows PowerShell 5.1 ("The string is missing the terminator").
5.1 reads a script with no byte-order mark in the ANSI code page, so the
UTF-8 em dash in a string (bytes E2 80 94) read as "â€”" -- and 0x94 is a
closing curly quote, which PowerShell takes for a double quote. Three more
scripts held non-ASCII text with no BOM and parsed by luck. A script with
any non-ASCII byte must start with a UTF-8 BOM (PowerShell 7 reads either).

So the gate reads every tracked script's param block -- with PowerShell's
own parser where a PowerShell is installed, and with a reading here that
needs none (CI's Linux job may have no pwsh) -- and an advanced script
(``[CmdletBinding()]``, or ``[Parameter()]`` on any parameter) must not
declare a common parameter. And setup.ps1 is run in an empty folder, where it
gets past its banner and stops at its first checks; the copy with the
attribute put back is each check's negative control.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# PowerShell's own list, as 5.1 and 7 answer
# [Cmdlet]::CommonParameters + [Cmdlet]::OptionalCommonParameters
# (7 adds ProgressAction). The PowerShell check asks PowerShell itself.
COMMON_PARAMETERS = frozenset({
    "Verbose", "Debug", "ErrorAction", "WarningAction", "InformationAction",
    "ProgressAction", "ErrorVariable", "WarningVariable", "OutVariable",
    "OutBuffer", "PipelineVariable", "InformationVariable", "WhatIf",
    "Confirm", "UseTransaction",
})

_REFUSED = "defined multiple times"


def _tracked_scripts() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.ps1", "*.psm1"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line.strip()]


def _shells() -> list[str]:
    return [s for s in ("powershell.exe", "pwsh") if shutil.which(s)]


# ── the reading that needs no PowerShell ──────────────────────────────────


def _without_comments(text: str) -> str:
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return re.sub(r"(?m)#.*$", "", text)


def _param_block(text: str) -> tuple[str, str] | None:
    """(what comes before ``param(``, the block's inside) of the script's
    own param block, or None. The script's block is the first ``param(``
    outside any function and braces."""
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            m = re.match(r"param\s*\(", text[i:], flags=re.I)
            if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_-$")):
                start = i + m.end()
                level, j = 1, start
                while j < len(text) and level:
                    level += {"(": 1, ")": -1}.get(text[j], 0)
                    j += 1
                return text[:i], text[start:j - 1]
        i += 1
    return None


def static_conflicts(text: str) -> list[str]:
    """Common parameters an advanced script declares, read without
    PowerShell."""
    found = _param_block(_without_comments(text))
    if found is None:
        return []
    before, inside = found
    advanced = bool(re.search(r"\[\s*CmdletBinding\b", before, flags=re.I)) or bool(
        re.search(r"\[\s*Parameter\s*\(", inside, flags=re.I))
    if not advanced:
        return []
    names = {n.lower() for n in re.findall(r"\$(?!env:|script:|global:)(\w+)\b", inside, flags=re.I)}
    # PowerShell's parameter names ignore case: $debug is -Debug too.
    return sorted(c for c in COMMON_PARAMETERS if c.lower() in names)


# ── PowerShell's own reading ──────────────────────────────────────────────

_PROBE = r"""
$common = [System.Management.Automation.Cmdlet]::CommonParameters +
          [System.Management.Automation.Cmdlet]::OptionalCommonParameters
$out = foreach ($p in ($env:KAZMA_PS_PATHS -split '\|')) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors)
    $pb = $ast.ParamBlock
    $advanced = $false
    $names = @()
    if ($pb) {
        foreach ($a in $pb.Attributes) {
            if ($a.TypeName.GetReflectionAttributeType() -eq [System.Management.Automation.CmdletBindingAttribute]) { $advanced = $true }
        }
        foreach ($par in $pb.Parameters) {
            $names += $par.Name.VariablePath.UserPath
            foreach ($a in $par.Attributes) {
                if ($a -is [System.Management.Automation.Language.AttributeAst] -and
                    $a.TypeName.GetReflectionAttributeType() -eq [System.Management.Automation.ParameterAttribute]) { $advanced = $true }
            }
        }
    }
    $conflicts = @()
    if ($advanced) { $conflicts = @($names | Where-Object { $common -contains $_ }) }
    [pscustomobject]@{ path = $p; errors = @($errors | ForEach-Object { $_.Message }); conflicts = $conflicts }
}
ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
"""


def powershell_reading(shell: str, paths: list[Path], tmp_path: Path) -> dict[str, dict]:
    probe = tmp_path / "probe.ps1"
    probe.write_text(_PROBE, encoding="utf-8-sig")
    env = dict(os.environ, KAZMA_PS_PATHS="|".join(str(p) for p in paths))
    done = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert done.returncode == 0, done.stderr
    return {Path(row["path"]).name: row for row in json.loads(done.stdout)}


def _run_setup(shell: str, script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=script.parent, capture_output=True, text=True, timeout=180,
        encoding="utf-8", errors="replace",
    )


def _with_cmdletbinding(text: str) -> str:
    """setup.ps1 as it was: the attribute right above its param block."""
    planted = re.sub(r"(?m)^param\(", "[CmdletBinding()]\nparam(", text, count=1)
    assert planted != text
    return planted


# ── the gates ─────────────────────────────────────────────────────────────


def test_every_shipped_script_is_found():
    names = {p.name for p in _tracked_scripts()}
    assert "setup.ps1" in names


@pytest.mark.parametrize("script", _tracked_scripts(), ids=lambda p: p.name)
def test_no_advanced_script_declares_a_common_parameter(script):
    assert static_conflicts(script.read_text(encoding="utf-8-sig")) == []


_BOM = b"\xef\xbb\xbf"


def bom_problem(data: bytes) -> str | None:
    """Why Windows PowerShell 5.1 may misread these bytes, or None."""
    if data.startswith(_BOM) or data.isascii():
        return None
    return "holds non-ASCII text with no UTF-8 BOM: Windows PowerShell 5.1 reads it in the ANSI code page"


@pytest.mark.parametrize("script", _tracked_scripts(), ids=lambda p: p.name)
def test_a_script_with_non_ascii_text_has_a_bom(script):
    assert bom_problem(script.read_bytes()) is None, script.name


def test_the_bom_rule_sees_a_script_without_one():
    data = (ROOT / "scripts" / "industry_smoke.ps1").read_bytes()
    assert bom_problem(data) is None
    assert bom_problem(data[len(_BOM):]) is not None
    assert bom_problem(b"Write-Host 'plain'\r\n") is None


def test_the_reading_sees_the_old_setup_script():
    text = (ROOT / "setup.ps1").read_text(encoding="utf-8-sig")
    assert static_conflicts(_with_cmdletbinding(text)) == ["Debug"]
    # A [Parameter()] makes a script advanced too.
    assert static_conflicts("param(\n  [Parameter(Mandatory)] [string]$Name,\n  [switch]$Verbose\n)\n") == ["Verbose"]
    # A plain script may name its switch -Debug, and a function's block is
    # not the script's.
    assert static_conflicts("param([switch]$Debug)\n") == []
    assert static_conflicts("function f { [CmdletBinding()] param([switch]$x) }\n") == []


@pytest.mark.parametrize("shell", _shells() or ["none"])
def test_powershell_reads_every_script_without_errors_or_conflicts(shell, tmp_path):
    if shell == "none":
        pytest.skip("no PowerShell on this machine; the reading above still runs")
    scripts = _tracked_scripts()
    planted = tmp_path / "planted_setup.ps1"
    planted.write_text(_with_cmdletbinding((ROOT / "setup.ps1").read_text(encoding="utf-8-sig")),
                       encoding="utf-8-sig")
    no_bom = tmp_path / "industry_smoke_without_bom.ps1"
    no_bom.write_bytes((ROOT / "scripts" / "industry_smoke.ps1").read_bytes()[len(_BOM):])
    rows = powershell_reading(shell, scripts + [planted, no_bom], tmp_path)
    # The negative controls: PowerShell's own parser says the same as the
    # reading here about the old setup script, and 5.1 cannot parse the
    # smoke script as it was (7 reads UTF-8 with or without a BOM).
    assert rows["planted_setup.ps1"]["conflicts"] == ["Debug"]
    if shell == "powershell.exe":
        assert rows[no_bom.name]["errors"], "5.1 parsed the BOM-less smoke script"
    for script in scripts:
        row = rows[script.name]
        assert row["errors"] == [], (script.name, row["errors"])
        assert row["conflicts"] == [], (script.name, row["conflicts"])


@pytest.mark.parametrize("shell", _shells() or ["none"])
def test_setup_script_starts(shell, tmp_path):
    """Run for real in an empty folder: past the banner, it stops at its
    first checks (no kazma.yaml here). It installs uv when none answers, so
    it runs only where uv is installed."""
    if shell == "none":
        pytest.skip("no PowerShell on this machine")
    if not shutil.which("uv"):
        pytest.skip("setup.ps1 installs uv when none answers; not here")
    text = (ROOT / "setup.ps1").read_text(encoding="utf-8-sig")

    fixed = tmp_path / "fixed" / "setup.ps1"
    fixed.parent.mkdir()
    fixed.write_text(text, encoding="utf-8-sig")
    done = _run_setup(shell, fixed)
    output = done.stdout + done.stderr
    assert _REFUSED not in output, output
    assert "Environmental Guardrails" in output, output
    assert done.returncode == 1, output  # its own check: no kazma.yaml / Python

    old = tmp_path / "old" / "setup.ps1"
    old.parent.mkdir()
    old.write_text(_with_cmdletbinding(text), encoding="utf-8-sig")
    refused = _run_setup(shell, old)
    assert _REFUSED in refused.stdout + refused.stderr
    assert "Environmental Guardrails" not in refused.stdout
