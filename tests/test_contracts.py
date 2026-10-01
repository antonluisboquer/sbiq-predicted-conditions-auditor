from ocr_auditor.contracts import (
    DocumentRequestInput,
    ManifestArtifact,
    ManifestDocument,
)


def test_document_request_input_defaults():
    req = DocumentRequestInput(document_type="Purchase Contract")
    assert req.document_ids == []
    assert req.specifications == []
    assert req.document_category == ""


def test_manifest_document_from_raw():
    raw = {
        "id": "abc-123",
        "category": {"category_id": 200, "category_name": "Purchase Contract"},
    }
    doc = ManifestDocument.from_raw(raw)
    assert doc.id == "abc-123"
    assert doc.category_id == 200
    assert doc.category_name == "Purchase Contract"


def test_manifest_artifact_from_raw_accepts_ocr_type():
    raw = {
        "type": "ocr",
        "source": {"bucket": "tasktile-staging", "key": "a/b/c/doc-1.txt"},
        "document_id": "doc-1",
    }
    artifact = ManifestArtifact.from_raw(raw)
    assert artifact is not None
    assert artifact.bucket == "tasktile-staging"
    assert artifact.key == "a/b/c/doc-1.txt"
    assert artifact.document_id == "doc-1"


def test_manifest_artifact_from_raw_rejects_non_ocr_type():
    raw = {
        "type": "blob",
        "source": {"bucket": "tasktile-staging", "key": "a/b/c/doc-1.pdf"},
        "document_id": "doc-1",
    }
    assert ManifestArtifact.from_raw(raw) is None


def test_manifest_artifact_from_raw_rejects_incomplete_entry():
    raw = {"type": "ocr", "source": {}, "document_id": "doc-1"}
    assert ManifestArtifact.from_raw(raw) is None
