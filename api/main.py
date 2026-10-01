"""
Lambda entry point for the OCR Auditor Agent.

Expects a JSON event body of the form:

    {
      "document_requests": [ ... per docs/ocr-auditor-agent-spec.md#input-data-contract ... ],
      "manifest": { ... full Tasktile manifest.json, including artifacts[] ... }
    }

Returns the AuditReport JSON (spec #output-data-contract). This handler
intentionally does not import predicted-conditions code and does not write
back into that repo's DynamoDB checkpoint state (spec #deployment-shape) —
it's a pure function of its own two inputs.

NOTE: this will fail at the `fetcher.fetch(...)` step against the real
`tasktile-staging` bucket until the AWS access described in spec open
question #1 is arranged. Until then, exercise the pipeline locally via
scripts/run_local.py (LocalDirOCRTextFetcher) instead of this handler.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_auditor.contracts import DocumentRequestInput  # noqa: E402
from ocr_auditor.graph import run_audit  # noqa: E402
from ocr_auditor.judge import DEFAULT_MODEL  # noqa: E402
from ocr_auditor.ocr_fetcher import S3OCRTextFetcher  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MODEL = os.environ.get("AUDITOR_MODEL", DEFAULT_MODEL)
# Set once cross-account access to tasktile-staging is arranged (see
# docs/tasktile-bucket-access-request.md) — only needed if that access takes
# the form of an assumable IAM role rather than a direct bucket policy grant.
OCR_SOURCE_BUCKET_ROLE_ARN = os.environ.get("OCR_SOURCE_BUCKET_ROLE_ARN") or None


def _error_response(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def handler(event, context=None):
    # Supports both a raw dict payload (direct Lambda invoke / local test)
    # and an API Gateway / Function URL style event with a string "body".
    body = event
    if isinstance(event, dict) and isinstance(event.get("body"), str):
        body = json.loads(event["body"])

    if not isinstance(body, dict) or "document_requests" not in body or "manifest" not in body:
        return _error_response(
            400, "request body must contain 'document_requests' and 'manifest'"
        )

    try:
        document_requests = [
            DocumentRequestInput.model_validate(dr) for dr in body["document_requests"]
        ]
    except Exception as exc:  # noqa: BLE001
        return _error_response(400, f"invalid document_requests: {exc}")

    manifest = body["manifest"]

    try:
        report = run_audit(
            document_requests=document_requests,
            manifest=manifest,
            fetcher=S3OCRTextFetcher(role_arn=OCR_SOURCE_BUCKET_ROLE_ARN),
            model=MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("audit run failed")
        return _error_response(500, f"audit run failed: {exc}")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": report.model_dump_json(indent=2),
    }
