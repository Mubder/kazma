"""Setup runs nothing it has not checked, and every image starts from a digest
(2026-11, supply chain).

``setup.ps1`` piped ``https://astral.sh/uv/install.ps1`` into
``Invoke-Expression`` -- in the administrator window it opens for itself --
and ``setup.sh`` piped ``https://astral.sh/uv/install.sh`` into ``sh``: each
ran whatever that address served on the day. Both now download one uv
release's archive from GitHub (``$UvVersion`` / ``UV_RELEASE``), check it
against the SHA-256 written in the script before anything is unpacked or
run, and stop on a mismatch. ``Dockerfile`` and ``Dockerfile.demo`` started
``FROM python:3.11-slim``, a tag the registry can move; every Dockerfile now
names a digest, and Dependabot watches each folder holding one.

Gates, each with a planted negative control: no shipped script pipes a
download into a shell; every ``FROM`` names ``@sha256:``; the two scripts name
one uv release with a hash for each platform they install on. Behaviour: the
real install functions, cut out of each script and run with a stand-in
download (a local archive, no network): a matching archive is installed where
setup looks for uv; a mismatching one is refused before it is unpacked.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UV_PIN = re.compile(r'\$UvVersion\s*=\s*"([^"]+)"')
SH_PIN = re.compile(r'^UV_RELEASE="([^"]+)"', re.M)
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


# ── no download piped into a shell ───────────────────────────────────────

_SHELLS = r"(?:sudo\s+(?:-\S+\s+)*)?(?:sh|bash|zsh|dash|ksh|fish|iex|invoke-expression|pwsh|powershell|python[0-9.]*|perl|ruby|node)\b"
_DOWNLOAD = r"(?:curl|wget|irm|iwr|invoke-restmethod|invoke-webrequest|downloadstring|fetch)\b"
_PIPED = re.compile(_DOWNLOAD + r"[^\n|]*\|\s*" + _SHELLS, re.I)
# `bash <(curl ...)`, `sh -c "$(curl ...)"`, `iex (irm ...)`.
_WRAPPED = re.compile(
    r"\b(?:sh|bash|zsh|iex|invoke-expression)\b[^\n]{0,20}?(?:<\(|\$\(|\()\s*" + _DOWNLOAD, re.I)


def _script_kind(rel: str) -> str:
    name = Path(rel).name
    if name.startswith("Dockerfile"):
        return "docker"
    if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
        return "workflow"
    return {".ps1": "ps", ".psm1": "ps", ".sh": "sh", ".cmd": "cmd", ".bat": "cmd"}.get(Path(rel).suffix, "")


def _code_lines(text: str, kind: str) -> list[tuple[int, str]]:
    """The script's lines with its comments left out."""
    if kind == "ps":
        text = re.sub(r"<#.*?#>", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if kind == "cmd" and re.match(r"(?i)^(rem\b|::)", stripped):
            continue
        if kind != "cmd" and stripped.startswith("#"):
            continue
        out.append((n, line))
    return out


def pipes_to_shell(text: str, kind: str) -> list[int]:
    """Lines that run a download through a shell or interpreter."""
    return [n for n, line in _code_lines(text, kind) if _PIPED.search(line) or _WRAPPED.search(line)]


def test_no_shipped_script_pipes_a_download_into_a_shell():
    offenders: list[str] = []
    seen = 0
    for rel in _tracked():
        if rel.startswith(("tests/", "docs/")) or "/tests/" in rel:
            continue
        kind = _script_kind(rel)
        if not kind or not (ROOT / rel).is_file():
            continue
        seen += 1
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        offenders += [f"{rel}:{n}" for n in pipes_to_shell(text, kind)]
    assert seen >= 5, "the gate read almost no scripts; it would be decoration"
    assert not offenders, (
        "A shipped script runs a download it has not checked. Download a pinned "
        "file, check its SHA-256, then run it (setup.ps1's Install-PinnedUv):\n  "
        + "\n  ".join(offenders)
    )


def test_negative_control_the_old_installers_are_found():
    old_ps = "Invoke-Native { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression 6>&1 }\n"
    old_sh = 'if curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -1; then\n'
    assert pipes_to_shell(old_ps, "ps") == [1]
    assert pipes_to_shell(old_sh, "sh") == [1]
    assert pipes_to_shell("RUN curl -fsSL https://x.example/i.sh | sudo -E bash -\n", "docker") == [1]
    assert pipes_to_shell('sh -c "$(curl -fsSL https://x.example/i.sh)"\n', "sh") == [1]
    assert pipes_to_shell("iex (irm https://x.example/i.ps1)\n", "ps") == [1]
    # A comment that names the old way, and a download checked before use, pass.
    assert pipes_to_shell("# setup piped https://astral.sh/uv/install.sh into sh\n", "sh") == []
    assert pipes_to_shell('curl -fsSL "$url" -o "$archive"\n', "sh") == []


# ── every image from a digest ────────────────────────────────────────────

_FROM = re.compile(r"(?im)^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?")


def unpinned_froms(text: str) -> list[str]:
    """``FROM`` images with no digest; a stage built earlier in the file and
    ``scratch`` are not images."""
    stages: set[str] = set()
    found = []
    for image, alias in _FROM.findall(text):
        if image.lower() not in stages and image != "scratch" and "@sha256:" not in image:
            found.append(image)
        if alias:
            stages.add(alias.lower())
    return found


def _dockerfiles() -> list[str]:
    return [rel for rel in _tracked() if Path(rel).name.startswith("Dockerfile")
            and not rel.startswith("tests/") and (ROOT / rel).is_file()]


def test_every_dockerfile_starts_from_a_digest():
    files = _dockerfiles()
    assert {"Dockerfile", "Dockerfile.demo", "deploy/document-worker/Dockerfile"} <= set(files)
    offenders = [f"{rel}: FROM {image}" for rel in files
                 for image in unpinned_froms((ROOT / rel).read_text(encoding="utf-8"))]
    assert not offenders, "Pin each base image by digest (FROM image:tag@sha256:...):\n  " + "\n  ".join(offenders)


def test_negative_control_a_tag_alone_is_found():
    assert unpinned_froms("FROM python:3.11-slim\nWORKDIR /app\n") == ["python:3.11-slim"]
    staged = ("FROM docker:29.0-cli@sha256:" + "a" * 64 + " AS cli\n"
              "FROM python:3.11-slim@sha256:" + "b" * 64 + "\nCOPY --from=cli /x /y\nFROM cli\n")
    assert unpinned_froms(staged) == []


def test_dependabot_watches_every_folder_with_a_dockerfile():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    watched = set()
    for update in config["updates"]:
        if update["package-ecosystem"] == "docker":
            watched |= set(update.get("directories") or [update.get("directory")])
    folders = {"/" + str(Path(rel).parent).replace("\\", "/").strip("./") for rel in _dockerfiles()}
    folders = {"/" if f in ("/", "/.") else f for f in folders}
    assert folders <= watched, f"Dependabot does not watch {sorted(folders - watched)}"


# ── the two setup scripts name one uv release, one hash a platform ───────

def _ps_hashes(text: str) -> dict[str, str]:
    block = re.search(r"\$UvArchiveSha256\s*=\s*@\{(.*?)\}", text, re.S).group(1)
    return dict(re.findall(r'"([\w-]+)"\s*=\s*"([0-9a-f]{64})"', block))


def _sh_hashes(text: str) -> dict[str, str]:
    block = re.search(r"uv_archive_sha256\(\)\s*\{(.*?)\n\}", text, re.S).group(1)
    return dict(re.findall(r'([\w-]+)\)\s*echo\s*"([0-9a-f]{64})"', block))


def test_both_setup_scripts_pin_one_uv_release():
    ps = (ROOT / "setup.ps1").read_text(encoding="utf-8-sig")
    sh = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert UV_PIN.search(ps).group(1) == SH_PIN.search(sh).group(1)
    assert set(_ps_hashes(ps)) == {"x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc", "i686-pc-windows-msvc"}
    assert set(_sh_hashes(sh)) == {
        f"{arch}-{system}" for arch in ("x86_64", "aarch64")
        for system in ("unknown-linux-gnu", "unknown-linux-musl", "apple-darwin")}
    hashes = list(_ps_hashes(ps).values()) + list(_sh_hashes(sh).values())
    assert len(set(hashes)) == len(hashes), "two platforms share a hash: one was copied over another"
    # The download is the GitHub release named, never the moving installer.
    for text in (ps, sh):
        assert "https://github.com/astral-sh/uv/releases/download/" in text
        assert "astral.sh/uv/install" not in "\n".join(line for _, line in _code_lines(text, "sh"))


# ── behaviour: the real install functions, with a local stand-in download ─


def _fake_zip(path: Path) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("uv.exe", b"stand-in uv")
        z.writestr("uvx.exe", b"stand-in uvx")
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


_PS_HARNESS = r"""
$ErrorActionPreference = "Stop"
$setup = $env:KAZMA_SETUP
$ast = [System.Management.Automation.Language.Parser]::ParseFile($setup, [ref]$null, [ref]$null)
foreach ($name in @('Get-UvTarget', 'Get-Sha256Hex', 'Expand-UvArchive', 'Install-PinnedUv')) {
    $def = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    . ([scriptblock]::Create($def.Extent.Text))
}
foreach ($var in @('$UvVersion', '$UvArchiveSha256')) {
    $assign = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq $var }, $false)
    . ([scriptblock]::Create($assign.Extent.Text))
}
function Log-Fail($m) { Write-Host "FAIL $m" }
function Log-Info($m) { Write-Host "INFO $m" }
function Log-Warn($m) { Write-Host "WARN $m" }
$script:asked = @(); $script:expanded = $false; $script:pathAdded = ""
# The download: a local file (no network). The unzip: the script's own, watched.
function Save-UvArchive([string]$Url, [string]$Path) { $script:asked += $Url; Copy-Item -LiteralPath $env:KAZMA_FAKE_ARCHIVE -Destination $Path }
$realExpand = ${function:Expand-UvArchive}
function Expand-UvArchive([string]$Archive, [string]$Destination) { $script:expanded = $true; & $realExpand $Archive $Destination }
function Add-UvToUserPath([string]$Dir) { $script:pathAdded = $Dir }
if ($env:KAZMA_FAKE_SHA) { $UvArchiveSha256[(Get-UvTarget)] = $env:KAZMA_FAKE_SHA }
$ok = Install-PinnedUv
$bin = Join-Path $env:USERPROFILE ".local\bin"
[pscustomobject]@{ ok = [bool]$ok; asked = @($script:asked); expanded = $script:expanded
    uv = (Test-Path (Join-Path $bin "uv.exe")); uvx = (Test-Path (Join-Path $bin "uvx.exe"))
    path_added = $script:pathAdded; session_path = ($env:Path -split ';')[0]; version = $UvVersion } | ConvertTo-Json -Compress
"""


def _powershells() -> list[str]:
    return [s for s in ("powershell.exe", "pwsh") if shutil.which(s)]


def _run_ps(shell: str, tmp_path: Path, fake_sha: str) -> dict:
    harness = tmp_path / "harness.ps1"
    harness.write_text(_PS_HARNESS, encoding="utf-8-sig")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "PROCESSOR_ARCHITEW6432"}
    env.update(KAZMA_SETUP=str(ROOT / "setup.ps1"), KAZMA_FAKE_ARCHIVE=str(tmp_path / "uv.zip"),
               KAZMA_FAKE_SHA=fake_sha, USERPROFILE=str(home), PROCESSOR_ARCHITECTURE="AMD64")
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                          capture_output=True, text=True, timeout=180, env=env, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1]) | {"log": done.stdout}


