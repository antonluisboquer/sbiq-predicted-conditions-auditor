"""
Pydantic models for the auditor's input and output data contracts.

Simplified "direct key" contract: the caller (predicted-conditions) already
knows exactly which OCR artifact backs a given document_request -- it has
its own manifest access -- so this repo no longer resolves document_ids
against a manifest itself. The input is just the OCR artifact's exact S3
location (`bucket` + `key`) plus the specifications to adjudicate against
it; there is no manifest-resolution step in this pipeline at all.

(An earlier version of this contract took `document_type` +
`document_ids` + a full `manifest.json` and resolved OCR locations itself
-- see git history / `scripts/manifest_to_audit_requests.py` for that
derivation logic, now kept only as a local dev/test convenience for
building `AuditRequest`s out of existing manifest+final_output fixtures.)
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

Verdict = Literal["satisfied", "unsatisfied"]

# Purely a pass-through tag: the auditor never reads or branches on this --
# it's just carried from AuditRequest to the matching DocumentAuditResult so
# the caller (predicted-conditions) can tell which borrower's copy of a
# document a given result row belongs to, for loan files where both the
# primary borrower and a coborrower each submit their own copy of the same
# document_type (e.g. two separate Bank Statement audits in one batch).
Borrower = Literal["primary", "coborrower"]

# A verdict can only be "satisfied" when the judge's own confidence meets
# this bar; anything below it is forced to "unsatisfied" regardless of what
# the model said, so a shaky/ambiguous call never slips through as a pass.
# There is deliberately no third "needs_human_review" value on the output
# contract -- low confidence on an "unsatisfied" verdict IS the human-review
# signal (see judge.py).
CONFIDENCE_THRESHOLD = 0.8


class AuditRequest(BaseModel):
    """One document to audit: its exact OCR artifact location plus the
    specifications an earlier sparse-metadata pass marked unsatisfied.

    `specifications_unsatisfied` matches the field name used by real
    production `document_request`s; `specifications` (plain list of
    strings) is also accepted as an alias for convenience/older samples.
    """

    bucket: str
    key: str
    document_type: str = ""
    specifications_unsatisfied: list[str] = Field(default_factory=list)
    borrower: Optional[Borrower] = Field(
        default=None,
        description="Opt-in, purely a pass-through -- see Borrower's docstring above.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "specifications_unsatisfied" not in data and "specifications" in data:
            data["specifications_unsatisfied"] = data["specifications"]
        return data


class SpecVerdict(BaseModel):
    """Per-specification output row. Deliberately lean: `reasoning` is used
    internally by the judge step (helps the model think, and is logged on
    failure) but is NOT part of this contract -- callers get the verdict,
    how confident it is, and the verbatim OCR evidence backing it, nothing
    more verbose than that."""

    specification: str
    verdict: Verdict
    confidence: float = 0.0
    evidence_quote: Optional[str] = Field(
        default=None,
        description="Verbatim excerpt from the OCR text supporting the verdict, or null",
    )


class DocumentAuditResult(BaseModel):
    """All verdicts produced for one `AuditRequest`, plus fetch bookkeeping
    so a failed S3 fetch is distinguishable from a genuinely-judged
    document. `verdicts` is always exactly one row per specification that
    was asked about -- every specification is persisted in the output
    regardless of its verdict (nothing is filtered out), so a caller never
    has to guess whether a missing spec means "satisfied" or "we didn't
    check"."""

    bucket: str
    key: str
    document_type: str = ""
    borrower: Optional[Borrower] = None
    fetched: bool
    error: Optional[str] = None
    verdicts: list[SpecVerdict] = Field(default_factory=list)


class AuditReport(BaseModel):
    """Aggregate report rolled up across all `AuditRequest`s passed in."""

    generated_at: str
    document_results: list[DocumentAuditResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
