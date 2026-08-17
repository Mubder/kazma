"""Work slashes rewrite into graph turns; control slashes stay instant."""

from __future__ import annotations

from kazma_core.agent.slash_turns import is_control_slash, rewrite_work_slash


def test_research_topic_rewrites():
    out = rewrite_work_slash("/research deep climate change in Kuwait")
    assert out is not None
    assert "climate change in Kuwait" in out
    assert "research" in out.lower()


def test_research_bare_is_control():
    assert rewrite_work_slash("/research") is None
    assert rewrite_work_slash("/research status") is None
    assert is_control_slash("/research")
    assert is_control_slash("/research help")


def test_swarm_task_rewrites_status_does_not():
    assert rewrite_work_slash("/swarm status") is None
    assert rewrite_work_slash("/swarm list") is None
    assert is_control_slash("/swarm status")
    out = rewrite_work_slash("/swarm analyze competitor pricing")
    assert out is not None
    assert "competitor pricing" in out
    assert "Dispatch" in out


def test_bare_swarm_mention_rewrites():
    out = rewrite_work_slash("use the swarm to review this PR")
    assert out is not None
    assert "review this PR" in out
    assert rewrite_work_slash("I saw a swarm of bees") is None


def test_kb_and_skill_work_vs_list():
    assert rewrite_work_slash("/kb list") is None
    assert is_control_slash("/kb list")
    assert rewrite_work_slash("/kb crawl docs https://example.com") is not None
    assert rewrite_work_slash("/skill list") is None
    assert rewrite_work_slash("/skill install owner/repo") is not None
