"""AWS CDK stack for the OCR Auditor Agent.

Deliberately a SEPARATE stack/deployment from predicted-conditions' own
`PredictedConditionsStack` (see docs/ocr-auditor-agent-spec.md
#deployment-shape): no shared Lambda, no shared DynamoDB checkpoint table,
no live code import. The only coupling is the JSON data contract
(`ocr_auditor.contracts`).

This agent is stateless and read-only (it doesn't write back into
predicted-conditions' state), so unlike that stack there is no DynamoDB
table or checkpoint-offload S3 bucket here at all -- just the Lambda
function itself.

Lambda-only (no Fargate): a per-document LLM judge call plus a handful of
documents per request_requests batch is expected to run well under
Lambda's 900s cap -- revisit only if the "Long-context handling" /
multi-document chunking fallback from the spec's #audit-logic section ends
up needing longer-running batch jobs.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]

# Keep in sync with api/main.py's expected environment.
SECRET_KEYS = ["ANTHROPIC_API_KEY"]

_DOCKER_BUILD_EXCLUDES = [
    ".venv",
    "venv",
    ".git",
    "tests",
    "docs",
    "infra",
    "*.log",
    "*.zip",
]
_DOCKER_PLATFORM = ecr_assets.Platform.LINUX_ARM64

_IMAGE_SOURCE_PATHS = ["api/Dockerfile", "api/requirements.txt", "ocr_auditor", "api"]


def _image_source_hash() -> str:
    """CDK's default docker-asset source hash has been known to miss
    Dockerfile-only edits on large repo trees (see predicted-conditions'
    identical workaround) -- compute our own content hash of exactly the
    shipped files and pass it as extra_hash to force rebuilds reliably."""
    h = hashlib.sha256()
    paths: list[Path] = []
    for rel in _IMAGE_SOURCE_PATHS:
        p = REPO_ROOT / rel
        if p.is_file():
            paths.append(p)
        elif p.is_dir():
            paths.extend(sorted(f for f in p.rglob("*") if f.is_file()))
    for f in sorted(paths):
        h.update(str(f.relative_to(REPO_ROOT)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _deploy_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class OcrAuditorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str = "dev", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        is_prod = stage == "prod"
        suffix = "" if is_prod else f"-{stage}"

        # Real secret values are NOT passed to CDK/CloudFormation -- pushed
        # out-of-band via `aws secretsmanager put-secret-value` after
        # `cdk deploy`, same pattern as predicted-conditions.
        agent_secrets = secretsmanager.Secret(
            self,
            "AuditorSecrets",
            secret_name=(None if is_prod else f"ocr-auditor-secrets{suffix}"),
            description=f"ocr-auditor-agent API keys ({stage}). Managed out-of-band.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{" + ",".join(f'"{k}":""' for k in SECRET_KEYS) + "}",
                generate_string_key="_cfn_placeholder",
                exclude_punctuation=True,
            ),
        )

        auditor_fn = lambda_.DockerImageFunction(
            self,
            "AuditorFunction",
            function_name=(None if is_prod else f"ocr-auditor-agent{suffix}"),
            description=f"OCR Auditor Agent ({stage}) -- see docs/ocr-auditor-agent-spec.md",
            code=lambda_.DockerImageCode.from_image_asset(
                str(REPO_ROOT),
                file="api/Dockerfile",
                exclude=_DOCKER_BUILD_EXCLUDES,
                platform=_DOCKER_PLATFORM,
                extra_hash=_image_source_hash(),
            ),
            memory_size=2048,
            timeout=Duration.seconds(300),
            architecture=lambda_.Architecture.ARM_64,
            environment={
                "AGENT_SECRETS_ARN": agent_secrets.secret_arn,
                "AUDITOR_MODEL": _deploy_env("AUDITOR_MODEL", "claude-sonnet-4-5-20250929"),
            },
        )
        agent_secrets.grant_read(auditor_fn)

        # --- Tasktile OCR bucket access (spec open question #1) ---
        # NOT YET ARRANGED as of this writing. Confirmed directly (read-only
        # probe, this account's existing predicted-conditions AWS creds):
        #   - `tasktile-staging` IS a real bucket, region us-west-2, and is
        #     NOT owned by this project's AWS account (828351637694) --
        #     HeadBucket returns 403 Forbidden from that account.
        #   - GetObject on a real, known OCR artifact key returns
        #     AccessDenied from that same account.
        # See docs/tasktile-bucket-access-request.md for the concrete ask
        # and who to route it to. Once resolved, set ONE of:
        #   - OCR_SOURCE_BUCKET_NAME: Tasktile grants a bucket policy
        #     allowing this account's Lambda role direct GetObject -- adds
        #     an identity-policy statement below.
        #   - OCR_SOURCE_BUCKET_ROLE_ARN: Tasktile instead exposes a
        #     cross-account role to assume -- grant sts:AssumeRole on it
        #     instead (S3OCRTextFetcher already supports `role_arn=`, wired
        #     via api/main.py's OCR_SOURCE_BUCKET_ROLE_ARN env var).
        source_bucket_name = _deploy_env("OCR_SOURCE_BUCKET_NAME")
        source_bucket_role_arn = _deploy_env("OCR_SOURCE_BUCKET_ROLE_ARN")
        if source_bucket_name:
            auditor_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[f"arn:aws:s3:::{source_bucket_name}/*"],
                )
            )
        elif source_bucket_role_arn:
            auditor_fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[source_bucket_role_arn],
                )
            )
            auditor_fn.add_environment("OCR_SOURCE_BUCKET_ROLE_ARN", source_bucket_role_arn)
        else:
            print(
                "[infra/stacks/ocr_auditor_stack.py] WARNING: neither "
                "OCR_SOURCE_BUCKET_NAME nor OCR_SOURCE_BUCKET_ROLE_ARN is set "
                "-- deploying without any S3 read permission for "
                "tasktile-staging. S3OCRTextFetcher will fail at runtime until "
                "the access described in docs/tasktile-bucket-access-request.md "
                "is arranged."
            )

        # Lambda Function URL -- simple HTTP entry point, no API Gateway
        # needed for a single-endpoint read-only service.
        function_url = auditor_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
            invoke_mode=lambda_.InvokeMode.BUFFERED,
        )

        CfnOutput(self, "FunctionUrl", value=function_url.url)
        CfnOutput(self, "AuditorSecretsArn", value=agent_secrets.secret_arn)
        CfnOutput(self, "LambdaFunctionName", value=auditor_fn.function_name)
        CfnOutput(self, "Stage", value=stage)
