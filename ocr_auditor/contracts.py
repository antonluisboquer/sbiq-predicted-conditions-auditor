"""
Pydantic models for the auditor's input and output data contracts, per
docs/ocr-auditor-agent-spec.md (#input-data-contract, #output-data-contract).

These are intentionally NOT imported from predicted-conditions — per the
spec's #deployment-shape section, this repo is decoupled from that one by
the JSON shapes documented here, not by a shared library. Field names/shapes
mirror predicted-conditions' `final_output.document_requests[]` and
Tasktile's `manifest.json` closely enough to parse them directly, but this
module is the independent source of truth for this repo.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Verdict = Literal["satisfied", "still_unsatisfied", "needs_human_review"]


class DocumentRequestInput(BaseModel):
    """One entry from predicted-conditions' `final_output.document_requests[]`,
    trimmed to the fields the auditor needs (spec #input-data-contract item 1).

    `document_ids` can legitimately be empty — some document_requests carry
    no document_ids at all (spec open question #4). Callers should treat
    that case as `needs_human_review` rather than erroring, since there is
    nothing to resolve/fetch against.
    """

    document_type: str
    document_category: str = ""
    document_ids: list[str] = Field(default_factory=list)
    guideline_reference: str = ""
    specifications: list[str] = Field(default_factory=list)


class ManifestDocument(BaseModel):
    """Trimmed view of one `manifest.documents[]` entry — just enough to
    cross-check the audited document's category against what the
    document_request expects (spec #input-data-contract item 2)."""

    id: str
    category_id: Optional[int] = None
    category_name: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: dict) -> "ManifestDocument":
        cat = raw.get("category") or {}
        return cls(
            id=raw.get("id", "") or "",
            category_id=cat.get("category_id"),
            category_name=cat.get("category_name"),
        )


class ManifestArtifact(BaseModel):
    """Trimmed view of one `manifest.artifacts[]` entry with `type == "ocr"`
    (spec #ocr-retrieval-mechanism). Entries of any other `type` are not
    modeled here — `from_raw` returns None for them."""

    type: str
    document_id: str
    bucket: str
    key: str

    @classmethod
    def from_raw(cls, raw: dict) -> Optional["ManifestArtifact"]:
        if raw.get("type") != "ocr":
            return None
        source = raw.get("source") or {}
        document_id = raw.get("document_id")
        bucket = source.get("bucket")
        key = source.get("key")
        if not (document_id and bucket and key):
            return None
        return cls(type=raw["type"], document_id=document_id, bucket=bucket, key=key)


class SpecVerdict(BaseModel):
    """Per-specification, per-document output row (spec #output-data-contract)."""

    document_id: Optional[str] = None
    document_type: str
    specification: str
    verdict: Verdict
    evidence_quote: Optional[str] = Field(
        default=None,
        description="Verbatim excerpt from the OCR text supporting the verdict, or null",
    )
    confidence: float = 0.0
    reasoning: str = ""


class DocumentAuditResult(BaseModel):
    """All verdicts produced for one resolved (document_type, document_id)
    pair, plus the resolution bookkeeping needed to explain unresolved
    documents (no manifest entry / no OCR artifact / category mismatch)."""

    document_request_document_type: str
    document_ids: list[str] = Field(default_factory=list)
    resolved: bool
    resolution_note: str = ""
    verdicts: list[SpecVerdict] = Field(default_factory=list)


class AuditReport(BaseModel):
    """Aggregate report rolled up across all document_requests passed in,
    meant to be diffed against the original pipeline's
    `document_requests[].specifications` / `satisfied_specifications` split
    (spec #output-data-contract)."""

    generated_at: str
    document_results: list[DocumentAuditResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
