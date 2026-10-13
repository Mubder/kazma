"""A document intake on ANOTHER HOST, held after one step (the container lease drill).

Run inside a container by ``tests/test_document_intake_lease_containers.py``;
standard library and kazma_core only (no pytest in the image). Environment:

``DRILL_DIR``      shared folder: the storage root, ``paused`` (written here
                   when the step is done) and ``go`` (written by the drill).
``TENANT``         the drill's tenant.
``LEASE_SECONDS``  this owner's intake lease.
``PAUSE_AFTER``    repository step to hold after (default ``create_version``).
``BLIND``          ``1``: renewals report success and write nothing (the
                   renewal thread never notices a takeover -- the race window
                   the database fence alone must cover).
``NO_FENCE``       ``1``: publish without the fence (the negative control).

Exit codes: 0 published, 7 fenced (``intake_taken_over``), 8 another
refusal, 9 the drill never released the hold.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path


def main() -> None:
    drill = Path(os.environ["DRILL_DIR"])
    tenant = os.environ["TENANT"]
    from kazma_core.config_store import get_config_store

    get_config_store().set("documents.enabled", True, category="documents")
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.ingestion import DocumentIngestionError, DocumentIngestionService

    svc = DocumentIngestionService(config=DocumentConfig(
        storage_root=drill / "content", security_malware_scan="off", security_local_salvage=False,
        ocr_enabled=False, intake_lease_seconds=int(os.environ.get("LEASE_SECONDS", "8")),
    ))
    step = os.environ.get("PAUSE_AFTER", "create_version")
    real_step = getattr(svc.repository, step)

    def held(*args, **kwargs):
        result = real_step(*args, **kwargs)
        (drill / "paused").write_text("paused", encoding="utf-8")
        deadline = time.monotonic() + 900
        while not (drill / "go").exists():
            if time.monotonic() > deadline:
                os._exit(9)
            time.sleep(0.1)
        return result

    setattr(svc.repository, step, held)
    if os.environ.get("BLIND") == "1":
        svc.admissions.heartbeat = lambda token: True
    if os.environ.get("NO_FENCE") == "1":
        real_enqueue = svc.jobs.enqueue

        def enqueue(**kwargs):
            kwargs.pop("intake_fence", None)
            return real_enqueue(**kwargs)

        svc.jobs.enqueue = enqueue
    try:
        svc.ingest_stream(io.BytesIO(b"zombie intake " + tenant.encode()), filename="note.txt",
                          tenant_id=tenant, actor_id="alice", workspace_id="w", idempotency_key="zombie")
    except DocumentIngestionError as exc:
        print("refused", exc.code, flush=True)
        os._exit(7 if exc.code == "intake_taken_over" else 8)
    print("published", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
