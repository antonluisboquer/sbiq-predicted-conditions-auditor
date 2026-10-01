"""Load secrets from AWS Secrets Manager on Lambda cold start.

Local dev (``scripts/run_local.py``) never sets ``AGENT_SECRETS_ARN``, so
this is a no-op there -- secrets keep coming from ``.env`` as before. On
Lambda, ``AGENT_SECRETS_ARN`` points at the Secrets Manager entry created
by the CDK stack (``infra/stacks/ocr_auditor_stack.py``) and populated
out-of-band via ``aws secretsmanager put-secret-value`` after ``cdk
deploy`` -- never as a CloudFormation resource property.

Mirrors predicted-conditions' ``api/secrets.py`` pattern exactly (same
load-once-at-cold-start, set-only-if-not-already-set behavior).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)
_loaded = False

# Keys hydrated from Secrets Manager into os.environ at cold start, so
# ocr_auditor/judge.py's ChatAnthropic client and ocr_auditor/ocr_fetcher.py's
# S3OCRTextFetcher.from_env() stay unchanged whether running locally or in
# Lambda. Keep in sync with infra/stacks/ocr_auditor_stack.py:SECRET_KEYS.
SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    # Dedicated Tasktile-OCR-scoped S3 credentials (deliberately separate
    # from predicted-conditions' own general AWS creds) -- see
    # ocr_auditor/ocr_fetcher.py's module docstring. Only needed when the
    # Lambda isn't configured with OCR_SOURCE_BUCKET_ROLE_ARN instead (see
    # api/main.py).
    "TASK_TILE_S3_ACCESS_KEY",
    "TASK_TILE_S3_SECRET_KEY",
    # Static app-level auth key for the Function URL's `x-api-key` header
    # check (api/main.py) -- same pattern as predicted-conditions'
    # api/secrets.py. The Function URL itself uses
    # FunctionUrlAuthType.NONE (no AWS SigV4 required), so this is the
    # only thing gating access to the public HTTP endpoint.
    "API_KEY",
)


def load_secrets() -> None:
    global _loaded
    if _loaded:
        return

    secret_arn = os.environ.get("AGENT_SECRETS_ARN", "").strip()
    if not secret_arn:
        _loaded = True
        return

    try:
        import boto3

        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=secret_arn)
        payload = json.loads(resp["SecretString"])
        for key in SECRET_KEYS:
            value = payload.get(key)
            if value and not os.environ.get(key):
                os.environ[key] = str(value)
        logger.info("Loaded auditor secrets from Secrets Manager")
    except Exception:
        logger.exception("Failed to load secrets from %s", secret_arn)
    finally:
        _loaded = True
