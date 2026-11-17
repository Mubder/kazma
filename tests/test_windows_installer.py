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


def test_signing_is_wired_and_checked_and_says_when_it_is_off():
    steps = _job("windows-installer")["steps"]
    signing = [s for s in steps if "artifact-signing-action" in str(s.get("uses") or "")]
    login = [s for s in steps if str(s.get("uses") or "").startswith("azure/login@")]
    assert signing and login
    for step in (*signing, *login):
        assert step.get("if") == "${{ vars.KAZMA_WINDOWS_SIGNING == 'artifact-signing' }}"
    check = next(s for s in steps if s.get("name") == "Check the installer's signature")
    assert "Get-AuthenticodeSignature" in check["run"] and "'Valid'" in check["run"]
    assert "unsigned" in check["run"]
    # Azure is reached with the run's OIDC token, never a stored secret.
    assert "client-secret" not in str(login[0].get("with"))


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
