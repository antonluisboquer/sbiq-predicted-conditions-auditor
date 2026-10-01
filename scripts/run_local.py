#!/usr/bin/env python3
"""
Run the OCR Auditor against an `audit_requests.json` file (the simplified
`bucket` + `key` + `document_type` + `specifications_unsatisfied` shape --
see `ocr_auditor/contracts.py`).

Two fetch modes:
  - `--ocr-dir PATH`: LocalDirOCRTextFetcher, reads local '<document_id>.txt'
    fixture files (keyed by the last path segment of each request's `key`).
    No AWS needed.
  - (default, no `--ocr-dir`): S3OCRTextFetcher.from_env(), reads real OCR
    artifacts from S3 using the S3_ACCESS_KEY/S3_SECRET_KEY credentials in
    .env. See docs/tasktile-bucket-access-request.md.

Usage:
    python scripts/run_local.py \\
        --audit-requests path/to/audit_requests.json \\
        [--ocr-dir path/to/local_ocr_texts/] [--output report.json] [--dry-run]

Runnable against the bundled synthetic fixtures (no AWS/API key needed):

    python scripts/run_local.py \\
        --audit-requests tests/fixtures/sample_audit_requests.json \\
        --ocr-dir tests/fixtures/ocr_texts \\
        --dry-run

Or against a real loan sample + real S3 (requires .env's S3_ACCESS_KEY/
S3_SECRET_KEY and ANTHROPIC_API_KEY). If you only have the older
final_output.json + manifest.json shape, convert it first:

    python scripts/manifest_to_audit_requests.py \\
        --final-output .../compiled_inputs/sahay/final_output.json \\
        --manifest .../compiled_inputs/sahay/manifest.json \\
        --document-type "Appraisal Report" \\
        --output /tmp/sahay_audit_requests.json

    python scripts/run_local.py --audit-requests /tmp/sahay_audit_requests.json

Drop --dry-run to actually call the LLM judge step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_auditor.contracts import AuditRequest  # noqa: E402
from ocr_auditor.graph import run_audit  # noqa: E402
from ocr_auditor.judge import DEFAULT_MODEL  # noqa: E402
from ocr_auditor.ocr_fetcher import LocalDirOCRTextFetcher, S3OCRTextFetcher  # noqa: E402


def _load_audit_requests(path: Path) -> list[AuditRequest]:
    data = json.loads(path.read_text())
    raw_requests = (
        data["audit_requests"] if isinstance(data, dict) and "audit_requests" in data else data
    )
    return [AuditRequest.model_validate(raw) for raw in raw_requests]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--audit-requests",
        required=True,
        type=Path,
        help="Path to an audit_requests.json (or a bare list of AuditRequest objects)",
    )
    parser.add_argument(
        "--ocr-dir",
        type=Path,
        default=None,
        help=(
            "Directory of local '<document_id>.txt' OCR fixture files. "
            "If omitted, fetches real OCR text from S3 using "
            "S3_ACCESS_KEY/S3_SECRET_KEY from .env."
        ),
    )
    parser.add_argument("--output", type=Path, default=None, help="Write report JSON here")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch only; skip the LLM judge call (no API key needed)",
    )
    args = parser.parse_args()

    audit_requests = _load_audit_requests(args.audit_requests)
    if not audit_requests:
        print(f"No audit_requests found in {args.audit_requests}", file=sys.stderr)
        sys.exit(1)

    fetcher = (
        LocalDirOCRTextFetcher(args.ocr_dir) if args.ocr_dir else S3OCRTextFetcher.from_env()
    )

    report = run_audit(
        audit_requests=audit_requests,
        fetcher=fetcher,
        model=args.model,
        dry_run=args.dry_run,
    )

    out = report.model_dump_json(indent=2)
    if args.output:
        args.output.write_text(out)
        print(f"Wrote report to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