@pytest.mark.parametrize("shell", _powershells() or ["none"])
def test_setup_ps1_installs_a_matching_archive_and_refuses_another(shell, tmp_path):
    if shell == "none":
        pytest.skip("no PowerShell on this machine")
    sha = _fake_zip(tmp_path / "uv.zip")
    good = _run_ps(shell, tmp_path, sha)
    assert good["ok"] is True, good["log"]
    assert good["asked"] == [f"https://github.com/astral-sh/uv/releases/download/{good['version']}/uv-x86_64-pc-windows-msvc.zip"]
    assert good["uv"] and good["uvx"]
    assert good["path_added"] == str(tmp_path / "home" / ".local" / "bin") == good["session_path"]

    shutil.rmtree(tmp_path / "home")
    refused = _run_ps(shell, tmp_path, "")   # the real hash: the stand-in does not match it
    assert refused["ok"] is False
    assert refused["expanded"] is False, "the archive was unpacked before its hash was checked"
    assert not refused["uv"] and not refused["path_added"]
    assert "does not match its recorded SHA-256" in refused["log"]


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.is_file() else None
    return shutil.which("bash")


def _fake_tar(path: Path, target: str) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for exe in ("uv", "uvx"):
            data = b"#!/bin/sh\necho stand-in 0.0.0\n"
            info = tarfile.TarInfo(f"uv-{target}/{exe}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _sh_functions() -> str:
    """setup.sh's helpers, from its log functions to the permission guard."""
    text = (ROOT / "setup.sh").read_text(encoding="utf-8")
    start = text.index("log_ok()")
    end = text.index("# ── 0. Permission Guard")
    return text[start:end]


_SH_HARNESS = r"""
set -euo pipefail
BRAND='' GREEN='' YELLOW='' RED='' DIM='' NC=''
source "$KAZMA_FUNCS"
uv_target() { echo "x86_64-unknown-linux-gnu"; }
if [[ -n "${KAZMA_FAKE_SHA:-}" ]]; then uv_archive_sha256() { echo "$KAZMA_FAKE_SHA"; }; fi
download_to() { echo "$1" > "$KAZMA_ASKED"; cp "$KAZMA_FAKE_ARCHIVE" "$2"; }
tar() { touch "$KAZMA_UNPACKED"; command tar "$@"; }
if install_pinned_uv; then echo "RESULT ok"; else echo "RESULT refused"; fi
"""


def _run_sh(bash: str, tmp_path: Path, fake_sha: str) -> dict:
    funcs, harness = tmp_path / "funcs.sh", tmp_path / "harness.sh"
    funcs.write_bytes(_sh_functions().encode("utf-8"))
    harness.write_bytes(_SH_HARNESS.encode("utf-8"))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    for flag in ("asked", "unpacked"):
        (tmp_path / flag).unlink(missing_ok=True)

    def posix(p: Path) -> str:
        return p.as_posix()

    env = dict(os.environ, HOME=posix(home), KAZMA_FUNCS=posix(funcs), KAZMA_FAKE_SHA=fake_sha,
               KAZMA_FAKE_ARCHIVE=posix(tmp_path / "uv.tar.gz"), KAZMA_ASKED=posix(tmp_path / "asked"),
               KAZMA_UNPACKED=posix(tmp_path / "unpacked"), NO_COLOR="1")
    done = subprocess.run([bash, posix(harness)], capture_output=True, text=True, timeout=120, env=env,
                          encoding="utf-8", errors="replace")
    asked = (tmp_path / "asked").read_text().strip() if (tmp_path / "asked").exists() else ""
    return {"log": done.stdout + done.stderr, "ok": "RESULT ok" in done.stdout,
            "asked": asked, "unpacked": (tmp_path / "unpacked").exists(),
            "uv": (home / ".local" / "bin" / "uv").is_file()}


def test_setup_sh_installs_a_matching_archive_and_refuses_another(tmp_path):
    bash = _bash()
    if bash is None:
        pytest.skip("no bash on this machine")
    target = "x86_64-unknown-linux-gnu"
    sha = _fake_tar(tmp_path / "uv.tar.gz", target)
    release = SH_PIN.search((ROOT / "setup.sh").read_text(encoding="utf-8")).group(1)

    good = _run_sh(bash, tmp_path, sha)
    assert good["ok"], good["log"]
    assert good["asked"] == f"https://github.com/astral-sh/uv/releases/download/{release}/uv-{target}.tar.gz"
    assert good["uv"]

    shutil.rmtree(tmp_path / "home")
    refused = _run_sh(bash, tmp_path, "")   # the real hash: the stand-in does not match it
    assert not refused["ok"], refused["log"]
    assert not refused["unpacked"], "the archive was unpacked before its hash was checked"
    assert not refused["uv"]
    assert "does not match its recorded SHA-256" in refused["log"]
