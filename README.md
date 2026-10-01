# ocr-auditor-agent

A separate, read-only, downstream agent that re-checks mortgage-loan
document specifications that [`predicted-conditions`](../predicted-conditions)
marked **unsatisfied**, by reading the document's **full raw OCR text**
instead of the sparse structured `metadata` fields that pipeline is limited
to.

Full design: [`docs/ocr-auditor-agent-spec.md`](docs/ocr-auditor-agent-spec.md).

## Why

`predicted-conditions` only ever sees a fixed-size extraction template per
document category (e.g. an Appraisal Report yields ~4 fields regardless of
its ~40 pages), so specs like *"must contain street-view photographs"* can
never be verified from that metadata alone — they always come back
unsatisfied. Some Tasktile manifests include a pointer to the document's
full OCR `.txt` output; this agent fetches that text and re-adjudicates the
unsatisfied specs directly against it.

## Status

Validated end-to-end against real data (Sahay Vibhor Binayprasad's
Appraisal Report, the exact case that originally motivated this project):
real S3 fetch from `tasktile-staging`, real Sonnet judge call, 22 real
unsatisfied specs re-adjudicated. Of spec open questions #1/#2/#5:

1. AWS access to `tasktile-staging` **is confirmed working** (scoped
   credentials; a separate bucket, `tasktile-dev`, is not yet covered).
2. The caller now passes each document's exact OCR `bucket`/`key` directly
   (see **Input/output contract** below) — this repo no longer resolves
   `document_ids` against a manifest itself, so "manifests with no
   `artifacts[]` key" is no longer this repo's problem to solve.
5. Real OCR artifacts are JSON (`{"result_text": ..., "whisper_metadata": ...}`),
   not plain text — handled in `ocr_auditor/ocr_fetcher.py`.

Everything here can be exercised **locally today** with fixture data via
`scripts/run_local.py` and `LocalDirOCRTextFetcher`, or against real S3 via
`S3OCRTextFetcher.from_env()`.

## Input/output contract

The engine runs **per document**, not per loan: give it an OCR artifact's
exact `bucket`/`key` plus the specifications to re-check against it. No
manifest, no `document_ids` — the caller (predicted-conditions) already
knows where each document's OCR text lives.

```json
{
  "audit_requests": [
    {
      "bucket": "tasktile-staging",
      "key": "clients/<client>/jobs/<job>/blobs/<blob>/ocr/<document_id>.txt",
      "document_type": "Appraisal Report",
      "specifications_unsatisfied": ["spec 1", "spec 2", "..."]
    }
  ]
}
```

returns an `AuditReport` with one `DocumentAuditResult` per request, and
**one verdict row per specification requested — every spec is persisted in
the output regardless of its verdict** (nothing is silently dropped):

```json
{
  "generated_at": "...",
  "document_results": [
    {
      "bucket": "tasktile-staging",
      "key": "...",
      "document_type": "Appraisal Report",
      "fetched": true,
      "error": null,
      "verdicts": [
        {
          "specification": "spec 1",
          "verdict": "satisfied",
          "confidence": 1.0,
          "evidence_quote": "verbatim OCR excerpt backing the verdict"
        }
      ]
    }
  ],
  "summary": {
    "total_documents_audited": 1,
    "documents_fetched": 1,
    "documents_fetch_failed": 0,
    "verdict_counts": {"satisfied": 1, "unsatisfied": 0}
  }
}
```

`verdict` is always one of `satisfied` / `unsatisfied` — a binary call,
no third "needs review" value. A verdict can only come back `satisfied`
if the judge's own `confidence` clears `CONFIDENCE_THRESHOLD` (0.8,
`ocr_auditor/contracts.py`); anything less confident is forced to
`unsatisfied` regardless of what the model said, so low confidence is the
human-review signal — a low-confidence `unsatisfied` means "this needs a
human to check", not "this document fails the requirement" outright.
`reasoning` is used internally by the judge step (improves verdict
quality, logged on failure) but is deliberately not part of this output
contract — see `ocr_auditor/contracts.py`.

