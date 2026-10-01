import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from manifest_to_audit_requests import convert, resolve_bucket_key  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def manifest() -> dict:
    return json.loads((FIXTURES / "sample_manifest.json").read_text())


@pytest.fixture
def final_output() -> dict:
    return json.loads((FIXTURES / "sample_final_output.json").read_text())


def test_resolve_bucket_key_uses_confirmed_artifact(manifest):
    bucket, key = resolve_bucket_key("c5f7e0a2-b8ba-4533-8404-db9ba33e0b36", manifest)
    assert bucket == "tasktile-staging"
    assert key.endswith("c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt")


def test_resolve_bucket_key_raises_for_unknown_document_id(manifest):
    with pytest.raises(ValueError, match="not found in manifest.documents"):
        resolve_bucket_key("99999999-9999-9999-9999-999999999999", manifest)


def test_resolve_bucket_key_raises_when_source_key_not_derivable(manifest):
    """The synthetic Appraisal Report entry has no artifacts[] entry AND a
    source.key that doesn't match the derivable '.../documents/<id>.pdf'
    pattern -- genuinely can't resolve an OCR location."""
    with pytest.raises(ValueError, match="did not match the expected"):
        resolve_bucket_key("00000000-0000-0000-0000-000000000099", manifest)


def test_resolve_bucket_key_derives_from_source_when_no_artifact_entry():
    """UPDATE (resolves most of spec open question #2): a manifest with no
    top-level 'artifacts' key at all is NOT a dead end -- the OCR key can
    be derived from documents[].source.key. Verified against a real S3
    fetch of the exact Sahay appraisal document this scenario is modeled
    on."""
    manifest_without_artifacts = {
        "documents": [
            {
                "id": "doc-1",
                "source": {
                    "bucket": "tasktile-staging",
                    "key": "clients/c1/jobs/j1/blobs/b1/documents/doc-1.pdf",
                },
            }
        ]
    }
    bucket, key = resolve_bucket_key("doc-1", manifest_without_artifacts)
    assert bucket == "tasktile-staging"
    assert key == "clients/c1/jobs/j1/blobs/b1/ocr/doc-1.txt"


def test_convert_filters_by_document_type_and_skips_unresolvable(final_output, manifest):
    audit_requests = convert(final_output, manifest, document_type="Purchase Contract")
    assert len(audit_requests) == 1
    assert audit_requests[0]["bucket"] == "tasktile-staging"
    assert audit_requests[0]["key"].endswith("c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt")
    assert audit_requests[0]["document_type"] == "Purchase Contract"
    assert audit_requests[0]["specifications_unsatisfied"] == [
        "A copy of the fully executed purchase contract is required.",
        "All attachments to the purchase contract are required.",
    ]


def test_convert_without_document_type_filter_skips_unresolvable_and_empty_ids(
    final_output, manifest
):
    """Appraisal Report (unresolvable), Credit Report (unknown document_id),
    and Rental Agreement (no document_ids at all) should all be silently
    skipped -- only resolvable requests make it into the output."""
    audit_requests = convert(final_output, manifest, document_type=None)
    types = {req["document_type"] for req in audit_requests}
    assert types == {"Purchase Contract", "EMD Docs"}
