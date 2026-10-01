"""
OCR text retrieval, per docs/ocr-auditor-agent-spec.md (#ocr-retrieval-mechanism).

Open question #1 in the spec is the single biggest blocker for this repo:
there is no confirmed AWS credential/IAM-role path scoped to Tasktile's
`tasktile-staging` bucket yet. The fetcher is therefore behind a tiny
Protocol so `S3OCRTextFetcher` can be swapped in once access is arranged,
without touching the resolver, judge, or graph wiring. Until then,
`LocalDirOCRTextFetcher` lets the rest of the pipeline be built/tested
end-to-end against local fixture text files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class OCRTextFetchError(RuntimeError):
    """Raised when OCR text could not be retrieved for a resolved document."""


class OCRTextFetcher(Protocol):
    def fetch(self, bucket: str, key: str) -> str: ...


class S3OCRTextFetcher:
    """Real fetcher — GetObject against the bucket/key resolved from the
    manifest's `artifacts[]` entry.

    Requires boto3 plus credentials/role scoped to the source bucket, which
    per spec open question #1 is NOT yet arranged. Calling this against the
    real `tasktile-staging` bucket today is expected to fail with an auth
    error until that access is granted (cross-account role, presigned URLs
    issued at ingestion, or a Tasktile API proxy — see the spec).
    """

    def __init__(self, client=None):
        if client is None:
            import boto3  # local import — keep boto3 optional for non-AWS paths

            client = boto3.client("s3")
        self._client = client

    def fetch(self, bucket: str, key: str) -> str:
        try:
            obj = self._client.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
        except Exception as exc:  # noqa: BLE001 — re-raised as OCRTextFetchError
            raise OCRTextFetchError(f"failed to fetch s3://{bucket}/{key}: {exc}") from exc
        return body.decode("utf-8", errors="replace")


class LocalDirOCRTextFetcher:
    """Dev/test fetcher — reads OCR text from a local directory of
    `<document_id>.txt` files instead of S3.

    Keyed by the last path segment of the manifest key, matching how
    Tasktile names OCR artifacts (`.../ocr/<document_id>.txt` — see the
    spec's artifacts[] example). Used by `scripts/run_local.py` and the test
    suite so the pipeline can be exercised end-to-end before S3 access to
    `tasktile-staging` is arranged, and because the real `.txt` content/
    format hasn't been inspected yet either (spec open question #5).
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def fetch(self, bucket: str, key: str) -> str:
        filename = Path(key).name  # e.g. "<document_id>.txt"
        path = self._root / filename
        if not path.exists():
            raise OCRTextFetchError(
                f"no local OCR fixture at {path} for s3://{bucket}/{key}"
            )
        return path.read_text(encoding="utf-8", errors="replace")
