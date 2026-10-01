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

Early scaffold. The two biggest open items from the spec are **not yet
resolved**:

1. No confirmed AWS access to the `tasktile-staging` bucket that holds the
   OCR `.txt` files (spec open question #1).
2. ~40% of sampled real manifests have no `artifacts[]` key at all, so OCR
   text can't be resolved for them even once (1) is solved (spec open
   question #2) — needs a fix at the run-submission layer upstream.

Everything here can be exercised **locally today** with fixture data via
`scripts/run_local.py` and `LocalDirOCRTextFetcher`, independent of both
blockers.

## Architecture

```
Resolver -> Fetcher -> Judge -> Report
```

A linear [LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph`
(`ocr_auditor/graph.py`) wiring four deterministic-except-one-step stages:

| Stage | Module | What it does |
|---|---|---|
| Resolve | `ocr_auditor/manifest_resolver.py` | Map each unsatisfied spec's `document_ids` to an OCR `.txt` location via `manifest.artifacts[]`; cross-check manifest category vs. requested `document_type`. |
| Fetch | `ocr_auditor/ocr_fetcher.py` | Retrieve the raw OCR text — `S3OCRTextFetcher` (real, blocked on access) or `LocalDirOCRTextFetcher` (local fixtures). |
| Judge | `ocr_auditor/judge.py` + `prompts.py` | One LLM call per document: full OCR text + that document's unsatisfied specs → a verdict per spec, grounded only in quoted text. |
| Report | `ocr_auditor/graph.py` (`_report_node`) | Aggregate per-document verdicts into an `AuditReport` with summary counts. |

See `ocr_auditor/contracts.py` for the full input/output pydantic schemas.

## Running locally (no AWS, no deployment)

```bash
pip install -r requirements.txt

# Dry run: exercises resolve -> fetch -> report, skips the LLM call
# entirely (no API key needed).
python scripts/run_local.py \
  --final-output tests/fixtures/sample_document_requests.json \
  --manifest tests/fixtures/sample_manifest.json \
  --ocr-dir tests/fixtures/ocr_texts \
  --dry-run

# Real run: calls the LLM judge step (requires ANTHROPIC_API_KEY).
export ANTHROPIC_API_KEY=...
python scripts/run_local.py \
  --final-output tests/fixtures/sample_document_requests.json \
  --manifest tests/fixtures/sample_manifest.json \
  --ocr-dir tests/fixtures/ocr_texts
```

`tests/fixtures/` pairs real manifest/spec shapes (trimmed from
`predicted-conditions/compiled_inputs/montes_10x` and
`data/canonical_doc_specs.json`) with **synthetic** OCR text — see
`tests/fixtures/README.md`. The real OCR `.txt` format hasn't been
inspected yet (spec open question #5); once it has, update these fixtures.

## Running tests

```bash
pip install -r requirements.txt
pytest
```

All tests run offline (no AWS, no LLM API calls) via `dry_run=True` and a
fake LLM stand-in in `tests/test_judge.py`.

## Deployment

`infra/` is an AWS CDK stack (`OcrAuditorStack`) deploying a single
container-image Lambda behind an IAM-authenticated Function URL — see
`infra/stacks/ocr_auditor_stack.py`. It is a **genuinely separate** stack
from `predicted-conditions`' own (no shared Lambda, table, or runtime; see
spec `#deployment-shape`). S3 read permission for the Tasktile OCR bucket is
left unset until the access described in spec open question #1 is arranged
(`OCR_SOURCE_BUCKET_NAME` env var wires it in once available).

```bash
cd infra
pip install -r requirements.txt
export OCR_AUDITOR_AWS_ACCOUNT_ID=...
export OCR_AUDITOR_AWS_REGION=...
cdk deploy
```

## Repo layout

```
ocr_auditor/            # core library (resolve, fetch, judge, graph, contracts)
api/                     # Lambda handler + Dockerfile
scripts/run_local.py     # CLI runner against local fixtures, no AWS needed
infra/                   # CDK stack
tests/                   # pytest suite + fixtures
docs/ocr-auditor-agent-spec.md   # the design spec this repo implements
```
