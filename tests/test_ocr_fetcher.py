from pathlib import Path

import pytest

from ocr_auditor.ocr_fetcher import LocalDirOCRTextFetcher, OCRTextFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "ocr_texts"


def test_local_fetcher_reads_by_key_basename():
    fetcher = LocalDirOCRTextFetcher(FIXTURES)
    text = fetcher.fetch(
        "tasktile-staging",
        "clients/x/jobs/y/blobs/z/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt",
    )
    assert "PURCHASE AGREEMENT" in text


def test_local_fetcher_missing_file_raises():
    fetcher = LocalDirOCRTextFetcher(FIXTURES)
    with pytest.raises(OCRTextFetchError):
        fetcher.fetch("tasktile-staging", "a/b/c/does-not-exist.txt")
