import json
from pathlib import Path

import pytest

from ocr_auditor.contracts import DocumentRequestInput
from ocr_auditor.graph import run_audit
from ocr_auditor.ocr_fetcher import LocalDirOCRTextFetcher

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def document_requests() -> list[DocumentRequestInput]:
    data = json.loads((FIXTURES / "sample_document_requests.json").read_text())
    return [DocumentRequestInput.model_validate(raw) for raw in data["document_requests"]]


@pytest.fixture
def manifest() -> dict:
    return json.loads((FIXTURES / "sample_manifest.json").read_text())


def test_dry_run_end_to_end_resolves_and_skips_llm(document_requests, manifest):
    """dry_run=True exercises resolve -> fetch -> report without calling any
    LLM, so this test needs no API key and no network access."""
    fetcher = LocalDirOCRTextFetcher(FIXTURES / "ocr_texts")

    report = run_audit(
        document_requests=document_requests,
        manifest=manifest,
        fetcher=fetcher,
        dry_run=True,
    )

    # 5 document_requests -> Purchase Contract (1 doc) + EMD Docs (1 doc)
    # + Appraisal Report (1 doc, unresolved) + Credit Report (1 doc,
    # unresolved) + Rental Agreement (0 document_ids -> 1 placeholder).
    assert len(report.document_results) == 5

    by_type = {r.document_request_document_type: r for r in report.document_results}

    purchase = by_type["Purchase Contract"]
    assert purchase.resolved is True
    assert len(purchase.verdicts) == 4
    assert all(v.verdict == "needs_human_review" for v in purchase.verdicts)
    assert all("dry-run" in v.reasoning for v in purchase.verdicts)

    appraisal = by_type["Appraisal Report"]
    assert appraisal.resolved is False
    assert "no OCR artifact" in appraisal.resolution_note

    credit = by_type["Credit Report"]
    assert credit.resolved is False
    assert "not found in manifest.documents" in credit.resolution_note

    rental = by_type["Rental Agreement"]
    assert rental.resolved is False
    assert rental.document_ids == []
    assert "no document_ids" in rental.resolution_note

    assert report.summary["total_documents_audited"] == 5
    assert report.summary["documents_resolved"] == 2
    assert report.summary["documents_unresolved"] == 3
