"""The guard runs the REAL document broker, against a real Docker engine.

Opt-in, like the broker's own qualification suite: set
``KAZMA_TEST_DOCUMENT_WORKER_IMAGE`` to a built worker image
(``python scripts/document_worker_image.py build`` prints its tag). Without
it, or without a docker CLI, every test here is skipped -- a skip is not
evidence.

The real guard supervises a fake Kazma server (``fixtures/fake_kazma.py``)
and the real ``worker_broker.py``; the test then parses a real PDF through
the container the way Kazma's server does (the address and token file the
guard hands it), kills the broker, sees the guard bring it back, and parses
again. Every container the broker started is gone at the end.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.docker_cli import find_docker_cli

from tests.test_guard_document_broker import _BrokerRun, guard
from tests.test_guard_integration import _free_port

IMAGE = (os.environ.get("KAZMA_TEST_DOCUMENT_WORKER_IMAGE") or "").strip()
DOCKER = find_docker_cli() if IMAGE else None
pytestmark = [
    pytest.mark.skipif(
        not (IMAGE and DOCKER),
        reason="set KAZMA_TEST_DOCUMENT_WORKER_IMAGE (and have a docker CLI) to run the real broker",
    ),
    pytest.mark.timeout(1200),
]


def _labelled(name: str) -> list[str]:
    from kazma_core.documents.worker_broker import LABEL

    result = subprocess.run(
        [DOCKER, "ps", "-a", "-q", "--filter", f"label={LABEL}={name}"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.split()


def _pdf(path: Path) -> Path:
    from reportlab.pdfgen import canvas

    page = canvas.Canvas(str(path))
    page.drawString(72, 720, "Guard broker check: invoice 2026-10-15 total 4,250.75")
    page.showPage()
    page.save()
    return path


def _parse_through_the_broker(tmp_path: Path, record: dict, monkeypatch, tag: str) -> tuple[str, float]:
    """What Kazma's server does with what the guard handed it."""
    from kazma_core.documents import worker_container
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.service import DocumentService

    monkeypatch.setenv("KAZMA_DOCUMENT_WORKER_ISOLATION", "container")
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_URL", record["url"])
    monkeypatch.setenv("KAZMA_DOCUMENT_BROKER_TOKEN_FILE", record["token_file"])
    monkeypatch.setattr(worker_container, "_status_cache", None)
    report = worker_container.isolation_report()
    assert report["ready"] is True and report["broker"]["attested"] is True, report
    service = DocumentService(config=replace(
        DocumentConfig(storage_root=tmp_path / f"store-{tag}"), ocr_enabled=False,
        security_local_salvage=False))
    started = time.monotonic()
    ir = asyncio.run(service.parse_path(_pdf(tmp_path / f"{tag}.pdf"), tenant_id="t",
                                        workspace_id="w", actor_id="a"))
    took = time.monotonic() - started
    text = " ".join(block.text for page in ir.pages for block in page.blocks)
    return text, took


def test_the_guard_keeps_the_real_broker_running_and_documents_parse(tmp_path, monkeypatch):
    port = _free_port()
    run = _BrokerRun(tmp_path, port, args=["--once"], START_TIMEOUT="120")
    # The real broker and the image under test, not the fake. When the image
    # is the one built for this checkout, the guard finds it by itself (the
    # default path: the tag from the build inputs); otherwise it is pinned.
    from kazma_core.documents.worker_image import expected_tag

    run.env.pop("KAZMA_GUARD_BROKER_SCRIPT")
    if IMAGE == expected_tag(Path(guard.REPO_ROOT)):
        run.env.pop("KAZMA_GUARD_BROKER_IMAGE")
    else:
        run.env["KAZMA_GUARD_BROKER_IMAGE"] = IMAGE
    name = guard._broker_name()
    measured: dict[str, float] = {}
    with run as g:
        began = time.monotonic()
        g.wait_for("broker.ready", timeout=600)
        measured["first_ready_s"] = round(time.monotonic() - began, 1)
        record = g.state()["document_broker"]
        assert record["state"] == "running" and record["image"] == IMAGE
        text, took = _parse_through_the_broker(tmp_path, record, monkeypatch, "first")
        assert "invoice 2026-10-15" in text and "4,250.75" in text, text
        measured["first_parse_s"] = round(took, 2)

        # Kill the broker the way a crash would: the guard brings it back.
        first_pid = record["pid"]
        killed = time.monotonic()
        subprocess.run(["taskkill", "/PID", str(first_pid), "/T", "/F"] if os.name == "nt"
                       else ["kill", "-9", str(first_pid)], capture_output=True, check=False)
        g.wait_for("broker.exited", timeout=60)
        g.wait_count("broker.ready", 2, timeout=600)
        measured["restart_to_ready_s"] = round(time.monotonic() - killed, 1)
        record = g.state()["document_broker"]
        assert record["pid"] and record["pid"] != first_pid
        assert g.count("child.spawned") == 1, "a broker crash must not restart Kazma"
        text, took = _parse_through_the_broker(tmp_path, record, monkeypatch, "second")
        assert "invoice 2026-10-15" in text
        measured["second_parse_s"] = round(took, 2)

        # End: the server exits, the guard (--once) stops the broker gracefully.
        child = g.state()["child_pid"]
        subprocess.run(["taskkill", "/PID", str(child), "/T", "/F"] if os.name == "nt"
                       else ["kill", "-9", str(child)], capture_output=True, check=False)
        assert g.proc is not None
        g.proc.wait(timeout=180)
        assert "broker.stopped" in g.event_names()
    deadline = time.monotonic() + 60
    while _labelled(name) and time.monotonic() < deadline:
        time.sleep(1)
    assert _labelled(name) == [], "the broker left job containers behind"
    print("\nMEASURED " + json.dumps(measured))


def test_a_broker_asked_for_an_unbuilt_image_serves_the_newest_build():
    """The fallback against the real engine: an image tag nobody built, and
    the newest image the build script made serves, said in the status."""
    import uuid

    from kazma_core.documents.worker_broker import BrokerSettings, _Broker

    broker = _Broker(BrokerSettings(
        docker=DOCKER, image="kazma-document-worker:src-" + uuid.uuid4().hex[:16],
        token="t" * 40, name="fallback" + uuid.uuid4().hex[:6], fallback_to_newest_built=True))
    broker.check_engine()
    try:
        broker.resolve_image()
    except RuntimeError:
        pytest.skip("no image built by scripts/document_worker_image.py on this engine")
    status = broker.status()
    assert status["image_fallback"] is True and status["image_ref"].startswith("kazma-document-worker:")
    assert status["image_source"], status
    assert broker.requested_image_present() is False
