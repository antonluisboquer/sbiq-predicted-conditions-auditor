import json
from pathlib import Path

import pytest

from ocr_auditor.contracts import DocumentRequestInput
from ocr_auditor.manifest_resolver import resolve_document_request

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def manifest() -> dict:
    return json.loads((FIXTURES / "sample_manifest.json").read_text())


def test_resolves_document_with_ocr_artifact(manifest):
    request = DocumentRequestInput(
        document_type="Purchase Contract",
        document_ids=["c5f7e0a2-b8ba-4533-8404-db9ba33e0b36"],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest)
    assert result.any_resolved
    [resolved] = result.resolved_documents
    assert resolved.resolved is True
    assert resolved.bucket == "tasktile-staging"
    assert resolved.key.endswith("c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt")
    assert resolved.category_mismatch_note == ""


def test_flags_category_mismatch_without_blocking_resolution(manifest):
    request = DocumentRequestInput(
        document_type="Something Else Entirely",
        document_ids=["c5f7e0a2-b8ba-4533-8404-db9ba33e0b36"],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest)
    [resolved] = result.resolved_documents
    assert resolved.resolved is True  # mismatch doesn't block resolution
    assert "does not match" in resolved.category_mismatch_note


def test_document_found_but_no_ocr_artifact(manifest):
    request = DocumentRequestInput(
        document_type="Appraisal Report",
        document_ids=["00000000-0000-0000-0000-000000000099"],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest)
    [resolved] = result.resolved_documents
    assert resolved.resolved is False
    assert "no OCR artifact" in resolved.reason


def test_document_id_not_in_manifest_at_all(manifest):
    request = DocumentRequestInput(
        document_type="Credit Report",
        document_ids=["99999999-9999-9999-9999-999999999999"],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest)
    [resolved] = result.resolved_documents
    assert resolved.resolved is False
    assert "not found in manifest.documents" in resolved.reason


def test_manifest_with_no_artifacts_key_at_all():
    """Spec open question #2 — some real manifests only have 'documents',
    no 'artifacts' key at all."""
    manifest_without_artifacts = {
        "documents": [
            {
                "id": "doc-1",
                "category": {"category_id": 162, "category_name": "Appraisal Report"},
            }
        ]
    }
    request = DocumentRequestInput(
        document_type="Appraisal Report",
        document_ids=["doc-1"],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest_without_artifacts)
    [resolved] = result.resolved_documents
    assert resolved.resolved is False
    assert "documents-only" in resolved.reason


def test_empty_document_ids_yields_no_resolved_documents(manifest):
    request = DocumentRequestInput(
        document_type="Rental Agreement",
        document_ids=[],
        specifications=["spec 1"],
    )
    result = resolve_document_request(request, manifest)
    assert result.resolved_documents == []
