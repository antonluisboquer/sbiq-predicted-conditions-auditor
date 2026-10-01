#!/usr/bin/env python3
"""
Dev/test-only helper: convert an existing `final_output.json` +
`manifest.json` pair (predicted-conditions' own data shapes) into the
auditor's simplified `AuditRequest` JSON shape (`bucket` + `key` +
`document_type` + `specifications_unsatisfied`).

This exists purely for local testing convenience -- so cached/real
fixtures that still use the old manifest-based shape don't have to be
hand-converted every time. The core engine (`ocr_auditor/`) does NOT use
or import this module; in production, the caller (predicted-conditions)
already knows the exact `bucket`/`key` of each document's OCR artifact and
passes it directly (see `ocr_auditor/contracts.py`'s module docstring).

OCR key derivation (resolves most of spec open question #2): manifests
with no top-level `artifacts[]` array at all (confirmed on the exact Sahay
case that originally motivated this project -- 34 documents, 0 artifacts)
are NOT a dead end. Every `documents[].source.key` observed follows
Tasktile's fixed blob layout:

    clients/<client>/jobs/<job>/blobs/<blob>/documents/<document_id>.pdf

and every real `artifacts[]` OCR entry observed points at the exact same
client/job/blob path, just swapping the last two segments:

    clients/<client>/jobs/<job>/blobs/<blob>/ocr/<document_id>.txt

So the OCR key can be *derived* directly from a document's own
`source.key`, with no dependency on `artifacts[]` being present at all.
Verified against a real S3 fetch of Sahay's own Appraisal Report (document
`1893b393-b59b-4195-a181-05c01c4efac9`) using a derived key -- it resolved
and returned the full 40/40-page OCR text, with no `artifacts[]` entry
existing anywhere in that manifest. A confirmed `artifacts[]` entry is
always preferred over a derived key when both exist.

Usage:
    python scripts/manifest_to_audit_requests.py \\
        --final-output path/to/final_output.json \\
        --manifest path/to/manifest.json \\
        --document-type "Appraisal Report" \\
        --output audit_requests.json

Then feed the result straight into scripts/run_local.py:
    python scripts/run_local.py --audit-requests audit_requests.json ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _derive_ocr_key(pdf_source_key: str, document_id: str) -> str | None:
    """`.../blobs/<blob_id>/documents/<document_id>.pdf` ->
    `.../blobs/<blob_id>/ocr/<document_id>.txt`. Returns None if
    `pdf_source_key` doesn't contain the expected `/documents/` segment."""
    marker = "/documents/"
    if marker not in pdf_source_key:
        return None
    base = pdf_source_key.rsplit(marker, 1)[0]
    return f"{base}/ocr/{document_id}.txt"


def _index_manifest(manifest: dict) -> tuple[dict, dict]:
    docs_by_id = {}
    for raw in manifest.get("documents", []) or []:
        doc_id = raw.get("id")
        if doc_id:
            docs_by_id[doc_id] = raw

    artifacts_by_doc_id = {}
    for raw in manifest.get("artifacts", []) or []:
        if raw.get("type") != "ocr":
            continue
        doc_id = raw.get("document_id")
        source = raw.get("source") or {}
        if doc_id and source.get("bucket") and source.get("key"):
            artifacts_by_doc_id[doc_id] = source

    return docs_by_id, artifacts_by_doc_id


def resolve_bucket_key(document_id: str, manifest: dict) -> tuple[str, str]:
    """Returns (bucket, key) for `document_id`, preferring a confirmed
    `manifest.artifacts[]` entry and falling back to a derived key from
    `manifest.documents[].source.key`. Raises ValueError if neither works."""
    docs_by_id, artifacts_by_doc_id = _index_manifest(manifest)

    artifact_source = artifacts_by_doc_id.get(document_id)
    if artifact_source:
        return artifact_source["bucket"], artifact_source["key"]

    doc = docs_by_id.get(document_id)
    if doc is None:
        raise ValueError(f"document_id {document_id!r} not found in manifest.documents[]")

    source = doc.get("source") or {}
    pdf_key = source.get("key")
    bucket = source.get("bucket")
    derived_key = _derive_ocr_key(pdf_key, document_id) if pdf_key else None
    if derived_key and bucket:
        return bucket, derived_key

    raise ValueError(
        f"no confirmed OCR artifact for document_id {document_id!r}, and its "
        f"source.key did not match the expected '.../documents/<id>.pdf' "
        f"pattern needed to derive an OCR key"
    )


def convert(final_output: dict, manifest: dict, document_type: str | None) -> list[dict]:
    raw_requests = (
        final_output["document_requests"]
        if isinstance(final_output, dict) and "document_requests" in final_output
        else final_output
    )

    audit_requests = []
    for req in raw_requests:
        if document_type and req.get("document_type", "").strip().lower() != document_type.strip().lower():
            continue

        specs = req.get("specifications_unsatisfied", req.get("specifications", []))
        for doc_id in req.get("document_ids", []):
            try:
                bucket, key = resolve_bucket_key(doc_id, manifest)
            except ValueError as exc:
                print(f"skipping document_id={doc_id!r}: {exc}", file=sys.stderr)
                continue
            audit_requests.append(
                {
                    "bucket": bucket,
                    "key": key,
                    "document_type": req.get("document_type", ""),
                    "specifications_unsatisfied": specs,
                }
            )

    return audit_requests


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--final-output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--document-type",
        default=None,
        help="Only convert document_requests with this exact document_type. Omit for all.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    final_output = json.loads(args.final_output.read_text())
    manifest = json.loads(args.manifest.read_text())

    audit_requests = convert(final_output, manifest, args.document_type)

    out = json.dumps({"audit_requests": audit_requests}, indent=2)
    if args.output:
        args.output.write_text(out)
        print(f"Wrote {len(audit_requests)} audit_requests to {args.output}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
