"""X Studio drafting reads the operator's reference documents.

Settings -> X names up to three text files (``connectors.x.drafting.
reference_files``). Every Generate reads them whole: the drafter gets each
file fenced as data, with the rule that a post states only what the files or
the brief support, and the fact check gets each as evidence a claim can cite
(``evidence.source_hold`` accepts a reference file's provenance). A named file
that cannot be read stops the draft before any model call.

Before 2026-10-26 the drafter saw only the brief. The negative control runs
the same drafting with the loader returning nothing, as that code did, and
the grounding check fails on it.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

MARKER = "Kazma asks before it acts. REF-MARKER-7Q"


@pytest.fixture
def drafting(monkeypatch, tmp_path):
    from kazma_core.x_api.stance import ReplyConfig

    state = {"calls": 0, "sources": [], "output": {"drafts": ["Kazma asks before it acts."]}}

    class Client:
        config = SimpleNamespace(model="studio-local", base_url="http://127.0.0.1:1234/v1")

        async def chat(self, messages, **kwargs):
            state["calls"] += 1
            state["messages"] = messages
            return SimpleNamespace(content=json.dumps(state["output"]))

    class Registry:
        def list_providers(self):
            return [{"name": "local", "base_url": "http://127.0.0.1:1234/v1"}]

        def get_client_by_provider(self, provider, model):
            return Client()

        def get_active_profile(self):
            return {"provider": "local", "model": "global-local"}

        def get_client(self):
            return None

        async def release_client(self, client):
            pass

    async def verify(draft, subject, *, context, sources=()):
        state["sources"].append(tuple(sources))
        return ()

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())
    monkeypatch.setattr("kazma_core.x_api.verification.verify_candidate", verify)
    reference = tmp_path / "what-is-kazma.md"
    reference.write_text(f"# What Kazma is\n\n{MARKER}\n", encoding="utf-8")
    state["path"] = reference

    def configure(*paths):
        cfg = ReplyConfig(enabled=False, mode="draft", summoners=(), trigger="", max_replies_per_day=5,
                          max_replies_per_target_per_day=1, cooldown_per_thread_s=0, min_target_followers=0,
                          poll_interval_s=60, drafting_references=tuple(str(p) for p in paths))
        monkeypatch.setattr("kazma_core.x_api.stance.get_reply_config", lambda: cfg)

    state["configure"] = configure
    configure(reference)
    from kazma_core.config_store import get_config_store
    get_config_store().set("connectors.x.ai", {"selection": "global", "local_only": True,
                          "roles": {"post_drafting": {"selection": "specific", "provider": "local",
                                                      "model": "studio-local"}}})
    return state


def assert_grounded(state, result) -> None:
    """The drafter read the reference and the fact check can cite it."""
    from kazma_core.x_api.evidence import source_hold

    system, user = state["messages"][0]["content"], state["messages"][1]["content"]
    assert MARKER in user, "the drafter was not given the reference document"
    assert 'source="x_reference:what-is-kazma.md"' in user or "x_reference:what-is-kazma.md" in user
    assert "reference documents" in system
    assert state["sources"] and all(len(group) == 1 for group in state["sources"])
    source = state["sources"][0][0]
    assert source["kind"] == "reference_file" and MARKER in source["content"]
    assert source_hold(source) == ""
    assert [ref["name"] for ref in result.references] == ["what-is-kazma.md"]


async def test_the_drafter_reads_the_reference_and_the_check_can_cite_it(drafting):
    from kazma_core.x_api.post_drafting import draft_posts

    result = await draft_posts("A post about why Kazma asks first.")
    assert_grounded(drafting, result)
    assert result.reviews[0]["references"] == list(result.references)


async def test_negative_control_drafting_without_the_reference_fails_the_check(drafting, monkeypatch):
    from kazma_core.x_api.post_drafting import draft_posts

    # What the code before 2026-10-26 did: the brief alone.
    monkeypatch.setattr("kazma_core.x_api.post_drafting.load_references", lambda paths: ())
    result = await draft_posts("A post about why Kazma asks first.")
    with pytest.raises(AssertionError):
        assert_grounded(drafting, result)


async def test_an_edited_reference_is_read_at_the_next_generate(drafting):
    from kazma_core.x_api.post_drafting import draft_posts

    await draft_posts("First.")
    drafting["path"].write_text("Kazma now reads reference documents. EDITED-9", encoding="utf-8")
    await draft_posts("Second.")
    assert "EDITED-9" in drafting["messages"][1]["content"]
    assert MARKER not in drafting["messages"][1]["content"]


async def test_a_reference_that_cannot_be_read_stops_the_draft_before_any_model_call(drafting, tmp_path):
    from kazma_core.x_api.post_drafting import draft_posts
    from kazma_core.x_api.references import ReferenceUnreadable

    drafting["configure"](tmp_path / "missing.md")
    with pytest.raises(ReferenceUnreadable, match="missing.md"):
        await draft_posts("A post.")
    assert drafting["calls"] == 0


async def test_no_reference_drafts_from_the_brief_as_before(drafting):
    from kazma_core.x_api.post_drafting import draft_posts

    drafting["configure"]()
    result = await draft_posts("A post.")
    assert result.references == () and drafting["sources"] == [()]
    assert "reference documents" not in drafting["messages"][0]["content"]


def test_a_relative_path_is_read_under_the_active_workspace(monkeypatch, tmp_path):
    from kazma_core.x_api.references import load_references

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "brief.md").write_text("Facts.", encoding="utf-8")
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: tmp_path)
    (document,) = load_references(("docs/brief.md",))
    assert document.text == "Facts." and document.name == "brief.md"


@pytest.mark.parametrize("make, expected", [
    (lambda d: d, "not a file"),
    (lambda d: _write(d / "bin.md", b"\xff\xfe\x00bad"), "UTF-8"),
    (lambda d: _write(d / "empty.md", b"  \n"), "no text"),
    (lambda d: _write(d / "hitl_gates.db", b"SQLite format 3\x00"), "Kazma's own databases"),
])
def test_what_is_not_a_readable_text_file_is_refused(tmp_path, make, expected):
    from kazma_core.x_api.references import ReferenceUnreadable, load_references

    with pytest.raises(ReferenceUnreadable, match=expected):
        load_references((str(make(tmp_path)),))


def test_the_reference_text_is_bounded(tmp_path):
    from kazma_core.x_api.references import MAX_CHARS, ReferenceUnreadable, load_references, validate_paths

    big = _write(tmp_path / "big.md", ("x" * (MAX_CHARS + 1)).encode())
    with pytest.raises(ReferenceUnreadable, match="at most|most X drafting"):
        load_references((str(big),))
    with pytest.raises(ValueError, match="at most 3"):
        validate_paths(["a", "b", "c", "d"])
    assert validate_paths("a\n\n a \nb") == ("a", "b")


def test_source_hold_judges_a_reference_by_its_file_text_and_age(tmp_path):
    from kazma_core.x_api.evidence import source_hold
    from kazma_core.x_api.references import load_references

    path = _write(tmp_path / "ref.md", b"Kazma is self-hosted.")
    (document,) = load_references((str(path),))
    assert source_hold(document.evidence()) == ""
    assert "content hash" in source_hold({**document.evidence(), "content": "Kazma is hosted."})
    # Without its kind a reference is held like a library passage with no
    # document: the reference branch is what lets it be cited.
    assert "library" in source_hold({k: v for k, v in document.evidence().items() if k != "kind"})
    old = time.time() - 40 * 86400
    os.utime(path, (old, old))
    (stale,) = load_references((str(path),))
    assert "age" in source_hold(stale.evidence(), max_age_days=30)


def test_settings_save_the_list_warn_about_an_unreadable_file_and_refuse_too_many(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui.x_reply_api import protected_router, router

    async def no_loop():
        pass

    monkeypatch.setattr("kazma_core.x_api.mentions_fire.ensure_mentions_loop", no_loop)
    path = _write(tmp_path / "ref.md", b"Facts.")
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    with TestClient(app) as client:
        response = client.put("/api/x/reply", json={"drafting_references": [str(path), " "]}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["drafting_references"] == [str(path)]
        assert not response.json()["warnings"]
        # A client that does not send the field keeps what is saved.
        client.put("/api/x/reply", json={}, headers=headers)
        assert client.get("/api/x/reply").json()["drafting_references"] == [str(path)]
        missing = str(tmp_path / "missing.md")
        response = client.put("/api/x/reply", json={"drafting_references": [missing]}, headers=headers)
        assert response.status_code == 200 and "missing.md" in " ".join(response.json()["warnings"])
        response = client.put("/api/x/reply", json={"drafting_references": ["a", "b", "c", "d"]}, headers=headers)
        assert response.status_code == 400
    assert get_config_store().get("connectors.x.drafting.reference_files") == [missing]


def test_generate_route_reports_the_references_it_read(drafting, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.agent.artifacts import ArtifactStore
    from kazma_ui.x_api import protected_router, router

    store = ArtifactStore(tmp_path / "artifacts.db")
    monkeypatch.setattr("kazma_core.agent.artifacts.get_artifact_store", lambda: store)
    app = FastAPI()
    app.include_router(router)
    app.include_router(protected_router)
    with TestClient(app) as client:
        response = client.post("/api/x/generate", json={"brief": "Why Kazma asks first"},
                               headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200, response.text
        assert [ref["name"] for ref in response.json()["references"]] == ["what-is-kazma.md"]
        drafting["configure"](tmp_path / "missing.md")
        response = client.post("/api/x/generate", json={"brief": "Why Kazma asks first"},
                               headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 400 and "missing.md" in response.json()["error"]


WRAPPED = (
    "- **In one sentence:** Kazma is a self-hosted, open-source (MIT) AI agent\n"
    "  that asks before it acts: one assistant across the web, the terminal,\n"
    "  editors, Telegram, Discord and Slack, in English and Arabic.\n")
QUOTE = ("In one sentence: Kazma is a self-hosted, open-source (MIT) AI agent that asks "
         "before it acts: one assistant across the web, the terminal")


def _checked(evidence: str):
    from kazma_core.x_api.verification import parse_checks

    raw = json.dumps({"checks": [{"check": "target", "verdict": "pass", "reason": "Matches the reference.",
                                  "evidence": evidence, "source_ids": [], "claims": []}]})
    return parse_checks(raw, ("target",), draft="A draft.", observed="A draft.\n" + WRAPPED, sources=())


def test_a_quote_across_a_wrapped_line_of_a_reference_is_an_observed_passage():
    # Live 2026-10-26: three checks of one draft refused as "not an observed
    # passage" -- the page is wrapped Markdown, and the quote is not.
    (result,) = _checked(QUOTE)
    assert result.verdict == "pass"
    # The rule before: an exact substring, which this quote is not.
    assert QUOTE not in "A draft.\n" + WRAPPED


@pytest.mark.parametrize("evidence", ["Kazma is a hosted service", "**", "   ", "``"])
def test_other_words_or_no_words_are_still_not_an_observed_passage(evidence):
    from kazma_core.x_api.verification import _CheckSchemaError

    with pytest.raises(_CheckSchemaError, match="observed passage"):
        _checked(evidence)


def _write(path, data: bytes):
    path.write_bytes(data)
    return path
