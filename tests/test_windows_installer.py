"""Kazma's Windows installer (scripts/windows_installer, REMAINING 2.7).

The installer is built in the release workflow from the source archive the
build job made (the archive the release signs), laid out with the
release-install marker (kazma_cli.release_tree), then run through Inno Setup;
it is signed once a signing account is configured and is checksummed,
attested and Sigstore-signed with the release's other files. On a computer it
lays the tree down and runs setup.cmd, continues setup after a WSL restart,
and its uninstaller runs `kazma uninstall`, which keeps the data.
The drill on a clean Windows VM is scripts/windows_installer_drill.py.
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "scripts" / "windows_installer"
ISS = (HERE / "kazma.iss").read_text(encoding="utf-8")
RELEASE = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))

sys.path.insert(0, str(HERE))
import build  # noqa: E402
from kazma_cli import release_tree  # noqa: E402


def _archive(tmp: Path, version: str, files: dict[str, str]) -> Path:
    path = tmp / f"kazma-{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for rel, text in {"pyproject.toml": f'version = "{version}"\n', **files}.items():
            data = text.encode()
            info = tarfile.TarInfo(f"kazma-{version}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


# -- build.py ------------------------------------------------------------------

def test_the_installer_tree_is_the_archive_and_its_marker(tmp_path):
    archive = _archive(tmp_path, "1.2.3", {"setup.cmd": "@echo off\n", "kazma-cli/kazma_cli/x.py": "x\n"})
    version, files = build.lay_out(archive, tmp_path / "staging")
    tree = tmp_path / "staging" / "kazma-1.2.3"
    assert version == "1.2.3"
    assert (tree / "setup.cmd").read_text() == "@echo off\n"
    marker = release_tree.read_marker(tree)
    assert marker["version"] == "1.2.3"
    assert set(marker["files"]) == {"pyproject.toml", "setup.cmd", "kazma-cli/kazma_cli/x.py"} == set(files)


def test_an_archive_the_updater_refuses_is_never_built_into_an_installer(tmp_path):
    archive = tmp_path / "kazma-1.2.3.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in (("kazma-1.2.3/pyproject.toml", b"v"), ("kazma-1.2.3/.env", b"KAZMA_SECRET=x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(SystemExit, match="refused"):
        build.lay_out(archive, tmp_path / "staging")


def test_only_a_kazma_source_archive_is_taken():
    with pytest.raises(SystemExit):
        build.archive_version(Path("other-1.0.tar.gz"))
    assert build.archive_version(Path("kazma-0.21.2.tar.gz")) == "0.21.2"


def test_inno_setup_is_pinned_by_version_and_sha256():
    assert re.fullmatch(r"[0-9a-f]{64}", build.INNO_SHA256)
    assert build.INNO_URL.startswith("https://github.com/jrsoftware/issrc/releases/download/")
    assert build.INNO_VERSION in build.INNO_URL


# -- kazma.iss -----------------------------------------------------------------

def test_the_installer_has_one_real_app_id():
    found = re.findall(r"^AppId=\{\{([0-9A-F-]+)\}$", ISS, re.M)
    assert len(found) == 1 and re.fullmatch(r"[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}", found[0])


def test_kazma_goes_where_a_persons_programs_go_with_admin_rights_asked_once():
    assert "DefaultDirName={localappdata}\\Programs\\Kazma" in ISS
    assert "PrivilegesRequired=admin" in ISS


def test_the_installer_runs_the_same_setup_a_clone_runs():
    assert '\\setup.cmd" -Mode \' + SetupMode() + \' -Yes -NoElevate' in ISS
    assert "ewWaitUntilTerminated" in ISS


def test_setup_runs_before_inno_asks_whether_to_restart():
    # Inno Setup asks NeedRestart before ssPostInstall: setup run from there
    # could never ask for the WSL restart (the first VM drill's log said
    # "Need to restart Windows? No" over a RunOnce entry it had just written).
    assert "AfterInstall: RunKazmaSetup" in ISS
    assert "ssPostInstall" not in ISS.split("[Code]", 1)[1].replace("// ", "")


def test_every_message_answers_itself_in_a_silent_install():
    # A plain MsgBox waits for a click in a /VERYSILENT /SUPPRESSMSGBOXES
    # install too: the second VM drill hung on one until Windows restarted.
    code = ISS.split("[Code]", 1)[1]
    assert "SuppressibleMsgBox(" in code
    assert not re.search(r"(?<!Suppressible)MsgBox\(", code)


def test_setup_continues_after_the_wsl_restart():
    # kazma_cli.install's EXIT_RESTART (3): a RunOnce entry, and the restart asked.
    from kazma_cli.install import EXIT_RESTART

    assert f"ResultCode = {EXIT_RESTART}" in ISS
    assert "CurrentVersion\\RunOnce" in ISS and "RestartAfter := True" in ISS
    assert "function NeedRestart(): Boolean;" in ISS


def test_uninstalling_runs_kazma_uninstall_which_keeps_the_data():
    line = next(x for x in ISS.splitlines() if x.startswith("Filename:") and "uninstall" in x)
    assert '"-m kazma_cli uninstall --yes"' in line
    assert "--remove-data" not in ISS and "--purge" not in ISS


def test_the_shortcuts_open_update_and_repair_kazma():
    for wanted in ('"-m kazma_cli open"', "-m kazma_cli update", "\\setup.cmd\"; WorkingDir"):
        assert wanted in ISS, wanted


def test_kazma_open_exists():
    text = (ROOT / "kazma-cli" / "kazma_cli" / "main.py").read_text(encoding="utf-8")
    assert 'elif cmd == "open":' in text and "server_port()" in text


# -- the release workflow ------------------------------------------------------

def _job(name):
    return RELEASE["jobs"][name]


def _runs(name):
    return "\n".join(str(s.get("run") or "") for s in _job(name)["steps"])


def test_the_installer_is_built_from_the_build_jobs_checked_archive():
    runs = _runs("windows-installer")
    assert "sha256sum -c build-artifacts.sha256" in runs
    assert runs.index("sha256sum -c") < runs.index("windows_installer/build.py")
    assert '--source "dist/kazma-${BASE}.tar.gz"' in runs and "--install-inno" in runs


#: The signing services one repository variable chooses between, and the
#: action each signs with, in the one shared signing step. Moving from one to
#: another is the variable and the new service's secrets and variables, all
#: named in docs/SUPPLY_CHAIN.md.
SIGNING_SERVICES = {
    "signpath": "signpath/github-action-submit-signing-request@",
    "ssl-com": "SSLcom/esigner-codesign@",
    "artifact-signing": "azure/artifact-signing-action@",
}
SIGN_ACTION = ROOT / ".github" / "actions" / "sign-windows-installer" / "action.yml"
COMPOSITE = yaml.safe_load(SIGN_ACTION.read_text(encoding="utf-8"))
SIGNING_TEST = yaml.safe_load((ROOT / ".github" / "workflows" / "windows-signing-test.yml").read_text(encoding="utf-8"))
_SWITCH = re.compile(r"^\$\{\{ inputs\.service == '([a-z-]+)' \}\}$")


def _named(steps, name):
    return next(s for s in steps if s.get("name") == name)


def _signing_problems(steps) -> list[str]:
    """What is wrong with the shared step's signing steps: each service's
    steps run only when the caller names that service, every service signs,
    and the first check knows exactly the same services."""
    problems: list[str] = []
    by_service: dict[str, list[dict]] = {}
    for step in steps:
        cond = str(step.get("if") or "")
        if "inputs.service" not in cond:
            continue
        match = _SWITCH.match(cond)
        if not match or match.group(1) not in SIGNING_SERVICES:
            problems.append(f"{step.get('name')}: runs on {cond!r}, not one service the switch names")
            continue
        by_service.setdefault(match.group(1), []).append(step)
    for service, action in SIGNING_SERVICES.items():
        if not any(str(s.get("uses") or "").startswith(action) for s in by_service.get(service, [])):
            problems.append(f"{service}: no step signs with {action}")
    choose = next((s for s in steps if s.get("id") == "choose"), None)
    listed = re.search(r"-notin @\(([^)]*)\)", choose["run"]) if choose else None
    named = set(re.findall(r"'([a-z-]+)'", listed.group(1))) if listed else set()
    if named != set(SIGNING_SERVICES):
        problems.append(f"the shared step accepts {sorted(named)}, not {sorted(SIGNING_SERVICES)}")
    return problems


def _callers():
    """Every step that calls the shared signing step, with its workflow."""
    found = []
    for workflow, doc in (("release.yml", RELEASE), ("windows-signing-test.yml", SIGNING_TEST)):
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses") == "./.github/actions/sign-windows-installer":
                    found.append((workflow, step))
    return found


def test_one_switch_chooses_the_signing_service_and_the_check_knows_each():
    steps = COMPOSITE["runs"]["steps"]
    assert _signing_problems(steps) == []
    check = _named(steps, "Check the signature")["run"]
    assert "Get-AuthenticodeSignature" in check and "'Valid'" in check
    assert "TimeStamperCertificate" in check, "a signature with no timestamp dies with its certificate"
    assert "unsigned" in check
    # Azure is reached with the run's OIDC token, never a stored secret.
    login = next(s for s in steps if str(s.get("uses") or "").startswith("azure/login@"))
    assert "client-secret" not in str(login.get("with"))


def test_the_release_and_the_test_sign_through_the_one_shared_step():
    callers = _callers()
    release = [step for workflow, step in callers if workflow == "release.yml"]
    test = [step for workflow, step in callers if workflow == "windows-signing-test.yml"]
    assert len(release) == 1 and len(test) == 1
    assert release[0]["with"]["service"] == "${{ vars.KAZMA_WINDOWS_SIGNING }}"
    assert release[0]["with"]["environment"] == "release"
    assert test[0]["with"]["environment"] == "test"
    # YAML reads the key `on` as true.
    dispatch = (SIGNING_TEST.get("on") or SIGNING_TEST[True])["workflow_dispatch"]
    assert "artifact-signing" not in dispatch["inputs"]["service"]["options"]
    # No workflow signs on its own: a second path would be one the test never ran.
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        for action in SIGNING_SERVICES.values():
            assert action not in text, f"{path.name} signs outside the shared step ({action})"
    # Every credential a caller hands in is a secret, never written in the file.
    for workflow, step in callers:
        for key, value in step["with"].items():
            if any(word in key for word in ("token", "password", "username", "totp", "credential", "client-id",
                                            "tenant-id", "subscription-id")):
                assert str(value).startswith("${{ secrets."), (workflow, key)


def test_the_signing_test_publishes_nothing():
    assert SIGNING_TEST["permissions"] == {"contents": "read"}
    for job in SIGNING_TEST["jobs"].values():
        assert job["permissions"].get("contents") == "read" and "id-token" not in job["permissions"]
        text = yaml.safe_dump(job)
        assert "gh release" not in text and "kazma-dist" not in text and "installer/" not in text


def test_control_a_service_the_check_does_not_know_is_found():
    steps = [dict(s) for s in COMPOSITE["runs"]["steps"]]
    planted = {"name": "Sign (Other)", "if": "${{ inputs.service == 'other-ca' }}",
               "uses": "example/sign@0000000000000000000000000000000000000000"}
    assert _signing_problems([*steps, planted])
    dropped = [s for s in steps if not str(s.get("uses") or "").startswith(SIGNING_SERVICES["ssl-com"])]
    assert any("ssl-com" in p for p in _signing_problems(dropped))


def test_supply_chain_names_everything_each_service_needs():
    """Moving to SSL.com (or any of the three), and testing one first, is
    documented: every secret and variable a caller hands the shared step is
    named in the windows-signing section, and so is every service."""
    text = (ROOT / "docs" / "SUPPLY_CHAIN.md").read_text(encoding="utf-8")
    section = text.split("{#windows-signing}", 1)[1].split("\n## ", 1)[0]
    for workflow, step in _callers():
        for name in re.findall(r"(?:secrets|vars)\.([A-Z_]+)", yaml.safe_dump(step["with"])):
            assert f"`{name}`" in section, f"{name} ({workflow}) is not in SUPPLY_CHAIN.md"
    for service in SIGNING_SERVICES:
        assert f"`{service}`" in section, service
    assert "windows-signing-test.yml" in section


def _powershell():
    import shutil

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if sys.platform != "win32" or not shell:
        pytest.skip("Get-AuthenticodeSignature is Windows PowerShell's")
    return shell


def _run_step(tmp_path, name, env: dict[str, str]) -> int:
    import os
    import subprocess

    step = _named(COMPOSITE["runs"]["steps"], name)
    run = re.sub(r"\$\{\{[^}]*\}\}", "", step["run"])
    done = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", run], cwd=tmp_path,
                          env={**os.environ, **env}, capture_output=True, text=True, timeout=120)
    return done.returncode


def _unsigned(tmp_path):
    (tmp_path / "out").mkdir()
    exe = tmp_path / "out" / "kazma-0.0.1-windows-setup.exe"
    exe.write_bytes(b"MZ" + b"\0" * 64)
    return exe


@pytest.mark.parametrize("service, environment, code", [
    ("", "release", 0), ("signpath", "release", 1), ("ssl-com", "test", 1), ("artifact-signing", "release", 1),
])
def test_the_check_passes_an_unsigned_file_only_when_no_service_is_chosen(tmp_path, service, environment, code):
    """The real check on an unsigned file: no service passes with a notice; a
    chosen service fails it, in a test as in a release."""
    exe = _unsigned(tmp_path)
    env = {"SERVICE": service, "ENVIRONMENT": environment, "TARGET": str(exe)}
    assert _run_step(tmp_path, "Check the signature", env) == code


@pytest.mark.parametrize("service, environment, extra, code", [
    ("ssl-com", "test", False, 0), ("", "release", False, 0), ("sslcom", "release", False, 1),
    ("ssl-com", "prod", False, 1), ("artifact-signing", "test", False, 1), ("signpath", "release", True, 1),
])
def test_the_shared_step_refuses_a_wrong_choice_before_anything_is_sent(tmp_path, service, environment, extra,
                                                                        code):
    """A misspelt service or environment, a test with no test certificate, or
    a folder holding more than one program fails before any signing step."""
    _unsigned(tmp_path)
    if extra:
        (tmp_path / "out" / "other.exe").write_bytes(b"MZ")
    output = tmp_path / "github_output"
    env = {"SERVICE": service, "ENVIRONMENT": environment, "FOLDER": "out", "GITHUB_OUTPUT": str(output)}
    assert _run_step(tmp_path, "Choose the signing service", env) == code
    if code == 0:
        assert "name=kazma-0.0.1-windows-setup.exe" in output.read_text(encoding="utf-8")


def test_the_installer_is_checksummed_attested_and_signed_with_the_rest():
    runs = _runs("release")
    assert "sha256sum dist/kazma-*-windows-setup.exe >> build-artifacts.sha256" in runs
    assert "kazma-*-windows-setup.exe > SHA256SUMS" in runs
    attest = next(s for s in _job("release")["steps"] if "attest-build-provenance" in str(s.get("uses") or ""))
    assert "dist/kazma-*-windows-setup.exe" in attest["with"]["subject-path"]
    sys.path.insert(0, str(ROOT / "scripts"))
    import verify_release_signatures

    assert "kazma-*-windows-setup.exe" in verify_release_signatures.PUBLISHED


def test_control_the_old_signature_list_misses_the_installer(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import verify_release_signatures

    (tmp_path / "kazma-1.0.0-windows-setup.exe").write_bytes(b"MZ")
    old = ("kazma-*.whl", "kazma-*.tar.gz", "kazma-*.cdx.json", "SHA256SUMS")
    assert not [p for pattern in old for p in tmp_path.glob(pattern)]
    assert verify_release_signatures.published_files(tmp_path) == [tmp_path / "kazma-1.0.0-windows-setup.exe"]
