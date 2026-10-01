#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.ocr_auditor_stack import OcrAuditorStack

# Unlike predicted-conditions, this is a brand-new separate deployment with
# no established AWS account/region yet -- no hardcoded default here on
# purpose. Set these explicitly before `cdk deploy`.
ACCOUNT = os.environ.get("OCR_AUDITOR_AWS_ACCOUNT_ID")
REGION = os.environ.get("OCR_AUDITOR_AWS_REGION")

if not ACCOUNT or not REGION:
    raise SystemExit(
        "Set OCR_AUDITOR_AWS_ACCOUNT_ID and OCR_AUDITOR_AWS_REGION before "
        "running cdk (this is a new stack with no established account/region "
        "default yet -- see docs/ocr-auditor-agent-spec.md #deployment-shape)."
    )

# Stage separation: OCR_AUDITOR_STAGE=dev|prod selects which stack instance
# to synthesize/deploy. Defaults to "dev" so a bare `cdk deploy` never
# accidentally touches prod.
STAGE = os.environ.get("OCR_AUDITOR_STAGE", "dev").strip().lower()
if STAGE not in ("dev", "prod"):
    raise SystemExit(f"OCR_AUDITOR_STAGE must be 'dev' or 'prod', got: {STAGE!r}")

STACK_ID = "OcrAuditorStack" if STAGE == "prod" else "OcrAuditorStack-Dev"

print(f"[infra/app.py] Targeting account={ACCOUNT} region={REGION} stage={STAGE}")

app = cdk.App()
OcrAuditorStack(
    app,
    STACK_ID,
    stage=STAGE,
    env=cdk.Environment(account=ACCOUNT, region=REGION),
)
app.synth()
