from __future__ import annotations

import json

import pytest
from kazma_core.documents.models import (
    ArtifactId,
    BlobId,
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentId,
    DocumentIR,
    DocumentJobState,
    DocumentPage,
    DocumentResult,
    Provenance,
    VersionId,
)


def test_document_ir_has_deterministic_json_round_trip() -> None:
    document_id = DocumentId("11111111-1111-4111-8111-111111111111")
    version_id = VersionId("22222222-2222-4222-8222-222222222222")
    blob_id = BlobId("33333333-3333-4333-8333-333333333333")
    artifact_id = ArtifactId("44444444-4444-4444-8444-444444444444")
    ir = DocumentIR(
        document_id=document_id,
        version_id=version_id,
        pages=(
            DocumentPage(
                page_number=1,
                width=612,
                height=792,
                blocks=(
                    DocumentBlock(
                        block_id="b-1",
                        block_type=BlockType.PARAGRAPH,
                        text="مرحبا",
                        bounding_box=BoundingBox(1, 2, 100, 20),
                        confidence=0.99,
                        metadata={"language": "ar"},
                    ),
                ),
            ),
        ),
        provenance=Provenance(
            source="invoice.pdf",
            parser="pdf",
            parser_version="1.2.3",
            source_blob_id=blob_id,
            artifact_ids=(artifact_id,),
        ),
        metadata={"z": 2, "a": 1},
    )

    payload = ir.to_json()

    assert payload == ir.to_json()
    assert payload.startswith('{"document_id":')
    assert DocumentIR.from_json(payload) == ir
    assert DocumentIR.from_dict(json.loads(payload)).to_json() == payload


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "../escape",
        "11111111-1111-4111-8111-11111111111Z",
        "11111111-1111-4111-8111-111111111111/../../x",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper(),
    ],
)
def test_opaque_ids_reject_invalid_or_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError):
        DocumentId(value)


def test_document_result_serialization_round_trip() -> None:
    result = DocumentResult[dict[str, object]](
        ok=False,
        code="ocr_timeout",
        message="OCR timed out",
        data={"page": 4},
        document_id=DocumentId("11111111-1111-4111-8111-111111111111"),
        warnings=("partial text retained",),
        retryable=True,
    )

    restored = DocumentResult.from_json(result.to_json())

    assert restored.to_json() == result.to_json()
    assert restored.retryable is True
    assert restored.data == {"page": 4}


def test_document_job_states_match_canonical_pipeline() -> None:
    assert [state.value for state in DocumentJobState] == [
        "received",
        "quarantined",
        "validating",
        "rejected",
        "ready_to_parse",
        "parsing",
        "ocr_required",
        "ocr_running",
        "normalizing",
        "indexing",
        "verifying",
        "ready",
        "retry_wait",
        "cancelled",
        "dead_letter",
    ]


def test_model_validation_rejects_invalid_geometry_and_page_order() -> None:
    with pytest.raises(ValueError):
        BoundingBox(2, 0, 1, 1)
    with pytest.raises(ValueError):
        DocumentPage(page_number=0)
    with pytest.raises(ValueError):
        DocumentIR(
            document_id=DocumentId("11111111-1111-4111-8111-111111111111"),
            version_id=VersionId("22222222-2222-4222-8222-222222222222"),
            pages=(DocumentPage(2), DocumentPage(1)),
            provenance=Provenance(source="x", parser="test"),
        )
