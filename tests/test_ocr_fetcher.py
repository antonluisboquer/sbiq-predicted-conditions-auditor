import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ocr_auditor.ocr_fetcher import (
    LocalDirOCRTextFetcher,
    OCRTextFetchError,
    OCRTextResult,
    S3OCRTextFetcher,
    _parse_artifact_body,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ocr_texts"


def test_local_fetcher_reads_by_key_basename():
    fetcher = LocalDirOCRTextFetcher(FIXTURES)
    result = fetcher.fetch(
        "tasktile-staging",
        "clients/x/jobs/y/blobs/z/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt",
    )
    assert "PURCHASE AGREEMENT" in result.text
    assert result.is_partial is False  # plain-text fixture has no whisper_metadata


def test_local_fetcher_missing_file_raises():
    fetcher = LocalDirOCRTextFetcher(FIXTURES)
    with pytest.raises(OCRTextFetchError):
        fetcher.fetch("tasktile-staging", "a/b/c/does-not-exist.txt")


def test_parse_artifact_body_extracts_result_text_from_real_json_shape():
    """Real Tasktile OCR artifacts are JSON with a 'result_text' field, not
    plain text — confirmed against real S3 objects. See ocr_fetcher.py's
    module docstring."""
    body = json.dumps(
        {
            "result_text": "the real OCR text",
            "confidence_metadata": [[]],
            "whisper_metadata": {
                "processed_page_count": 40,
                "requested_page_count": 40,
                "total_page_count": 40,
            },
        }
    ).encode("utf-8")
    result = _parse_artifact_body(body)
    assert result.text == "the real OCR text"
    assert result.is_partial is False


def test_parse_artifact_body_flags_partial_coverage():
    body = json.dumps(
        {
            "result_text": "only a few pages worth of text",
            "whisper_metadata": {
                "processed_page_count": 3,
                "requested_page_count": 3,
                "total_page_count": 29,
            },
        }
    ).encode("utf-8")
    result = _parse_artifact_body(body)
    assert result.is_partial is True


def test_parse_artifact_body_falls_back_to_plain_text():
    body = b"just plain OCR text, not JSON at all"
    result = _parse_artifact_body(body)
    assert result.text == "just plain OCR text, not JSON at all"
    assert result.is_partial is False


def test_parse_artifact_body_falls_back_when_json_missing_result_text():
    body = json.dumps({"some_other_field": "value"}).encode("utf-8")
    result = _parse_artifact_body(body)
    assert "some_other_field" in result.text  # fell back to raw decoded body


def test_from_env_uses_task_tile_s3_region_not_aws_region(monkeypatch):
    """Regression guard: AWS_REGION is reserved by the Lambda runtime and
    always force-set to wherever the function is deployed, so it must NOT
    influence which region the tasktile-staging S3 client is built with.
    TASK_TILE_S3_REGION is the only var that should matter here."""
    monkeypatch.setenv("TASK_TILE_S3_ACCESS_KEY", "fake-key")
    monkeypatch.setenv("TASK_TILE_S3_SECRET_KEY", "fake-secret")
    monkeypatch.setenv("AWS_REGION", "us-east-2")  # simulates Lambda's reserved var
    monkeypatch.setenv("TASK_TILE_S3_REGION", "us-west-2")

    with patch("boto3.client") as mock_boto_client:
        S3OCRTextFetcher.from_env()

    _, kwargs = mock_boto_client.call_args
    assert kwargs["region_name"] == "us-west-2"


def test_from_env_defaults_region_when_task_tile_s3_region_unset(monkeypatch):
    monkeypatch.setenv("TASK_TILE_S3_ACCESS_KEY", "fake-key")
    monkeypatch.setenv("TASK_TILE_S3_SECRET_KEY", "fake-secret")
    monkeypatch.delenv("TASK_TILE_S3_REGION", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-2")  # must be ignored

    with patch("boto3.client") as mock_boto_client:
        S3OCRTextFetcher.from_env()

    _, kwargs = mock_boto_client.call_args
    assert kwargs["region_name"] == "us-west-2"


def test_from_env_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("TASK_TILE_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TASK_TILE_S3_SECRET_KEY", raising=False)
    with pytest.raises(OCRTextFetchError):
        S3OCRTextFetcher.from_env()
