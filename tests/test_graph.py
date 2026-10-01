import json
from pathlib import Path

import pytest

from ocr_auditor.contracts import AuditRequest
from ocr_auditor.graph import run_audit
from ocr_auditor.ocr_fetcher import LocalDirOCRTextFetcher

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def audit_requests() -> list[AuditRequest]:
    data = json.loads((FIXTURES / "sample_audit_requests.json").read_text())
    return [AuditRequest.model_validate(raw) for raw in data["audit_requests"]]


def test_dry_run_end_to_end_fetches_and_skips_llm(audit_requests):
    """dry_run=True exercises fetch -> report without calling any LLM, so
    this test needs no API key and no network access."""
    fetcher = LocalDirOCRTextFetcher(FIXTURES / "ocr_texts")

    report = run_audit(audit_requests=audit_requests, fetcher=fetcher, dry_run=True)

    # Purchase Contract (fetchable) + EMD Docs (fetchable) + Appraisal
    # Report (key has no matching local fixture -> fetch fails).
    assert len(report.document_results) == 3

    by_type = {r.document_type: r for r in report.document_results}

    purchase = by_type["Purchase Contract"]
    assert purchase.fetched is True
    assert purchase.error is None
    assert len(purchase.verdicts) == 4
    # dry_run never calls the LLM, so every spec fails safe to unsatisfied
    # at zero confidence (never claims "satisfied" for something unchecked).
    assert all(v.verdict == "unsatisfied" for v in purchase.verdicts)
    assert all(v.confidence == 0.0 for v in purchase.verdicts)

    appraisal = by_type["Appraisal Report"]
    assert appraisal.fetched is False
    assert "no local OCR fixture" in appraisal.error
    assert len(appraisal.verdicts) == 2
    assert all(v.verdict == "unsatisfied" for v in appraisal.verdicts)

    assert report.summary["total_documents_audited"] == 3
    assert report.summary["documents_fetched"] == 2
    assert report.summary["documents_fetch_failed"] == 1
    assert report.summary["verdict_counts"] == {"satisfied": 0, "unsatisfied": 8}


def test_every_specification_is_persisted_in_output_regardless_of_verdict(audit_requests):
    """Nothing gets filtered out of the output -- every requested spec gets
    exactly one verdict row, whatever that verdict turns out to be."""
    fetcher = LocalDirOCRTextFetcher(FIXTURES / "ocr_texts")
    report = run_audit(audit_requests=audit_requests, fetcher=fetcher, dry_run=True)

    by_type = {r.document_type: r for r in report.document_results}
    emd = by_type["EMD Docs"]
    assert [v.specification for v in emd.verdicts] == [
        "Earnest money deposit must be documented with a copy of the cancelled check or wire transfer confirmation.",
        "Earnest money deposit amount must match the amount stated in the purchase contract.",
    ]


def test_borrower_tag_is_passed_through_unchanged():
    """`borrower` is a pure pass-through -- the auditor never reads or
    branches on it, just echoes it back onto the matching
    DocumentAuditResult so the caller can tell which borrower's copy of a
    document a given result row belongs to."""
    fetcher = LocalDirOCRTextFetcher(FIXTURES / "ocr_texts")
    requests = [
        AuditRequest(
            bucket="tasktile-staging",
            key="clients/x/jobs/y/blobs/z/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt",
            document_type="Purchase Contract",
            specifications_unsatisfied=["spec A"],
            borrower="primary",
        ),
        AuditRequest(
            bucket="tasktile-staging",
            key="clients/x/jobs/y/blobs/z/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt",
            document_type="Purchase Contract",
            specifications_unsatisfied=["spec A"],
            borrower="coborrower",
        ),
        AuditRequest(
            bucket="tasktile-staging",
            key="clients/x/jobs/y/blobs/z/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt",
            document_type="Purchase Contract",
            specifications_unsatisfied=["spec A"],
            # no borrower set at all -- must stay None, not error/default to something else.
        ),
    ]

    report = run_audit(audit_requests=requests, fetcher=fetcher, dry_run=True)

    assert [r.borrower for r in report.document_results] == ["primary", "coborrower", None]
