"""
Lambda entry point for the OCR Auditor Agent.

Expects a JSON event body of the form:

    {
      "audit_requests": [
        {
          "bucket": "tasktile-staging",
          "key": "clients/.../ocr/<document_id>.txt",
          "document_type": "Appraisal Report",
          "specifications_unsatisfied": ["spec 1", "spec 2", ...],
          "borrower": "primary"  # optional; "primary" | "coborrower" | omitted.
                                  # Pure pass-through -- see contracts.py's
                                  # Borrower docstring -- not read or acted on
                                  # here, just echoed onto the matching
                                  # DocumentAuditResult.
        },
        ...
      ]
    }

Returns the AuditReport JSON. This handler intentionally does not import
predicted-conditions code and does not write back into that repo's
DynamoDB checkpoint state (spec #deployment-shape) -- it's a pure function
of its own input. The caller is responsible for knowing each document's
exact OCR artifact `bucket`/`key` (predicted-conditions has its own
manifest access) -- this service does no manifest resolution itself. See
`ocr_auditor/contracts.py`'s module docstring.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_auditor.contracts import AuditRequest  # noqa: E402
from ocr_auditor.graph import run_audit  # noqa: E402
from ocr_auditor.judge import DEFAULT_MODEL  # noqa: E402
from ocr_auditor.ocr_fetcher import S3OCRTextFetcher  # noqa: E402
from ocr_auditor.secrets import load_secrets  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cold-start secrets hydration (no-op locally where AGENT_SECRETS_ARN isn't
# set -- .env keeps covering ANTHROPIC_API_KEY / TASK_TILE_S3_* there). Must
# run before MODEL/fetcher construction below depend on those env vars.
load_secrets()

MODEL = os.environ.get("AUDITOR_MODEL", DEFAULT_MODEL)
# Set only if cross-account access to the OCR source bucket takes the form
# of an assumable IAM role rather than a direct bucket policy grant.
OCR_SOURCE_BUCKET_ROLE_ARN = os.environ.get("OCR_SOURCE_BUCKET_ROLE_ARN") or None


def _error_response(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def _check_api_key(event: dict) -> str | None:
    """Validate the `x-api-key` header for Function URL / HTTP-shaped
    invocations, mirroring predicted-conditions' `api/main.py` auth
    middleware. The Function URL is deployed with
    FunctionUrlAuthType.NONE (see infra/stacks/ocr_auditor_stack.py) so
    this app-level check is the only thing gating public access to it.

    Returns an error message if the request should be rejected, or None
    if it's OK to proceed. Direct raw-dict Lambda invokes (no "headers"
    key at all -- used by scripts/run_local.py-style callers and the
    deploy smoke test) skip this check entirely, since that path is
    already gated by AWS IAM's own `lambda:InvokeFunction` permission.
    """
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None

    expected_key = os.environ.get("API_KEY", "").strip()
    if not expected_key:
        # No API_KEY configured at all -- fail closed rather than silently
        # leaving the public Function URL wide open.
        logger.error("API_KEY is not set; rejecting Function URL request")
        return "server is not configured with an API key"

    provided = headers.get("x-api-key") or headers.get("X-Api-Key") or ""
    if provided != expected_key:
        return "invalid API key"
    return None


def handler(event, context=None):
    if isinstance(event, dict):
        auth_error = _check_api_key(event)
        if auth_error:
            return _error_response(401, auth_error)

    # Supports both a raw dict payload (direct Lambda invoke / local test)
    # and an API Gateway / Function URL style event with a string "body".
    body = event
    if isinstance(event, dict) and isinstance(event.get("body"), str):
        body = json.loads(event["body"])

    if not isinstance(body, dict) or "audit_requests" not in body:
        return _error_response(400, "request body must contain 'audit_requests'")

    try:
        audit_requests = [
            AuditRequest.model_validate(req) for req in body["audit_requests"]
        ]
    except Exception as exc:  # noqa: BLE001
        return _error_response(400, f"invalid audit_requests: {exc}")

    try:
        if OCR_SOURCE_BUCKET_ROLE_ARN:
            fetcher = S3OCRTextFetcher(role_arn=OCR_SOURCE_BUCKET_ROLE_ARN)
        else:
            fetcher = S3OCRTextFetcher.from_env()

        report = run_audit(
            audit_requests=audit_requests,
            fetcher=fetcher,
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
