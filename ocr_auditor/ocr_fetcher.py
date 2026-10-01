"""
OCR text retrieval, per docs/ocr-auditor-agent-spec.md (#ocr-retrieval-mechanism).

UPDATE (resolves spec open question #5): access to `tasktile-staging` IS now
confirmed working (scoped credentials, account `872194582181`, IAM user
`svc.sbiqai.uat`) -- open question #1 is resolved for that bucket. A
second, separate bucket, `tasktile-dev`, exists and is used by some other
samples (nyarko, weingarten, montes, niccum) -- NOT covered by these
credentials; see docs/tasktile-bucket-access-request.md.

The real artifact body is also NOT plain OCR text as the original spec
assumed ("all pages concatenated"). It's a JSON document with:

    {
      "result_text": "<the actual full OCR text, this is what we want>",
      "confidence_metadata": [...],   # per-page, per-token confidence (unused here)
      "line_metadata": [...],         # per-page line offsets (unused here)
      "metadata": {"0": {...}, ...},  # per-page font/layout stats (unused here)
      "webhook_metadata": "",
      "whisper_metadata": {
        "mode": "form",
        "processed_page_count": 40,
        "requested_page_count": 40,
        "total_page_count": 40
      }
    }

Verified against two real artifacts: Sahay's Appraisal Report (the exact
document that originally motivated this project) came back with
`processed_page_count == total_page_count == 40` -- the FULL document, not
a partial extract. A large (29-page) Purchase Contract sample came back
with `processed_page_count: 3, total_page_count: 29` -- genuinely partial,
apparently by request-time design (`requested_page_count` matched
`processed_page_count` exactly, i.e. only 3 pages were ever asked for, not
a failure) rather than a scanning failure. `S3OCRTextFetcher` surfaces
`whisper_metadata` so callers (see `judge.py`/`graph.py`) can see when
coverage might be partial, rather than silently treating partial text as
the whole document.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class OCRTextFetchError(RuntimeError):
    """Raised when OCR text could not be retrieved for a resolved document."""


@dataclass
class OCRTextResult:
    """Result of fetching one document's OCR artifact.

    `whisper_metadata` (when available) carries Tasktile's own page-coverage
    accounting (`processed_page_count` / `requested_page_count` /
    `total_page_count`) -- see module docstring. `is_partial` is True when
    `processed_page_count < total_page_count`, i.e. the returned `text` is
    known to NOT cover the whole document.
    """

    text: str
    whisper_metadata: dict = field(default_factory=dict)

    @property
    def is_partial(self) -> bool:
        total = self.whisper_metadata.get("total_page_count")
        processed = self.whisper_metadata.get("processed_page_count")
        if total is None or processed is None:
            return False
        return processed < total


def _parse_artifact_body(raw_bytes: bytes) -> OCRTextResult:
    """Parse a raw OCR artifact body into its usable text + coverage info.

    Real artifacts observed so far are JSON with a `result_text` field (see
    module docstring). Falls back to treating the whole body as plain text
    if it isn't valid JSON or doesn't have that shape -- defensive, in case
    some artifacts really are plain `.txt` as the original spec assumed
    (not yet observed, but the spec's own open question #5 flagged this as
    unverified, so don't hard-fail on a shape we haven't seen).
    """
    try:
        parsed = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return OCRTextResult(text=raw_bytes.decode("utf-8", errors="replace"))

    if isinstance(parsed, dict) and isinstance(parsed.get("result_text"), str):
        return OCRTextResult(
            text=parsed["result_text"],
            whisper_metadata=parsed.get("whisper_metadata") or {},
        )

    # Valid JSON but not the expected shape -- fall back to the raw decoded
    # body rather than guessing further.
    logger.warning(
        "OCR artifact parsed as JSON but had no string 'result_text' field; "
        "falling back to raw body as text"
    )
    return OCRTextResult(text=raw_bytes.decode("utf-8", errors="replace"))


class OCRTextFetcher(Protocol):
    def fetch(self, bucket: str, key: str) -> OCRTextResult: ...


class S3OCRTextFetcher:
    """Real fetcher — GetObject against the exact `bucket`/`key` on the
    caller's `AuditRequest` (see `contracts.py`; no manifest resolution
    happens in this repo), then parses the JSON artifact body into
    `OCRTextResult`.

    Access to `tasktile-staging` is confirmed working as of this writing
    (scoped credentials, account `872194582181`, IAM user `svc.sbiqai.uat`,
    region `us-west-2`). A second bucket, `tasktile-dev`, used by some other
    samples, is NOT covered by those same credentials — see
    docs/tasktile-bucket-access-request.md. `fetch()` doesn't hardcode a
    bucket; it uses whatever bucket the caller passed in, so requests for
    `tasktile-dev` documents will fail with AccessDenied until separate
    credentials/grant for that bucket are arranged too.
    """

    def __init__(
        self,
        client=None,
        *,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        role_arn: str | None = None,
        region_name: str = "us-west-2",
    ):
        if client is None:
            import boto3  # local import — keep boto3 optional for non-AWS paths

            if role_arn:
                sts = boto3.client(
                    "sts",
                    aws_access_key_id=access_key_id,
                    aws_secret_access_key=secret_access_key,
                )
                creds = sts.assume_role(
                    RoleArn=role_arn, RoleSessionName="ocr-auditor-fetch"
                )["Credentials"]
                client = boto3.client(
                    "s3",
                    region_name=region_name,
                    aws_access_key_id=creds["AccessKeyId"],
                    aws_secret_access_key=creds["SecretAccessKey"],
                    aws_session_token=creds["SessionToken"],
                )
            else:
                client = boto3.client(
                    "s3",
                    region_name=region_name,
                    aws_access_key_id=access_key_id,
                    aws_secret_access_key=secret_access_key,
                )
        self._client = client

    @classmethod
    def from_env(cls, *, region_name: str = "us-west-2") -> "S3OCRTextFetcher":
        """Build from `S3_ACCESS_KEY` / `S3_SECRET_KEY` env vars — the
        dedicated Tasktile-OCR-scoped credentials (deliberately separate
        from predicted-conditions' own general AWS creds, per the naming
        convention already in use in `.env`)."""
        import os

        access_key_id = os.environ.get("S3_ACCESS_KEY")
        secret_access_key = os.environ.get("S3_SECRET_KEY")
        if not access_key_id or not secret_access_key:
            raise OCRTextFetchError(
                "S3_ACCESS_KEY / S3_SECRET_KEY not set — required for "
                "S3OCRTextFetcher.from_env()"
            )
        return cls(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region_name=os.environ.get("AWS_REGION", region_name),
        )

    def fetch(self, bucket: str, key: str) -> OCRTextResult:
        try:
            obj = self._client.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
        except Exception as exc:  # noqa: BLE001 — re-raised as OCRTextFetchError
            raise OCRTextFetchError(f"failed to fetch s3://{bucket}/{key}: {exc}") from exc
        result = _parse_artifact_body(body)
        if result.is_partial:
            logger.warning(
                "partial OCR coverage for s3://%s/%s: processed %s of %s pages",
                bucket,
                key,
                result.whisper_metadata.get("processed_page_count"),
                result.whisper_metadata.get("total_page_count"),
            )
        return result


class LocalDirOCRTextFetcher:
    """Dev/test fetcher — reads an OCR artifact from a local directory of
    `<document_id>.txt` files instead of S3, parsing it the same way
    `S3OCRTextFetcher` does (handles both the real JSON-wrapped shape and
    plain-text fixtures).

    Keyed by the last path segment of the manifest key, matching how
    Tasktile names OCR artifacts (`.../ocr/<document_id>.txt`). Used by
    `scripts/run_local.py` and the test suite so the pipeline can be
    exercised end-to-end without live S3 calls.
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def fetch(self, bucket: str, key: str) -> OCRTextResult:
        filename = Path(key).name  # e.g. "<document_id>.txt"
        path = self._root / filename
        if not path.exists():
            raise OCRTextFetchError(
                f"no local OCR fixture at {path} for s3://{bucket}/{key}"
            )
        return _parse_artifact_body(path.read_bytes())