If you only have the older `final_output.json` + `manifest.json` shape
(predicted-conditions' own data), convert it first with
`scripts/manifest_to_audit_requests.py` (dev/test convenience only — not
used by the core engine; see its module docstring for the OCR-key-
derivation logic it uses when a manifest has no `artifacts[]` entry for a
document).

## Architecture

```
Fetcher -> Judge -> Report
```

A linear [LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`
(`ocr_auditor/graph.py`) wiring three deterministic-except-one-step stages:

| Stage | Module | What it does |
|---|---|---|
| Fetch | `ocr_auditor/ocr_fetcher.py` | Retrieve the raw OCR text from the request's `bucket`/`key` — `S3OCRTextFetcher` (real) or `LocalDirOCRTextFetcher` (local fixtures). |
| Judge | `ocr_auditor/judge.py` + `prompts.py` | One LLM call per document: full OCR text + that document's unsatisfied specs → a verdict per spec, grounded only in quoted text. |
| Report | `ocr_auditor/graph.py` (`_report_node`) | Aggregate per-document verdicts into an `AuditReport` with summary counts. |

See `ocr_auditor/contracts.py` for the full input/output pydantic schemas.

## Running locally (no AWS, no deployment)

```bash
pip install -r requirements.txt

# Dry run: exercises fetch -> report, skips the LLM call entirely (no API
# key needed).
python scripts/run_local.py \
  --audit-requests tests/fixtures/sample_audit_requests.json \
  --ocr-dir tests/fixtures/ocr_texts \
  --dry-run

# Real run: calls the LLM judge step (requires ANTHROPIC_API_KEY).
export ANTHROPIC_API_KEY=...
python scripts/run_local.py \
  --audit-requests tests/fixtures/sample_audit_requests.json \
  --ocr-dir tests/fixtures/ocr_texts

# Against real S3 (requires .env's TASK_TILE_S3_ACCESS_KEY/
# TASK_TILE_S3_SECRET_KEY) -- omit --ocr-dir:
python scripts/run_local.py --audit-requests /tmp/my_audit_requests.json
```

`tests/fixtures/` pairs real spec wording (from
`predicted-conditions/data/canonical_doc_specs.json`) with **synthetic**
OCR text — see `tests/fixtures/README.md`.

## Running tests

```bash
pip install -r requirements.txt
pytest
```

All tests run offline (no AWS, no LLM API calls) via `dry_run=True` and a
fake LLM stand-in in `tests/test_judge.py`.

## Deployment

`infra/` is an AWS CDK stack (`OcrAuditorStack`) deploying a single
container-image Lambda behind a Function URL — see
`infra/stacks/ocr_auditor_stack.py`. It is a **genuinely separate** stack
from `predicted-conditions`' own (no shared Lambda, table, or runtime; see
spec `#deployment-shape`), but is deployed into the **same shared AWS
account** predicted-conditions already uses (`828351637694`, `us-east-2`),
rather than standing up a new one.

**Auth**: the Function URL is `FunctionUrlAuthType.NONE` (not
`AWS_IAM`/SigV4) — callers authenticate with a static `x-api-key` header
instead (checked in `api/main.py`'s `_check_api_key()`), the same pattern
predicted-conditions itself uses. This means any caller can reach it over
plain HTTP with just the key, without needing AWS credentials in this
account at all.

Tasktile OCR bucket access (`tasktile-staging`, a **different** AWS account
from the one this Lambda runs in) uses dedicated static credentials
(`TASK_TILE_S3_ACCESS_KEY`/`TASK_TILE_S3_SECRET_KEY`), not an IAM role on
this stack's own Lambda — see `ocr_auditor/ocr_fetcher.py`.

All four secrets (`ANTHROPIC_API_KEY`, `TASK_TILE_S3_ACCESS_KEY`,
`TASK_TILE_S3_SECRET_KEY`, `API_KEY`) live in Secrets Manager, hydrated into
`os.environ` at Lambda cold start by `ocr_auditor/secrets.py`. They are
**never** passed as CloudFormation properties — push/update them out-of-band
after any deploy:

```bash
cd infra
pip install -r requirements.txt
export OCR_AUDITOR_AWS_ACCOUNT_ID=828351637694
export OCR_AUDITOR_AWS_REGION=us-east-2
export OCR_AUDITOR_STAGE=dev   # or "prod"
cdk deploy

# then push real secret values (see AuditorSecretsArn in the deploy output)
aws secretsmanager put-secret-value --secret-id <AuditorSecretsArn> \
  --secret-string '{"ANTHROPIC_API_KEY":"...","TASK_TILE_S3_ACCESS_KEY":"...","TASK_TILE_S3_SECRET_KEY":"...","API_KEY":"..."}'
```

⚠️ **Gotcha** (see the comment above `agent_secrets` in
`infra/stacks/ocr_auditor_stack.py`): never derive
`generate_secret_string`'s template from `SECRET_KEYS` — changing that
property on an existing `AWS::SecretsManager::Secret` makes CloudFormation
regenerate the *entire* secret value on the next `cdk deploy`, silently
wiping out whatever real values were pushed out-of-band. The template is
deliberately a fixed `"{}"`, independent of `SECRET_KEYS`, so adding new
keys later can't trigger this again.

Calling the deployed agent once secrets are pushed:

```bash
curl -X POST "<FunctionUrl from deploy output>" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <API_KEY>" \
  -d '{"audit_requests": [...]}'
```

## Repo layout

```
ocr_auditor/                          # core library (fetch, judge, graph, contracts)
api/                                   # Lambda handler + Dockerfile
scripts/run_local.py                   # CLI runner against local fixtures or real S3
scripts/manifest_to_audit_requests.py  # dev/test-only: old manifest shape -> AuditRequest
infra/                                  # CDK stack
tests/                                  # pytest suite + fixtures
docs/ocr-auditor-agent-spec.md          # the design spec this repo implements
```
