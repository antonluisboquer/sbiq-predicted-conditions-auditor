#!/usr/bin/env python3
"""
Run the OCR Auditor locally against fixture files, without any AWS
credentials — uses LocalDirOCRTextFetcher instead of S3OCRTextFetcher (see
spec open question #1: real `tasktile-staging` access isn't arranged yet).

Usage:
    python scripts/run_local.py \\
        --final-output path/to/final_output.json \\
        --manifest path/to/manifest.json \\
        --ocr-dir path/to/local_ocr_texts/ \\
        [--output report.json] [--dry-run]

Runnable against the bundled example fixtures (synthetic data, not a real
Tasktile OCR export — see tests/fixtures/README.md):

    python scripts/run_local.py \\
        --final-output tests/fixtures/sample_document_requests.json \\
        --manifest tests/fixtures/sample_manifest.json \\
        --ocr-dir tests/fixtures/ocr_texts \\
        --dry-run

Drop --dry-run to actually call the LLM judge step (requires
ANTHROPIC_API_KEY to be set).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_auditor.contracts import DocumentRequestInput  # noqa: E402
from ocr_auditor.graph import run_audit  # noqa: E402
from ocr_auditor.judge import DEFAULT_MODEL  # noqa: E402
from ocr_auditor.ocr_fetcher import LocalDirOCRTextFetcher  # noqa: E402


def _load_document_requests(path: Path) -> list[DocumentRequestInput]:
    data = json.loads(path.read_text())
    raw_requests = (
        data["document_requests"]
        if isinstance(data, dict) and "document_requests" in data
        else data
    )
    return [DocumentRequestInput.model_validate(raw) for raw in raw_requests]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--final-output",
        required=True,
        type=Path,
        help="Path to a final_output.json (or a bare list of document_requests)",
    )
    parser.add_argument("--manifest", required=True, type=Path, help="Path to manifest.json")
    parser.add_argument(
        "--ocr-dir",
        required=True,
        type=Path,
        help="Directory of local '<document_id>.txt' OCR fixture files",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write report JSON here")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve + fetch only; skip the LLM judge call (no API key needed)",
    )
    args = parser.parse_args()

    document_requests = _load_document_requests(args.final_output)
    manifest = json.loads(args.manifest.read_text())
    fetcher = LocalDirOCRTextFetcher(args.ocr_dir)

    report = run_audit(
        document_requests=document_requests,
        manifest=manifest,
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
