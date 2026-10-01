import importlib
import json
from unittest.mock import MagicMock, patch

import ocr_auditor.secrets as secrets_module


def _reload():
    """secrets.load_secrets() memoizes via a module-level `_loaded` flag --
    reload the module so each test starts from a clean slate."""
    return importlib.reload(secrets_module)


def test_load_secrets_is_noop_without_agent_secrets_arn(monkeypatch):
    mod = _reload()
    monkeypatch.delenv("AGENT_SECRETS_ARN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mod.load_secrets()

    assert "ANTHROPIC_API_KEY" not in __import__("os").environ


def test_load_secrets_hydrates_env_from_secrets_manager(monkeypatch):
    mod = _reload()
    monkeypatch.setenv("AGENT_SECRETS_ARN", "arn:aws:secretsmanager:fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TASK_TILE_S3_ACCESS_KEY", raising=False)

    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {
                "ANTHROPIC_API_KEY": "sk-fake",
                "TASK_TILE_S3_ACCESS_KEY": "AKIAFAKE",
                "TASK_TILE_S3_SECRET_KEY": "fakefakefake",
            }
        )
    }

    with patch("boto3.client", return_value=fake_client):
        mod.load_secrets()

    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-fake"
    assert os.environ["TASK_TILE_S3_ACCESS_KEY"] == "AKIAFAKE"
    fake_client.get_secret_value.assert_called_once_with(SecretId="arn:aws:secretsmanager:fake")


def test_load_secrets_never_overwrites_already_set_env(monkeypatch):
    mod = _reload()
    monkeypatch.setenv("AGENT_SECRETS_ARN", "arn:aws:secretsmanager:fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already-set-locally")

    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"ANTHROPIC_API_KEY": "from-secrets-manager"})
    }

    with patch("boto3.client", return_value=fake_client):
        mod.load_secrets()

    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "already-set-locally"


def test_load_secrets_only_runs_once(monkeypatch):
    mod = _reload()
    monkeypatch.setenv("AGENT_SECRETS_ARN", "arn:aws:secretsmanager:fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": json.dumps({})}

    with patch("boto3.client", return_value=fake_client) as mock_boto:
        mod.load_secrets()
        mod.load_secrets()

    mock_boto.assert_called_once()


def test_load_secrets_handles_fetch_error_gracefully(monkeypatch):
    mod = _reload()
    monkeypatch.setenv("AGENT_SECRETS_ARN", "arn:aws:secretsmanager:fake")

    with patch("boto3.client", side_effect=RuntimeError("boom")):
        mod.load_secrets()  # must not raise

    assert mod._loaded is True
