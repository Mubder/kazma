"""Exercise a real bundle restore and the legacy scheduler rollback fence."""

from __future__ import annotations

import hashlib
import time
import zipfile


def test_bundle_restore_preserves_receipts_and_never_releases_old_work(tmp_path, monkeypatch):
    from kazma_core.config_store import get_config_store
    from kazma_core.migration import importer
    from kazma_core.migration.bundle import BUNDLE_VERSION, Manifest
    from kazma_core.x_api import publication_store
    from kazma_core.x_api.schedule import reset_x_scheduled_store

    from scripts.x_shadow import _copy_database

    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    monkeypatch.setattr(publication_store, "_test_store", None)
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: source)
    store = publication_store.get_publication_store()
    legacy = reset_x_scheduled_store(source / "x_scheduled.db")
    base = dict(tenant_id="default", account_id="123", credential_revision="captured",
                max_day=20, max_month=100, duplicate_days=30, reply_to_id="", due_at=time.time(), origin="schedule")
    published = store.reserve(**base, text="Confirmed before restore", idempotency_key="done")
    store.claim(published["id"], owner="confirmed", tenant_id="default", account_id="123", credential_revision="captured")
    store.confirm(published["id"], owner="confirmed", tweet_id="901")
    uncertain = store.reserve(**base, text="Interrupted before restore", idempotency_key="unknown")
    store.claim(uncertain["id"], owner="interrupted", tenant_id="default", account_id="123", credential_revision="captured")
    queued = store.reserve(**base, text="Queued before restore", idempotency_key="queued")
    legacy.project_operation(queued)
    # This is the query made by an old pending-only fire loop. A managed
    # projection is fenced even before the new build's restore pause exists.
    assert not legacy.list_due()
    files = {"config.yaml": b"{}\n", "meta.env": b"", "pathmap.json": b"{}"}
    for name in ("x_publications.db", "x_scheduled.db"):
        snapshot = tmp_path / name
        _copy_database(source / name, snapshot)
        files["data/" + name] = snapshot.read_bytes()
    manifest = Manifest(bundle_version=BUNDLE_VERSION,
                        file_hashes={name: hashlib.sha256(content).hexdigest() for name, content in files.items()})
    archive = tmp_path / "restore.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            bundle.writestr(name, content)
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: target)
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    reset_x_scheduled_store(target / "x_scheduled.db")
    began = time.monotonic()
    report = importer.import_bundle(archive, target_workspace_root=str(tmp_path))
    elapsed = time.monotonic() - began
    assert report.ok, report.errors
    restored = publication_store.get_publication_store()
    assert restored.get(published["id"], tenant_id="default")["tweet_id"] == "901"
    assert restored.get(published["id"], tenant_id="default")["state"] == "published"
    assert restored.get(uncertain["id"], tenant_id="default")["state"] == "outcome_unknown"
    assert restored.get(queued["id"], tenant_id="default")["state"] == "awaiting_approval"
    assert get_config_store().get("system.x.restore_paused") is True
    assert not restored.due()
    assert not reset_x_scheduled_store(target / "x_scheduled.db").list_due()
    assert elapsed < 30, "A small isolated fixture restore should be bounded; this is not a production RTO claim."
