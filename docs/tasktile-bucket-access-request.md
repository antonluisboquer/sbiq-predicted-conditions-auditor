# Tasktile `tasktile-staging` bucket access — request & findings

Tracks spec open question #1
([`docs/ocr-auditor-agent-spec.md`](ocr-auditor-agent-spec.md#open-questions--risks)):
**the single biggest blocker** for this agent. This doc exists so the ask is
concrete enough to hand to whoever owns the Tasktile vendor relationship,
rather than "we need access to a bucket."

## What's confirmed (read-only diagnostic, no writes made)

Run from this project's existing AWS account (the same one
`predicted-conditions-agent-{dev,prod}` deploys into):

| Check | Result |
|---|---|
| Account used | `828351637694` (IAM user `amplify-masoud`) |
| `HeadBucket(tasktile-staging)` | `403 Forbidden` — bucket **exists**, region **`us-west-2`**, not owned by this account |
| `GetObject` on a real, known OCR artifact key* | `AccessDenied` |

\* Key tested:
`clients/cedb9bff-dc2f-4be5-9a7d-931dd3e78dcc/jobs/5dc75ab2-8d3c-40a7-bfe0-ea946c8496c8/blobs/5482ff8d-87fa-4196-836b-c052d612c653/ocr/c5f7e0a2-b8ba-4533-8404-db9ba33e0b36.txt`
— a real `manifest.artifacts[]` entry from
`compiled_inputs/montes_10x/manifest.json` in the `predicted-conditions`
repo, not a guess.

**Conclusion**: `tasktile-staging` is unambiguously owned by a different AWS
account (presumably Tasktile's own), and account `828351637694` currently
has zero grant to it — not a config/credentials bug on our side, a genuine
missing cross-account grant.

## What to ask for

Either of these unblocks `S3OCRTextFetcher` (already built to support both,
see `ocr_auditor/ocr_fetcher.py`):

**Option A — bucket policy grant (simpler, if Tasktile will do it)**
A statement on `tasktile-staging`'s bucket policy allowing:
```json
{
  "Effect": "Allow",
  "Principal": {"AWS": "arn:aws:iam::828351637694:root"},
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::tasktile-staging/clients/*/jobs/*/blobs/*/ocr/*"
}
```
(scoped to the `ocr/` prefix only — this agent never needs the original PDF
blobs, just the OCR `.txt` artifacts.) Pair with an identity-policy
statement on our side (already wired — set `OCR_SOURCE_BUCKET_NAME=tasktile-staging`
when deploying `infra/`).

**Option B — cross-account role to assume (if Tasktile prefers not to edit
their bucket policy directly)**
Tasktile creates an IAM role in their account trusting
`828351637694`, scoped to `s3:GetObject` on the same `ocr/*` prefix. Pass its
ARN as `OCR_SOURCE_BUCKET_ROLE_ARN` (wired in both `api/main.py` and
`infra/stacks/ocr_auditor_stack.py`).

**Option C — presigned URLs or a Tasktile API proxy** (fallback per the
original spec, not yet designed) — only pursue if A/B are off the table;
would need its own small integration layer in `ocr_fetcher.py`.

## Who to route this to

This repo has no explicit Tasktile contact on file (no code, docs, or
`CODEOWNERS` reference an owner). What's visible from `predicted-conditions`'
git history / AWS setup:
- AWS account `828351637694` credentials currently used locally belong to
  IAM user **`amplify-masoud`** — whoever controls that account (or that
  user) likely already knows who manages the Tasktile vendor relationship,
  or can grant themselves enough access to find out.
- Git history shows **Fintor** engineers (`@fintor.com` addresses) as the
  other contributors to `predicted-conditions` — worth checking with them
  for an existing Tasktile account-team / support contact before reaching
  out cold.

## Once access is granted

1. Set `OCR_SOURCE_BUCKET_NAME` (or `OCR_SOURCE_BUCKET_ROLE_ARN`) per above.
2. Swap `LocalDirOCRTextFetcher` for `S3OCRTextFetcher` in whatever's
   exercising the pipeline (already the default in `api/main.py`).
3. Pull a handful of **real** `.txt` OCR artifacts and replace the synthetic
   fixtures in `tests/fixtures/ocr_texts/` — spec open question #5 (real OCR
   format/page-boundary behavior) is still unverified and should inform the
   final judge prompt in `ocr_auditor/prompts.py`.
4. Re-run `scripts/run_local.py` against real data before trusting verdicts
   in production.
