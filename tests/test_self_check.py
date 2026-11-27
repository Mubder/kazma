"""``kazma self-check``: the checks Kazma already has, and a miss is not a pass.

A check that did not run is ``not_run``. An empty drill whose own verdict
would be PASS, a hardening check with ``passed`` true and ``ran`` false, and
a known-gap quote that sits only inside a closed entry are the controls.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from kazma_cli import known_gap_settings as gaps
from kazma_cli import self_check
from kazma_cli.release_signature import SignatureRefused
from kazma_core.backup.restore_drill import DrillResult

ROOT = Path(__file__).resolve().parents[1]
GAPS_TEXT = (ROOT / "docs" / "KNOWN_GAPS.md").read_text(encoding="utf-8")
_SOURCE = Path(self_check.__file__).read_text(encoding="utf-8")
_REQUIRED = frozenset(
    {
        "report_sections",
        "SecurityHardeningRunner",
        "run_drill",
        "skill_integrity",
        "check_bundle",
        "judge_rules",
    }
)


def _identifiers(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _clear(name: str) -> self_check.Section:
    return self_check._section(
        name,
        self_check.OK,
        "Nothing here needs attention.",
        "لا شيء هنا يحتاج انتباهاً.",
        [],
    )


def _install(monkeypatch, **overrides) -> None:
    for name in self_check.SECTION_ORDER:
        fn = overrides.get(name)
        if fn is None:
            section = _clear(name)

            def fn(section: self_check.Section = section) -> self_check.Section:
                return section

        monkeypatch.setattr(self_check, f"_{name}", fn)


def _silence_verbose(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_VERBOSE_ERRORS", raising=False)


def test_self_check_calls_the_existing_checks() -> None:
    assert _REQUIRED <= _identifiers(_SOURCE)


def test_negative_control_a_skipped_check_is_named() -> None:
    planted = _SOURCE.replace("run_drill", "skipped")
    assert planted != _SOURCE
    assert _REQUIRED - _identifiers(planted) == {"run_drill"}


def _keep_data_dir() -> None:
    """A non-empty data folder, so a stubbed report is not the empty-folder answer."""
    data = Path(os.environ["KAZMA_DATA_DIR"])
    data.mkdir(parents=True, exist_ok=True)
    marker = data / ".self-check-test"
    if not marker.exists():
        marker.write_text("x", encoding="utf-8")


def test_collect_opens_the_read_only_diagnostic(monkeypatch) -> None:
    from kazma_core.diagnostic_scope import active_diagnostic

    _keep_data_dir()
    seen: dict[str, str | None] = {}

    def fake() -> self_check.Report:
        seen["scope"] = active_diagnostic()
        return self_check.Report([])

    monkeypatch.setattr(self_check, "_collect", fake)
    assert self_check.collect().needs_attention is False
    assert seen["scope"] == "kazma self-check"


def test_a_doctor_warning_needs_attention_and_an_empty_report_did_not_run() -> None:
    warned = self_check._health_from([("warn", "key missing"), ("ok", "model set"), ("info", "note")])
    assert warned.status == self_check.ATTENTION
    assert [item.status for item in warned.items] == [
        self_check.ATTENTION,
        self_check.OK,
        self_check.INFO,
    ]
    assert warned.items[2].title_ar is None
    info_only = self_check._health_from([("info", "the cloud profile is off")])
    assert info_only.status == self_check.INFO
    assert self_check.Report([info_only]).needs_attention is False
    empty = self_check._health_from([])
    assert empty.status == self_check.NOT_RUN
    assert "passed" not in empty.detail_en
    unknown = self_check._health_from([("maybe", "unrecognised")])
    assert unknown.status == self_check.NOT_RUN
    assert unknown.items[0].status == self_check.NOT_RUN


def test_a_security_check_that_did_not_run_is_not_a_pass() -> None:
    """``passed`` true used to read as clean. ``ran`` false is the control."""
    skipped = SimpleNamespace(
        checks=[
            SimpleNamespace(name="deps", passed=True, ran=False, message="nothing to report"),
        ]
    )
    section = self_check._security_from(skipped)
    assert section.status == self_check.NOT_RUN
    assert section.items[0].status == self_check.NOT_RUN
    failed = SimpleNamespace(
        checks=[
            SimpleNamespace(name="deps", passed=False, ran=True, message="one advisory"),
        ]
    )
    assert self_check._security_from(failed).status == self_check.ATTENTION
    clean = SimpleNamespace(
        checks=[
            SimpleNamespace(name="deps", passed=True, ran=True, message="none"),
        ]
    )
    assert self_check._security_from(clean).status == self_check.OK
    advised = SimpleNamespace(
        checks=[
            SimpleNamespace(
                name="deps", passed=True, ran=True, message="none", recommendation="Keep the registry on",
            ),
        ]
    )
    advised_section = self_check._security_from(advised)
    assert "Keep the registry on" in advised_section.items[0].detail_en
    assert advised_section.items[0].detail_ar is None
    assert advised_section.items[0].title_ar is None
    assert self_check._security_from(SimpleNamespace(checks=[])).status == self_check.NOT_RUN


def test_an_empty_drill_is_not_a_pass_and_unverified_is_not_run() -> None:
    empty = DrillResult("backups/empty")
    assert empty.verdict == "PASS"
    assert self_check._backup_from(empty).status == self_check.NOT_RUN
    assert self_check._backup_from(object()).status == self_check.NOT_RUN
    missing = DrillResult("")
    missing.add("backup:exists", False, "no universal backups found")
    assert self_check._backup_from(missing).status == self_check.ATTENTION
    held = DrillResult("backups/latest")
    held.add("backup:exists", True, "found")
    assert self_check._backup_from(held).status == self_check.OK
    unverified = DrillResult("backups/latest")
    unverified.add("backup:exists", True, "found")
    unverified.add("postgres:toc", None, "pg_restore is not installed")
    section = self_check._backup_from(unverified)
    assert section.status == self_check.NOT_RUN
    assert section.items[0].status == self_check.NOT_RUN
    assert "PASS" not in section.detail_en


def test_skill_signatures_keep_a_failed_check_apart_from_an_unsigned_skill() -> None:
    none = self_check._skills_from([])
    assert none.status == self_check.OK
    assert "passed" not in none.detail_en
    assert "لم يُحسب" in none.detail_ar
    failed = self_check._skills_from([("demo", "unchecked", "the integrity check did not finish")])
    assert failed.status == self_check.NOT_RUN
    assert failed.items[0].detail_ar is None
    assert "boom" not in failed.detail_en
    unsigned = self_check._skills_from([("demo", "unsigned", "no signature file")])
    assert unsigned.status == self_check.ATTENTION
    assert self_check._skills_from([("demo", "verified", "holds")]).status == self_check.OK
    assert self_check._skills_from([("demo", "refused", "hash mismatch")]).status == self_check.ATTENTION
    assert self_check._skills_from([("demo", "maybe", "")]).status == self_check.NOT_RUN


def test_a_url_password_in_a_called_check_is_masked() -> None:
    section = self_check._health_from([("warn", "endpoint https://user:s3cret@example.com/v1")])
    text = self_check.render(self_check.Report([section]))
    payload = json.dumps(self_check.Report([section]).to_dict())
    assert "s3cret" not in text
    assert "s3cret" not in payload
    assert "example.com" in text


def test_release_signature_is_not_applicable_when_no_bundle_is_kept(monkeypatch) -> None:
    def checker(digest: bytes, bundle: bytes) -> str:
        raise AssertionError("the checker ran")

    missing = self_check._release_from(self_check.ReleaseArtifact("missing", ""), checker, "git")
    assert missing.status == self_check.NOT_RUN
    for kind in ("editable", "no_bundle"):
        section = self_check._release_from(self_check.ReleaseArtifact(kind, "0.21.2"), checker, "git")
        assert section.status == self_check.NOT_APPLICABLE, kind
        assert "source checkout" in section.detail_en
        assert "نسخة مصدر" in section.detail_ar
        assert self_check.Report([section]).needs_attention is False
    release = self_check._release_from(self_check.ReleaseArtifact("editable", "0.21.2"), checker, "release")
    assert release.status == self_check.NOT_APPLICABLE
    assert "release install" in release.detail_en
    assert "source checkout" not in release.detail_en
    assert "تثبيت إصدار" in release.detail_ar
    wheel = self_check._release_from(self_check.ReleaseArtifact("no_bundle", "0.21.2"), checker, "pip")
    assert wheel.status == self_check.NOT_APPLICABLE
    assert "wheel install" in wheel.detail_en
    older = self_check._release_from(self_check.ReleaseArtifact("editable", "0.21.1"), checker, "git")
    assert older.status == self_check.NOT_APPLICABLE
    assert "0.21.2" in older.detail_en
    odd = self_check._release_from(self_check.ReleaseArtifact("editable", "0.21.2+gSHA"), checker, "git")
    assert odd.status == self_check.NOT_APPLICABLE
    assert "older than" not in odd.detail_en
    monkeypatch.setattr(self_check, "_sigstore_present", lambda: False)
    ready = self_check.ReleaseArtifact("ready", "0.21.2", b"digest", b"bundle")
    assert self_check._release_from(ready, checker, "git").status == self_check.NOT_RUN


def test_a_ready_bundle_is_checked_and_a_refusal_needs_attention(monkeypatch) -> None:
    monkeypatch.setattr(self_check, "_sigstore_present", lambda: True)
    seen: list[tuple[bytes, bytes]] = []

    def holds(digest: bytes, bundle: bytes) -> str:
        seen.append((digest, bundle))
        return "release-workflow"

    ready = self_check.ReleaseArtifact("ready", "0.21.2", b"digest", b"bundle")
    section = self_check._release_from(ready, holds)
    assert section.status == self_check.OK
    assert seen == [(b"digest", b"bundle")]
    assert "release-workflow" in section.detail_en
    assert "release-workflow" in section.detail_ar
    assert "Sigstore" in section.detail_en
    assert "Sigstore" in section.detail_ar

    def refused(digest: bytes, bundle: bytes) -> str:
        raise SignatureRefused("the bundle does not match the wheel")

    bad = self_check._release_from(ready, refused)
    assert bad.status == self_check.ATTENTION
    assert "the bundle does not match the wheel" in bad.detail_en
    assert "Sigstore" in bad.detail_en


def test_a_release_check_that_raises_hides_the_exception(monkeypatch) -> None:
    _silence_verbose(monkeypatch)
    monkeypatch.setattr(self_check, "_sigstore_present", lambda: True)

    def broken(digest: bytes, bundle: bytes) -> str:
        raise RuntimeError("PRIVATE-KEY-MATERIAL")

    ready = self_check.ReleaseArtifact("ready", "0.21.2", b"digest", b"bundle")
    section = self_check._guard("release", lambda: self_check._release_from(ready, broken, "git"))
    assert section.status == self_check.NOT_RUN
    assert "PRIVATE-KEY-MATERIAL" not in section.detail_en
    assert "PRIVATE-KEY-MATERIAL" not in section.detail_ar
    assert "ref:" in section.detail_en


def test_find_release_artifact_never_fetches_and_reads_a_local_bundle(monkeypatch, tmp_path) -> None:
    class Dist:
        def __init__(self, version: str, raw: str | None) -> None:
            self.version = version
            self._raw = raw

        def read_text(self, name: str) -> str | None:
            assert name == "direct_url.json"
            return self._raw

    def use(dist: Dist) -> None:
        monkeypatch.setattr(self_check, "_distribution", lambda: dist)

    use(Dist("0.21.2", json.dumps({"url": "https://example.invalid/kazma.whl"})))
    assert self_check._find_release_artifact().kind == "no_bundle"
    use(Dist("0.21.2", json.dumps({"dir_info": {"editable": True}, "url": "file:///src"})))
    assert self_check._find_release_artifact().kind == "editable"
    wheel = tmp_path / "kazma-0.21.2-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    bundle = Path(str(wheel) + ".sigstore.json")
    bundle.write_bytes(b'{"mediaType":"bundle"}')
    use(Dist("0.21.2", json.dumps({"url": wheel.as_uri()})))
    found = self_check._find_release_artifact()
    assert found.kind == "ready"
    assert found.digest == hashlib.sha256(b"wheel-bytes").digest()
    assert found.bundle == b'{"mediaType":"bundle"}'
    bundle.write_bytes(b"")
    assert self_check._find_release_artifact().kind == "no_bundle"

    def missing() -> None:
        from importlib.metadata import PackageNotFoundError

        raise PackageNotFoundError("kazma")

    monkeypatch.setattr(self_check, "_distribution", missing)
    assert self_check._find_release_artifact().kind == "missing"


def test_every_known_gap_rule_quotes_an_open_entry() -> None:
    for rule in gaps.RULES:
        assert gaps._quote_is_open(GAPS_TEXT, rule.quote) is True, rule.id


def test_a_closed_known_gap_is_not_reported_as_applying() -> None:
    quote = "The restore rehearsal is off by default"
    assert gaps._quote_is_open(GAPS_TEXT, quote) is False
    planted = gaps.GapRule(
        id="closed-rehearsal",
        quote=quote,
        title_en="closed",
        title_ar="مغلق",
        detail_en="should not apply",
        detail_ar="لا ينطبق",
        applies=lambda: True,
    )
    found = gaps.judge_rules(GAPS_TEXT, (planted,))
    assert [item.status for item in found] == ["not_run"]
    assert "should not apply" not in found[0].detail_en


def test_postgres_gap_follows_a_moved_data_folder(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "postgres-data-dir")
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: True)
    monkeypatch.setattr("kazma_core.paths.data_dir_is_relocated", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []
    monkeypatch.setattr("kazma_core.paths.data_dir_is_relocated", lambda: True)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_python_exec_gap_follows_container_required(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "python-exec-on-the-host")
    monkeypatch.setattr("kazma_core.safety.deployment_policy.container_required", lambda: False)
    monkeypatch.setattr("kazma_core.tools.code_exec.docker_exec_forced", lambda: False)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr("kazma_core.tools.code_exec.docker_exec_forced", lambda: True)
    assert gaps.judge_rules(rule.quote, (rule,)) == []
    monkeypatch.setattr("kazma_core.tools.code_exec.docker_exec_forced", lambda: False)
    monkeypatch.setattr("kazma_core.safety.deployment_policy.container_required", lambda: True)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_trusted_mcp_gap_needs_a_trusted_server(monkeypatch) -> None:
    from kazma_core.mcp.manager import AsyncMCPManager

    rule = next(item for item in gaps.RULES if item.id == "mcp-trusted-skips-the-gate")
    monkeypatch.setattr(
        "kazma_core.config_store.get_config_store",
        lambda: SimpleNamespace(get=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(AsyncMCPManager, "_trusted_allowed_in_prod", lambda: True)
    monkeypatch.setattr("kazma_core.mcp_servers_store.list_mcp_servers", lambda **kwargs: [])
    assert gaps.judge_rules(rule.quote, (rule,)) == []
    monkeypatch.setattr(
        "kazma_core.mcp_servers_store.list_mcp_servers",
        lambda **kwargs: [{"name": "demo", "trust": " trusted "}],
    )
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr(AsyncMCPManager, "_trusted_allowed_in_prod", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_allowlist_gap_asks_the_existing_reader(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "mcp-safe-allowlist")
    monkeypatch.setattr("kazma_core.mcp.manager.mcp_safe_allowlist_names_a_tool", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []
    monkeypatch.setattr("kazma_core.mcp.manager.mcp_safe_allowlist_names_a_tool", lambda: True)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    source = Path(gaps.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source


def test_a_setting_question_that_raises_is_not_run(monkeypatch) -> None:
    _silence_verbose(monkeypatch)
    rule = next(item for item in gaps.RULES if item.id == "postgres-data-dir")

    def boom() -> bool:
        raise RuntimeError("db-url-SECRET")

    monkeypatch.setattr("kazma_core.db.backend.is_postgres", boom)
    found = gaps.judge_rules(rule.quote, (rule,))
    assert [item.status for item in found] == ["not_run"]
    assert "db-url-SECRET" not in found[0].detail_en
    assert "db-url-SECRET" not in found[0].detail_ar
    assert "ref:" in found[0].detail_en


def test_gaps_from_never_counts_an_ok_item_as_a_pass() -> None:
    clear = self_check._gaps_from([])
    assert clear.status == self_check.OK
    assert "None of the setting-conditional" in clear.detail_en
    forced = self_check._gaps_from(
        [
            SimpleNamespace(status="ok", title_en="t", title_ar="ت", detail_en="d", detail_ar="ذ"),
        ]
    )
    assert forced.status == self_check.NOT_RUN
    assert forced.items[0].status == self_check.NOT_RUN
    mixed = self_check._gaps_from(
        [
            SimpleNamespace(status="attention", title_en="a", title_ar="أ", detail_en="d", detail_ar="ذ"),
            SimpleNamespace(status="not_run", title_en="b", title_ar="ب", detail_en="e", detail_ar="ه"),
        ]
    )
    assert mixed.status == self_check.ATTENTION


def test_a_missing_known_gaps_page_is_not_run(monkeypatch, tmp_path) -> None:
    _silence_verbose(monkeypatch)
    missing = tmp_path / "HIDDEN-PAGE-PATH" / "KNOWN_GAPS.md"
    monkeypatch.setattr("kazma_cli.known_gap_settings.gaps_path", lambda: missing)
    section = self_check._gaps()
    assert section.status == self_check.NOT_RUN
    assert "HIDDEN-PAGE-PATH" not in section.detail_en
    assert "HIDDEN-PAGE-PATH" not in section.detail_ar


def test_not_run_exits_1_in_text_and_json(monkeypatch, capsys) -> None:
    _keep_data_dir()

    def health() -> self_check.Section:
        return self_check._not_run("health", "The health checks did not run.", "فحوص الصحة لم تُجرَ.")

    _install(monkeypatch, health=health)
    assert self_check.run([]) == 1
    text = capsys.readouterr().out
    assert "Kazma self-check" in text
    assert "فحص كاظمه" in text
    assert "Health — not run" in text
    assert "الصحة — لم يُجرَ" in text
    assert "Something needs attention. This command exits 1." in text
    assert "شيء يحتاج انتباهاً. هذا الأمر يخرج بالرمز 1." in text
    assert self_check.run(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"version", "needs_attention", "sections"}
    assert payload["version"] == 1
    assert payload["needs_attention"] is True
    assert [row["id"] for row in payload["sections"]] == list(self_check.SECTION_ORDER)
    assert payload["sections"][0]["status"] == "not_run"
    assert payload["sections"][0]["title"] == {"en": "Health", "ar": "الصحة"}


def test_every_section_ok_exits_0(monkeypatch, capsys) -> None:
    _keep_data_dir()
    _install(monkeypatch)
    assert self_check.run(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["needs_attention"] is False
    assert {row["status"] for row in payload["sections"]} == {"ok"}
    assert self_check.run([]) == 0
    text = capsys.readouterr().out
    assert "Nothing needs attention." in text
    assert "لا شيء يحتاج انتباهاً." in text
    assert "Health — ok" in text
    assert "الصحة — سليم" in text


def test_a_check_that_raises_is_not_run_and_hides_the_exception(monkeypatch, capsys) -> None:
    _keep_data_dir()
    _silence_verbose(monkeypatch)

    def health() -> self_check.Section:
        raise RuntimeError(r"C:\Users\secret\kazma-data\settings.db")

    _install(monkeypatch, health=health)
    assert self_check.run(["--json"]) == 1
    out = capsys.readouterr().out
    assert "settings.db" not in out
    assert "kazma-data" not in out
    assert json.loads(out)["sections"][0]["status"] == "not_run"


def test_bad_arguments_and_help_do_not_run_the_checks(monkeypatch, capsys) -> None:
    def collect() -> self_check.Report:
        raise AssertionError("checks ran")

    monkeypatch.setattr(self_check, "collect", collect)
    assert self_check.run(["--no-such"]) == 2
    assert self_check.run(["--help"]) == 0
    captured = capsys.readouterr()
    assert "--json" in captured.out


def test_a_secret_in_a_called_check_is_redacted() -> None:
    planted = "api_key=demo-plain-value-123"
    masked = self_check._mask(f"endpoint {planted}")
    assert "demo-plain-value-123" not in masked
    assert "<redacted>" in masked


def test_an_odd_exception_does_not_end_the_report(monkeypatch, capsys) -> None:
    """A failure outside the old builtin list is not run, and its text is hidden."""
    _keep_data_dir()
    _silence_verbose(monkeypatch)

    class OddFailure(Exception):
        pass

    def health() -> self_check.Section:
        raise OddFailure("api_key=demo-plain-value-123")

    _install(monkeypatch, health=health)
    assert self_check.run(["--json"]) == 1
    out = capsys.readouterr().out
    assert "demo-plain-value-123" not in out
    assert json.loads(out)["sections"][0]["status"] == "not_run"


def test_a_skill_check_that_raises_is_unchecked(monkeypatch) -> None:
    from kazma_core.agent_skills.catalog import skill_integrity

    def boom(skill, warn=False):
        raise RuntimeError("api_key=demo-plain-value-123")

    monkeypatch.setattr("kazma_core.agent_skills.catalog.activation_integrity", boom)
    state, reason = skill_integrity(SimpleNamespace())
    assert state == "unchecked"
    assert "demo-plain-value-123" not in reason
    assert reason == "the integrity check did not finish"


def test_windows_file_check_is_shared_with_self_check(monkeypatch) -> None:
    import kazma_cli.doctor as doctor

    monkeypatch.setattr(doctor, "collect", lambda: [("ok", "model")])
    monkeypatch.setattr(doctor, "_system_rows", lambda: [("ok", "deps")])
    monkeypatch.setattr(doctor, "_database_service_rows", lambda: [])
    monkeypatch.setattr(doctor, "_second_lock_rows", lambda: [])
    monkeypatch.setattr(doctor, "_cloud_profile_rows", lambda: [("info", "off")])
    monkeypatch.setattr(doctor, "_windows_rows", lambda: [])
    titles = [title for title, _group in doctor.report_sections()]
    assert "Windows' file check" not in titles
    monkeypatch.setattr(doctor, "_windows_rows", lambda: [("info", "Smart App Control is on")])
    titles = [title for title, _group in doctor.report_sections()]
    assert titles[2] == "Windows' file check"
    assert titles[-1] == "The cloud profile"


def test_a_partial_hardening_check_did_not_run(monkeypatch) -> None:
    from kazma_core.security.hardening import SecurityHardeningRunner

    monkeypatch.setattr(
        "kazma_core.security.service_exposure.exposure_problems",
        lambda: ([], ["docker"]),
    )
    check = SecurityHardeningRunner(Path("."))._exposure()
    assert check.passed is True
    assert check.ran is False
    assert "not checked" in check.message
    monkeypatch.setattr(
        "kazma_core.security.service_exposure.searxng_secret_problems",
        lambda: ([], ["compose"]),
    )
    secret = SecurityHardeningRunner(Path("."))._searxng_secret()
    assert secret.passed is True
    assert secret.ran is False


def _unchecked_constructors(tree: ast.AST) -> list[str]:
    """Functions that bind ``unchecked`` and build a HardeningCheck without ``ran``."""
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        binds = any(
            isinstance(target, ast.Name) and target.id == "unchecked"
            for child in ast.walk(node)
            if isinstance(child, ast.Assign)
            for target in child.targets
        )
        if not binds:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name != "HardeningCheck":
                continue
            if not any(keyword.arg == "ran" for keyword in call.keywords):
                missing.append(node.name)
    return missing


def test_every_unchecked_hardening_check_says_whether_it_ran() -> None:
    source = Path(
        ROOT / "kazma-core" / "kazma_core" / "security" / "hardening.py"
    ).read_text(encoding="utf-8")
    assert _unchecked_constructors(ast.parse(source)) == []


def test_negative_control_an_unchecked_pass_without_ran_is_named() -> None:
    planted = "def probe():\n    unchecked = ['docker']\n    return HardeningCheck(passed=True)\n"
    assert _unchecked_constructors(ast.parse(planted)) == ["probe"]


def test_an_ok_backup_says_when_it_was_taken(tmp_path) -> None:
    import time

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "manifest.json").write_text(json.dumps({"timestamp": 1700000000}), encoding="utf-8")
    held = DrillResult(str(backup))
    held.add("backup:exists", True, "found")
    section = self_check._backup_from(held)
    shown = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(1700000000))
    assert shown in section.detail_en
    assert shown in section.detail_ar
    assert section.items[0].title_ar is None
    unnamed = tmp_path / "1700000000"
    unnamed.mkdir()
    fallback = DrillResult(str(unnamed))
    fallback.add("backup:exists", True, "found")
    again = self_check._backup_from(fallback)
    assert shown in again.detail_en


def test_the_hardening_payload_says_whether_a_check_ran() -> None:
    from kazma_ui.settings import security_hardening_payload

    report = SimpleNamespace(
        total=1, passed=1, failed=0, critical_failures=0, timestamp="t",
        checks=[SimpleNamespace(
            name="deps", passed=True, ran=False, severity="high", message="partial", recommendation="retry",
        )],
    )
    body = security_hardening_payload(report)
    assert body["checks"][0]["ran"] is False
    assert body["checks"][0]["recommendation"] == "retry"


def test_every_open_gap_setting_has_a_rule_or_a_reason() -> None:
    named = gaps.open_gap_settings(GAPS_TEXT)
    ruled = set().union(*(rule.settings for rule in gaps.RULES))
    exempt = set(gaps.EXEMPTIONS)
    assert not (ruled & exempt)
    assert all(reason.strip() for reason in gaps.EXEMPTIONS.values())
    assert named == ruled | exempt


def test_negative_control_a_planted_gap_setting_has_no_cover() -> None:
    named = gaps.open_gap_settings(GAPS_TEXT + "\nKAZMA_PLANTED_GAP\n")
    covered = set().union(*(rule.settings for rule in gaps.RULES)) | set(gaps.EXEMPTIONS)
    assert named - covered == {"KAZMA_PLANTED_GAP"}


_ARABIC = __import__("re").compile(r"[؀-ۿ]")


def _borrowed_english(tree: ast.AST) -> list[str]:
    """Constant Arabic arguments of ``_section``, ``_not_run`` and ``Item`` that copy English."""
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name not in {"_section", "_not_run", "Item"}:
            continue
        constants = [arg for arg in node.args if isinstance(arg, ast.Constant)]
        strings = [arg.value for arg in constants if isinstance(arg.value, str)]
        if name == "Item" and len(strings) >= 2 and strings[0] == strings[1]:
            bad.append("Item")
        if name == "Item" and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
            arabic = node.args[2].value
            english = node.args[1].value if isinstance(node.args[1], ast.Constant) else None
            if isinstance(arabic, str) and (not _ARABIC.search(arabic) or arabic == english):
                bad.append("Item")
        if name in {"_section", "_not_run"} and len(strings) >= 2:
            english, arabic = strings[-2], strings[-1]
            if arabic == english or not _ARABIC.search(arabic):
                bad.append(name)
    return bad


def test_self_check_arabic_is_not_copied_english() -> None:
    assert _borrowed_english(ast.parse(_SOURCE)) == []


def test_negative_control_copied_english_is_named() -> None:
    planted = "Item(OK, 'Hello', 'Hello')\n"
    assert "Item" in _borrowed_english(ast.parse(planted))


def _mkdir_functions(tree: ast.AST) -> list[str]:
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        creates = False
        guarded = False
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "mkdir":
                creates = True
            if isinstance(child, ast.Name) and child.id == "_self_check_reading":
                guarded = True
            if isinstance(child, ast.Attribute) and child.attr == "_self_check_reading":
                guarded = True
        if creates and not guarded:
            missing.append(node.name)
    return missing


def test_self_check_does_not_create_a_path_helper_directory() -> None:
    source = (ROOT / "kazma-core" / "kazma_core" / "paths.py").read_text(encoding="utf-8")
    assert _mkdir_functions(ast.parse(source)) == []


def test_negative_control_an_unguarded_mkdir_is_named() -> None:
    planted = "def scratch():\n    path.mkdir()\n"
    assert _mkdir_functions(ast.parse(planted)) == ["scratch"]


def _snapshot(root: Path) -> tuple[dict[str, tuple[int, str]], set[str]]:
    files: dict[str, tuple[int, str]] = {}
    dirs: set[str] = set()
    for path in root.rglob("*"):
        rel = str(path.relative_to(root))
        if path.is_dir():
            dirs.add(rel)
            continue
        if path.is_file():
            files[rel] = (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return files, dirs


def test_negative_control_the_snapshot_sees_a_planted_write(tmp_path) -> None:
    before = _snapshot(tmp_path)
    (tmp_path / "planted").write_text("x", encoding="utf-8")
    assert _snapshot(tmp_path) != before


def _child_collect(env: dict[str, str], cwd: Path) -> str:
    import subprocess
    import sys

    code = (
        "from kazma_cli.self_check import collect\n"
        "report = collect()\n"
        "print(report.needs_attention)\n"
        "print(','.join(section.status for section in report.sections))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["KAZMA_DATA_DIR"] = str(tmp_path / "data")
    env["KAZMA_USER_HOME"] = str(tmp_path / "home")
    env["KAZMA_SKILLS_HOME"] = str(tmp_path / "skills")
    env["KAZMA_PROJECT_ROOT"] = str(tmp_path / "project")
    env["KAZMA_ENV_FILE"] = str(tmp_path / "none.env")
    env["KAZMA_DB_BACKEND"] = "sqlite"
    env.pop("KAZMA_DATABASE_URL", None)
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_an_empty_or_mistyped_data_folder_is_not_created(tmp_path) -> None:
    env = _isolated_env(tmp_path)
    out = _child_collect(env, tmp_path)
    assert out.splitlines()[0] == "True"
    assert set(out.splitlines()[1].split(",")) == {"not_run"}
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "skills").exists()

    empty = tmp_path / "empty-data"
    empty.mkdir()
    env["KAZMA_DATA_DIR"] = str(empty)
    before = _snapshot(tmp_path)
    _child_collect(env, tmp_path)
    assert _snapshot(tmp_path) == before

    target = tmp_path / "not-a-folder"
    target.write_text("mistyped", encoding="utf-8")
    env["KAZMA_DATA_DIR"] = str(target)
    before = _snapshot(tmp_path)
    _child_collect(env, tmp_path)
    assert target.read_text(encoding="utf-8") == "mistyped"
    assert _snapshot(tmp_path) == before


def test_a_seeded_data_folder_is_left_byte_for_byte(tmp_path) -> None:
    import sqlite3

    data = tmp_path / "data"
    data.mkdir()
    db = data / "settings.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "category TEXT NOT NULL DEFAULT 'general', updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO settings (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
        ("registry.active_model", '"demo"', "general", "2026-01-01"),
    )
    conn.commit()
    conn.close()
    env = _isolated_env(tmp_path)
    # The dependency check must not reach the network. It imports audit_installed
    # at call time, so the child replaces it before collect runs.
    code_path = tmp_path / "run_self_check.py"
    code_path.write_text(
        "import kazma_core.security.dependency_scanner as scanner\n"
        "from kazma_core.security.dependency_scanner import DependencyQueryError\n"
        "async def offline(**kwargs):\n"
        "    raise DependencyQueryError('offline')\n"
        "scanner.audit_installed = offline\n"
        "from kazma_cli.self_check import collect\n"
        "report = collect()\n"
        "print(report.needs_attention)\n",
        encoding="utf-8",
    )
    before = _snapshot(tmp_path)
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, str(code_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert not (data / "hitl_gates.db").exists()
    assert not (tmp_path / "home").exists()
    assert _snapshot(tmp_path) == before

