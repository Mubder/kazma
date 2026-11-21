"""Research papers, reports, local code runs and research runs are each
space's own once Spaces is enforced.

* **The papers index**: each space keeps its own (the owner's under
  ``research.papers_index``, every other space's under ``tenant.<id>.``), so
  the Research page and the agent's ``list_research_papers`` show a space
  its own papers, and a space's count, export and erase reach its index.
* **Reports**: a space reads the reports under its own root only; the
  machine's other roots (``KAZMA_WORKSPACE``, the default sandbox) are the
  owner's space's -- a second host space (S1-34) no longer reads them.
* **Local code runs**: only the owner's space runs ``python_exec`` on the
  machine itself; every other space a container or nothing.
* **Research runs**: a research job records its space when it starts; the
  run supervisor and the acquisition monitor read it, never a guess from
  the folder.

Controls: one index for the install shows the first person's topic to the
owner; the report roots of before hand a host space the owner's report;
the local-run rule of before lets a host space run on the machine; the
folder guess counts a host space's job as the owner's.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

MEMBER_TOPIC = "membertopic" + uuid.uuid4().hex[:6]
OWNER_TOPIC = "ownertopic" + uuid.uuid4().hex[:6]


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(scope, "_enforced", True)
    person = people.create_person("papers-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    host = identity.create_space("host-one", created_by=person["id"], kind="host")
    store = get_config_store()
    try:
        yield {"person": person, "space": person["personal_space"], "host": host["id"]}
    finally:
        for key in list(store.get_all().get("research", {})):
            store.delete(key)
        store.delete("research.papers_index")
        for space in (person["personal_space"], host["id"]):
            store.delete(f"tenant.{space}.research.papers_index")
        store.delete(identity.REGISTRY_KEY)


def _register(topic: str) -> None:
    from kazma_core.tools.research_pipeline import _register_paper

    _register_paper({"id": topic, "topic": topic, "report_path": f"research/reports/{topic}/report.md",
                     "kind": "research_paper"})


def _listed() -> str:
    from kazma_core.tools.research_pipeline import list_research_papers

    return repr(list_research_papers())


def test_each_space_lists_its_own_papers(world) -> None:
    with scope.bind_scope(world["space"], world["person"]["id"]):
        _register(MEMBER_TOPIC)
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        _register(OWNER_TOPIC)
        owners = _listed()
    with scope.bind_scope(world["space"], world["person"]["id"]):
        members = _listed()
    assert OWNER_TOPIC in owners and MEMBER_TOPIC not in owners
    assert MEMBER_TOPIC in members and OWNER_TOPIC not in members


def test_a_spaces_index_is_reached_by_its_count_export_and_erase(world) -> None:
    from kazma_core.spaces import space_data

    with scope.bind_scope(world["space"], world["person"]["id"]):
        _register(MEMBER_TOPIC)
    keys = space_data._settings_keys(world["space"], [])
    assert f"tenant.{world['space']}.research.papers_index" in keys


def test_a_finished_paper_is_kept_with_its_own_spaces_run_only(world) -> None:
    from kazma_core.tools.research_final_paper import _run_entry
    from kazma_core.tools.research_pipeline import _register_paper

    turn = "turn-" + uuid.uuid4().hex
    with scope.bind_scope(world["space"], world["person"]["id"]):
        _register_paper({"id": MEMBER_TOPIC, "topic": MEMBER_TOPIC, "turn": turn,
                         "report_path": f"research/reports/{MEMBER_TOPIC}/report.md"})
        assert _run_entry(turn) is not None
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert _run_entry(turn) is None


def test_not_enforced_one_index_as_before(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    _register(MEMBER_TOPIC)
    assert MEMBER_TOPIC in _listed()
    assert any(MEMBER_TOPIC in str(item) for item in get_config_store().get("research.papers_index"))


def test_control_one_index_for_the_install_shows_the_members_topic(world, monkeypatch) -> None:
    from kazma_core.tools import research_pipeline

    monkeypatch.setattr(research_pipeline, "papers_index_key", lambda: "research.papers_index")
    with scope.bind_scope(world["space"], world["person"]["id"]):
        _register(MEMBER_TOPIC)
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert MEMBER_TOPIC in _listed()


# ── reports under a host space's own root only ───────────────────────────


@pytest.fixture
def owners_report(tmp_path, monkeypatch) -> Path:
    machine = tmp_path / "machine-workspace"
    report = machine / "research" / "reports" / f"{OWNER_TOPIC}-1" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text(f"# {OWNER_TOPIC}\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_WORKSPACE", str(machine))
    return report


def test_a_host_space_reads_no_report_of_the_machines_roots(world, owners_report) -> None:
    from kazma_core.tools.research_pipeline import _candidate_report_roots, resolve_report_file

    with scope.bind_scope(world["host"], world["person"]["id"]):
        roots = _candidate_report_roots()
        assert owners_report.parents[3].resolve() not in roots
        assert OWNER_TOPIC not in _listed()
        assert resolve_report_file(f"research/reports/{OWNER_TOPIC}-1/report.md") is None
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert owners_report.parents[3].resolve() in _candidate_report_roots()


def test_control_the_roots_of_before_hand_a_host_space_the_owners_report(
    world, owners_report, monkeypatch
) -> None:
    from kazma_core.tools import research_pipeline

    monkeypatch.setattr(research_pipeline, "_owners_space_here", lambda: True)
    with scope.bind_scope(world["host"], world["person"]["id"]):
        assert owners_report.parents[3].resolve() in research_pipeline._candidate_report_roots()


# ── local code runs ──────────────────────────────────────────────────────


@pytest.fixture
def local_allowed(monkeypatch):
    monkeypatch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
    monkeypatch.delenv("KAZMA_CODE_EXEC_DOCKER", raising=False)
    from kazma_core.safety import deployment_policy

    monkeypatch.setattr(deployment_policy, "container_required", lambda: False)


def test_only_the_owners_space_runs_code_on_the_machine(world, local_allowed) -> None:
    from kazma_core.tools.code_exec import local_exec_forbidden

    with scope.bind_scope(world["host"], world["person"]["id"]):
        assert local_exec_forbidden() is True
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert local_exec_forbidden() is False
    assert local_exec_forbidden() is True  # nothing bound: nobody's


def test_control_the_local_run_rule_of_before_let_a_host_space_run(world, local_allowed, monkeypatch) -> None:
    from kazma_core.tools import code_exec

    monkeypatch.setattr(scope, "_enforced", False)  # the rule of before asked no space
    with scope.bind_scope(world["host"], world["person"]["id"]):
        assert code_exec.local_exec_forbidden() is False


# ── research runs record their space ─────────────────────────────────────


def test_a_research_job_is_counted_in_its_recorded_space(world, tmp_path) -> None:
    from kazma_core.runs.adapters.research import _CrawlSpaces

    row = {"policy": {"workspace": str(tmp_path / "host-root"), "space": world["host"]}}
    assert _CrawlSpaces().of(row) == world["host"]


def test_control_the_folder_guess_counts_a_host_spaces_job_as_the_owners(world, tmp_path) -> None:
    from kazma_core.runs.adapters.research import _CrawlSpaces

    row = {"policy": {"workspace": str(tmp_path / "host-root")}}
    assert _CrawlSpaces().of(row) in ("", OWNER_SPACE_ID)


async def test_a_research_crawl_freezes_its_space(world, tmp_path, monkeypatch) -> None:
    from kazma_core.web_acquire import crawl_jobs, research_crawl

    started: list[dict] = []

    def _start(library, seed, policy, **kwargs):
        started.append(policy)
        raise RuntimeError("stop here")

    monkeypatch.setattr(crawl_jobs, "start", _start)
    with scope.bind_scope(world["host"], world["person"]["id"]):
        with pytest.raises(RuntimeError):
            async with research_crawl.research_job(
                    "https://example.org/", tmp_path, {"max_pages": 1, "max_depth": 0, "path_prefix": ""}):
                pass
    assert started and started[0]["space"] == world["host"]
