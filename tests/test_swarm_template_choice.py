"""An ``auto`` task goes to the template whose words it uses most (2026-11-15).

Live on 14 November a research task sent to the swarm as ``auto`` -- "Find
authoritative, current facts from the official websites and report them ...
Latest stable Python 3 release on python.org" -- was given the coder template.
The autoscaler took the FIRST template in file order with ANY of its words,
and the coder (first in the file) has "python"; the researcher's "report",
"find", "facts", "official", "latest" never counted. The model classifier made
the same mistake: "python" alone read as coding work.

Now ``AutoScaler.maybe_scale`` ranks templates by how many of their words the
prompt uses (file order breaks a tie, then the catch-all), and a language's
name is context for the classifier, as JavaScript's already was. Every case
runs the real engine and autoscaler on the shipped templates; the yes/no
first-match rule is the negative control.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from kazma_core.models.router import TaskProfile, classify_prompt
from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate
from kazma_core.swarm.config import SwarmConfig
from kazma_core.swarm.engine import SwarmEngine

SHIPPED = Path(__file__).resolve().parents[1] / "swarm_templates.json"

#: The live task's prompt, as the tool worker logged it.
LIVE_RESEARCH = (
    "Find authoritative, current facts from the official websites and report them with the exact page URL "
    "each came from. Do not guess from memory; actually read the pages.\n\n"
    "1. Latest stable Python 3 release on python.org: the version number and its release date."
)


@pytest.fixture(autouse=True)
def _inert(monkeypatch):
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")


def _template_for(tmp_path: Path, prompt: str) -> str:
    """The template the real autoscaler spawns from, on a copy of the shipped file."""
    copy = tmp_path / "swarm_templates.json"
    shutil.copyfile(SHIPPED, copy)
    scaler = AutoScaler(SwarmEngine(SwarmConfig(enabled=True, workers=[])), templates_path=copy)
    scaler.load_templates()
    assert {"coder", "researcher", "generalist"} <= set(scaler._templates)
    detail: dict = {}
    worker = scaler.maybe_scale(prompt, detail=detail)
    assert worker is not None
    return str(detail.get("template") or worker.name.split("-pool-")[0])


def test_the_live_research_task_goes_to_the_researcher(tmp_path) -> None:
    assert _template_for(tmp_path, LIVE_RESEARCH) == "researcher"


def test_control_the_first_match_rule_gave_it_to_the_coder(tmp_path, monkeypatch) -> None:
    """The rule before: any word at all, first template in file order."""
    real = WorkerTemplate.match_score
    monkeypatch.setattr(WorkerTemplate, "match_score",
                        lambda self, prompt, required=None: int(real(self, prompt, required) > 0))
    assert _template_for(tmp_path, LIVE_RESEARCH) == "coder"


@pytest.mark.parametrize("prompt", [
    "Refactor this Python function and debug the failing class",
    "Implement a TypeScript function that parses dates",
])
def test_coding_work_still_goes_to_the_coder(tmp_path, prompt) -> None:
    assert _template_for(tmp_path, prompt) == "coder"


def test_a_tie_keeps_file_order(tmp_path) -> None:
    # One coder word ("debug") and one researcher word ("report"): the
    # specialist first in the file, as before.
    assert _template_for(tmp_path, "debug it and report back") == "coder"


def test_a_task_using_no_template_words_goes_to_the_catch_all(tmp_path) -> None:
    assert _template_for(tmp_path, "what will the weather be tomorrow") == "generalist"


def test_match_score_counts_each_word_once() -> None:
    template = WorkerTemplate.from_dict({"name": "t", "capabilities": {"expertise": ["report", "Report", "find"]}})
    assert template.match_score("report report and find") == 2
    assert template.matches_task("please report") and not template.matches_task("barcode")


def test_a_language_name_alone_is_not_coding_work() -> None:
    assert classify_prompt(LIVE_RESEARCH) != TaskProfile.CODING
    assert classify_prompt("What is new in the latest Python release?") != TaskProfile.CODING
    # With code work beside it, it is.
    assert classify_prompt("write a Python function") == TaskProfile.CODING
    assert classify_prompt("write a python script to rename files") == TaskProfile.CODING
    assert classify_prompt("fix this python traceback") == TaskProfile.CODING
