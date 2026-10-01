"""
LangGraph wiring for the OCR Auditor:

    Fetch -> Judge -> Report

Kept as a straightforward linear graph rather than a ReAct/tool-calling
loop: this pipeline's control flow is fully deterministic end to end --
fetching OCR text is plain IO, and the only genuinely model-driven step is
the per-document judge call. A StateGraph still buys clean separation
between stages and a natural place to later add retries, parallel fan-out
per document, or a chunking fallback for oversized OCR text (spec
#audit-logic "Long-context handling") without restructuring callers.

NOTE: there is deliberately no "resolve" stage here. `AuditRequest` already
carries the exact `bucket`/`key` of the OCR artifact to fetch -- the caller
(predicted-conditions) is responsible for knowing where that is (it has its
own manifest access). See `contracts.py`'s module docstring, and
`scripts/manifest_to_audit_requests.py` for a dev/test-only helper that
derives `bucket`/`key` from an existing manifest.json + final_output.json
pair when you don't already have them handy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from .contracts import AuditReport, AuditRequest, DocumentAuditResult, SpecVerdict
from .judge import DEFAULT_MODEL, judge_document
from .ocr_fetcher import OCRTextFetchError, OCRTextFetcher


@dataclass
class PendingAudit:
    """Working state for one `AuditRequest` as it flows through fetch ->
    judge. Kept as a plain dataclass (rather than threading everything
    through `DocumentAuditResult` directly) so intermediate fields
    (ocr_text/fetch_error/etc.) don't leak into the pydantic output
    contract.
    """

    bucket: str
    key: str
    document_type: str
    specifications: list[str]
    ocr_text: Optional[str] = None
    ocr_is_partial: bool = False
    ocr_whisper_metadata: dict = field(default_factory=dict)
    fetch_error: Optional[str] = None
    verdicts: list[SpecVerdict] = field(default_factory=list)

    def to_result(self) -> DocumentAuditResult:
        return DocumentAuditResult(
            bucket=self.bucket,
            key=self.key,
            document_type=self.document_type,
            fetched=self.fetch_error is None,
            error=self.fetch_error,
            verdicts=self.verdicts,
        )


class AuditorState(TypedDict, total=False):
    audit_requests: list[AuditRequest]
    pending: list[PendingAudit]
    report: AuditReport


def _unresolved_verdicts(pending: PendingAudit) -> list[SpecVerdict]:
    """Used when a document couldn't be judged at all (fetch failed, or
    dry_run) -- every requested specification still gets a row, flagged
    unsatisfied at zero confidence (fail-safe: never claim "satisfied" for
    something that was never actually checked), so nothing silently
    disappears from the output. The *why* lives on the parent
    `DocumentAuditResult.error`, not repeated per-row."""
    return [
        SpecVerdict(
            specification=spec,
            verdict="unsatisfied",
            evidence_quote=None,
            confidence=0.0,
        )
        for spec in pending.specifications
    ]


def _prepare_node(state: AuditorState) -> AuditorState:
    pending = [
        PendingAudit(
            bucket=req.bucket,
            key=req.key,
            document_type=req.document_type,
            specifications=list(req.specifications_unsatisfied),
        )
        for req in state["audit_requests"]
    ]
    return {**state, "pending": pending}


def _make_fetch_node(fetcher: OCRTextFetcher):
    def _fetch_node(state: AuditorState) -> AuditorState:
        for item in state["pending"]:
            try:
                result = fetcher.fetch(item.bucket, item.key)
                item.ocr_text = result.text
                item.ocr_is_partial = result.is_partial
                item.ocr_whisper_metadata = result.whisper_metadata
            except OCRTextFetchError as exc:
                item.fetch_error = str(exc)
        return state

    return _fetch_node


def _make_judge_node(model: str, llm, dry_run: bool):
    def _judge_node(state: AuditorState) -> AuditorState:
        for item in state["pending"]:
            if item.fetch_error is not None:
                item.verdicts = _unresolved_verdicts(item)
                continue

            if dry_run:
                # Fetch succeeded; just skip the (costly) LLM judge call.
                item.verdicts = _unresolved_verdicts(item)
                continue

            item.verdicts = judge_document(
                document_type=item.document_type,
                ocr_text=item.ocr_text or "",
                specifications=item.specifications,
                model=model,
                llm=llm,
                is_partial=item.ocr_is_partial,
                whisper_metadata=item.ocr_whisper_metadata,
            )
        return state

    return _judge_node


def _summarize(pending: list[PendingAudit]) -> dict:
    counts = {"satisfied": 0, "unsatisfied": 0}
    for item in pending:
        for v in item.verdicts:
            counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return {
        "total_documents_audited": len(pending),
        "documents_fetched": sum(1 for p in pending if p.fetch_error is None),
        "documents_fetch_failed": sum(1 for p in pending if p.fetch_error is not None),
        "verdict_counts": counts,
    }


def _report_node(state: AuditorState) -> AuditorState:
    pending = state["pending"]
    report = AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        document_results=[p.to_result() for p in pending],
        summary=_summarize(pending),
    )
    return {**state, "report": report}


def build_graph(
    *,
    fetcher: OCRTextFetcher,
    model: str = DEFAULT_MODEL,
    llm=None,
    dry_run: bool = False,
):
    """Build and compile the fetch -> judge -> report StateGraph.

    `fetcher` is required explicitly (no default) so callers always make an
    intentional choice between `S3OCRTextFetcher` (real) and
    `LocalDirOCRTextFetcher` (local fixtures, see scripts/run_local.py).
    """
    graph = StateGraph(AuditorState)
    graph.add_node("prepare", _prepare_node)
    graph.add_node("fetch", _make_fetch_node(fetcher))
    graph.add_node("judge", _make_judge_node(model, llm, dry_run))
    graph.add_node("report", _report_node)

    graph.set_entry_point("prepare")
    graph.add_edge("prepare", "fetch")
    graph.add_edge("fetch", "judge")
    graph.add_edge("judge", "report")
    graph.add_edge("report", END)

    return graph.compile()


def run_audit(
    *,
    audit_requests: list[AuditRequest],
    fetcher: OCRTextFetcher,
    model: str = DEFAULT_MODEL,
    llm=None,
    dry_run: bool = False,
) -> AuditReport:
    """Convenience wrapper: build the graph, run it once, return the report.

    Used by both `api/main.py` (Lambda handler) and `scripts/run_local.py`
    (local CLI) so the two entry points share identical pipeline behavior.
    """
    app = build_graph(fetcher=fetcher, model=model, llm=llm, dry_run=dry_run)
    final_state = app.invoke({"audit_requests": audit_requests})
    return final_state["report"]
