"""``kazma self-check``: the checks Kazma already has, and a miss is not a pass.

A check that did not run is ``not_run``. An empty drill whose own verdict
would be PASS, a hardening check with ``passed`` true and ``ran`` false, and
a known-gap quote that sits only inside a closed entry are the controls.
"""

from __future__ import annotations

import ast
import hashlib
import json
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


def test_collect_opens_the_read_only_diagnostic(monkeypatch) -> None:
    from kazma_core.diagnostic_scope import active_diagnostic

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
        self_check.OK,
    ]
    assert self_check._health_from([("info", "the cloud profile is off")]).status == self_check.OK
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
    failed = self_check._skills_from([("demo", "unsigned", "the integrity check failed: boom")])
    assert failed.status == self_check.NOT_RUN
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


def test_release_signature_is_not_run_unless_a_bundle_can_be_checked(monkeypatch) -> None:
    def checker(digest: bytes, bundle: bytes) -> str:
        raise AssertionError("the checker ran")

    for kind, version in (("missing", ""), ("editable", "0.21.2"), ("no_bundle", "0.21.2")):
        section = self_check._release_from(self_check.ReleaseArtifact(kind, version), checker)
        assert section.status == self_check.NOT_RUN, kind
        assert "holds" not in section.detail_en
    older = self_check._release_from(self_check.ReleaseArtifact("editable", "0.21.1"), checker)
    assert older.status == self_check.NOT_RUN
    assert "0.21.2" in older.detail_en
    odd = self_check._release_from(self_check.ReleaseArtifact("editable", "0.21.2+gSHA"), checker)
    assert odd.status == self_check.NOT_RUN
    assert "older than" not in odd.detail_en
    monkeypatch.setattr(self_check, "_sigstore_present", lambda: False)
    ready = self_check.ReleaseArtifact("ready", "0.21.2", b"digest", b"bundle")
    assert self_check._release_from(ready, checker).status == self_check.NOT_RUN


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

    def refused(digest: bytes, bundle: bytes) -> str:
        raise SignatureRefused("the bundle does not match the wheel")

    bad = self_check._release_from(ready, refused)
    assert bad.status == self_check.ATTENTION
    assert "the bundle does not match the wheel" in bad.detail_en


def test_a_release_check_that_raises_hides_the_exception(monkeypatch) -> None:
    _silence_verbose(monkeypatch)
    monkeypatch.setattr(self_check, "_sigstore_present", lambda: True)

    def broken(digest: bytes, bundle: bytes) -> str:
        raise RuntimeError("PRIVATE-KEY-MATERIAL")

    ready = self_check.ReleaseArtifact("ready", "0.21.2", b"digest", b"bundle")
    section = self_check._release_from(ready, broken)
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


def test_postgres_gap_follows_is_postgres(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "postgres-data-dir")
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: True)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_python_exec_gap_follows_local_exec_forbidden(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "python-exec-on-the-host")
    monkeypatch.setattr("kazma_core.tools.code_exec.local_exec_forbidden", lambda: False)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr("kazma_core.tools.code_exec.local_exec_forbidden", lambda: True)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_trusted_mcp_gap_follows_the_production_answer(monkeypatch) -> None:
    from kazma_core.mcp.manager import AsyncMCPManager

    rule = next(item for item in gaps.RULES if item.id == "mcp-trusted-skips-the-gate")
    monkeypatch.setattr(AsyncMCPManager, "_trusted_allowed_in_prod", lambda: True)
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    monkeypatch.setattr(AsyncMCPManager, "_trusted_allowed_in_prod", lambda: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


def test_allowlist_gap_asks_mcp_safe_allowlisted(monkeypatch) -> None:
    rule = next(item for item in gaps.RULES if item.id == "mcp-safe-allowlist")
    seen: list[str] = []

    def membership(name: str) -> bool:
        seen.append(name)
        return name == "other"

    monkeypatch.setattr("kazma_core.mcp.manager.mcp_safe_allowlisted", membership)
    monkeypatch.setenv("KAZMA_MCP_SAFE_ALLOWLIST", "")
    assert gaps.judge_rules(rule.quote, (rule,)) == []
    assert seen == []
    monkeypatch.setenv("KAZMA_MCP_SAFE_ALLOWLIST", " read_file , other ")
    assert [item.status for item in gaps.judge_rules(rule.quote, (rule,))] == ["attention"]
    assert seen == ["read_file", "other"]
    monkeypatch.setattr("kazma_core.mcp.manager.mcp_safe_allowlisted", lambda name: False)
    assert gaps.judge_rules(rule.quote, (rule,)) == []


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
    def health() -> self_check.Section:
        return self_check._not_run("health", "The health checks did not run.", "فحوص السلامة لم تُجرَ.")

    _install(monkeypatch, health=health)
    assert self_check.run([]) == 1
    text = capsys.readouterr().out
    assert "Kazma self-check" in text
    assert "فحص كاظمه" in text
    assert "Health — not run" in text
    assert "السلامة — لم يُجرَ" in text
    assert "Something needs attention. This command exits 1." in text
    assert "شيء يحتاج انتباهاً. هذا الأمر يخرج بالرمز 1." in text
    assert self_check.run(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"needs_attention", "sections"}
    assert payload["needs_attention"] is True
    assert [row["id"] for row in payload["sections"]] == list(self_check.SECTION_ORDER)
    assert payload["sections"][0]["status"] == "not_run"
    assert payload["sections"][0]["title"] == {"en": "Health", "ar": "السلامة"}


def test_every_section_ok_exits_0(monkeypatch, capsys) -> None:
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
    assert "السلامة — سليم" in text


def test_a_check_that_raises_is_not_run_and_hides_the_exception(monkeypatch, capsys) -> None:
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
