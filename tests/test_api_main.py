"""Tests for api/main.py's Lambda handler -- focused on the `x-api-key`
auth check (api/main.py's `_check_api_key`), since the Function URL is
deployed with FunctionUrlAuthType.NONE and relies entirely on this
app-level check (see infra/stacks/ocr_auditor_stack.py)."""

import pytest

import api.main as api_main


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch):
    """api.main reads MODEL etc. at import time, but `_check_api_key`
    reads os.environ live on every call, so no reload is strictly needed
    -- just make sure API_KEY starts unset for each test."""
    monkeypatch.delenv("API_KEY", raising=False)
    yield


def test_check_api_key_skips_raw_dict_invoke_without_headers():
    """Direct `aws lambda invoke` / local-test calls have no "headers" key
    at all -- already gated by AWS IAM's lambda:InvokeFunction permission,
    so this app-level check is a no-op for that path."""
    event = {"audit_requests": []}
    assert api_main._check_api_key(event) is None


def test_check_api_key_rejects_missing_header_when_configured(monkeypatch):
    monkeypatch.setenv("API_KEY", "super-secret-key")
    event = {"headers": {}}
    assert api_main._check_api_key(event) == "invalid API key"


def test_check_api_key_rejects_wrong_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "super-secret-key")
    event = {"headers": {"x-api-key": "wrong"}}
    assert api_main._check_api_key(event) == "invalid API key"


def test_check_api_key_accepts_correct_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "super-secret-key")
    event = {"headers": {"x-api-key": "super-secret-key"}}
    assert api_main._check_api_key(event) is None


def test_check_api_key_accepts_correct_header_case_variant(monkeypatch):
    """Function URL events may normalize header casing differently
    depending on the client; handle the common capitalized variant too."""
    monkeypatch.setenv("API_KEY", "super-secret-key")
    event = {"headers": {"X-Api-Key": "super-secret-key"}}
    assert api_main._check_api_key(event) is None


def test_check_api_key_fails_closed_when_unconfigured():
    """If API_KEY somehow isn't set at all on a deployed, publicly
    reachable (auth_type=NONE) Function URL, reject rather than silently
    allowing unauthenticated access."""
    event = {"headers": {"x-api-key": "anything"}}
    assert api_main._check_api_key(event) == "server is not configured with an API key"


def test_handler_returns_401_for_http_shaped_event_with_bad_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "correct-key")
    event = {
        "headers": {"x-api-key": "wrong-key"},
        "body": '{"audit_requests": []}',
    }
    resp = api_main.handler(event)
    assert resp["statusCode"] == 401


def test_handler_proceeds_past_auth_for_raw_dict_invoke(monkeypatch):
    """No "headers" key at all -- auth check is skipped entirely, so the
    handler proceeds straight to body validation instead of a 401. (It may
    still fail later for unrelated reasons, e.g. missing S3 credentials in
    this test environment -- we're only confirming the auth gate itself
    isn't what blocks it.)"""
    monkeypatch.setenv("API_KEY", "correct-key")
    resp = api_main.handler({"audit_requests": []})
    assert resp["statusCode"] != 401
