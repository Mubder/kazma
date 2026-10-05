"""A shipped collection count must match its source inputs without running tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def test_shipped_collection_receipt_matches_current_inputs():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("receipt_generator", root / "scripts/generate_metrics.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    receipt = json.loads((root / generator.COLLECTION_RECEIPT).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 1
    assert type(receipt["collected_tests"]) is int and receipt["collected_tests"] > 0
    assert receipt["input_sha256"] == generator.collection_fingerprint(), (
        "Collection inputs changed: stage new source/test files, then run "
        "python scripts/generate_metrics.py --write --require-collected and stage its outputs."
    )
