"""
LangGraph wiring for the OCR Auditor, mirroring the architecture diagram in
docs/ocr-auditor-agent-spec.md (#architecture):

    Resolver -> Fetcher -> Judge -> Report

Kept as a straightforward linear graph rather than a ReAct/tool-calling
loop: unlike predicted-conditions' multi-step orchestrator, this pipeline's
control flow is fully deterministic end to end — resolving document_ids and
fetching OCR text are plain lookups/IO, and the only genuinely model-driven
step is the per-document judge call. A StateGraph still buys clean
separation between the four stages and a natural place to later add
retries, parallel fan-out per document, or a chunking fallback for
oversized OCR text (spec #audit-logic "Long-context handling") without
restructuring callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from .contracts import AuditReport, DocumentAuditResult, DocumentRequestInput, SpecVerdict
from .judge import DEFAULT_MODEL, judge_document
from .manifest_resolver import resolve_document_request
from .ocr_fetcher import OCRTextFetchError, OCRTextFetcher


@dataclass
class PendingAudit:
    """Working state for one (document_request, document_id) pair as it
    flows through resolve -> fetch -> judge. Kept as a plain dataclass
    (rather than threading everything through `DocumentAuditResult`
    directly) so intermediate fields (bucket/key/ocr_text/fetch_error) don't
    leak into the pydantic output contract.
    """

    document_type: str
    document_id: Optional[str]
    specifications: list[str]
    resolved: bool
    resolution_note: str = ""
    bucket: Optional[str] = None
    key: Optional[str] = None
    ocr_text: Optional[str] = None
    fetch_error: Optional[str] = None
    verdicts: list[SpecVerdict] = field(default_factory=list)

    def to_result(self) -> DocumentAuditResult:
        return DocumentAuditResult(
            document_request_document_type=self.document_type,
            document_ids=[self.document_id] if self.document_id else [],
            resolved=self.resolved and self.fetch_error is None,
            resolution_note=self.fetch_error or self.resolution_note,
            verdicts=self.verdicts,
        )


class AuditorState(TypedDict, total=False):
    document_requests: list[DocumentRequestInput]
    manifest: dict
    pending: list[PendingAudit]
    report: AuditReport


def _unresolved_verdicts(pending: PendingAudit, reason: str) -> list[SpecVerdict]:
    return [
        SpecVerdict(
            document_id=pending.document_id,
            document_type=pending.document_type,
            specification=spec,
            verdict="needs_human_review",
            evidence_quote=None,
            confidence=0.0,
            reasoning=reason,
        )
        for spec in pending.specifications
    ]


def _resolve_node(state: AuditorState) -> AuditorState:
    manifest = state["manifest"]
    pending: list[PendingAudit] = []

    for request in state["document_requests"]:
        if not request.document_ids:
            # Spec open question #4: no document_ids at all -> nothing to
            # resolve/fetch against, flag the whole request for review.
            pending.append(
                PendingAudit(
                    document_type=request.document_type,
                    document_id=None,
                    specifications=list(request.specifications),
                    resolved=False,
                    resolution_note="document_request has no document_ids",
                )
            )
            continue

        resolution = resolve_document_request(request, manifest)
        for resolved in resolution.resolved_documents:
            note = resolved.reason or resolved.category_mismatch_note
            pending.append(
                PendingAudit(
                    document_type=request.document_type,
                    document_id=resolved.document_id,
                    specifications=list(request.specifications),
                    resolved=resolved.resolved,
                    resolution_note=note,
                    bucket=resolved.bucket,
                    key=resolved.key,
                )
            )

    return {**state, "pending": pending}


def _make_fetch_node(fetcher: OCRTextFetcher):
    def _fetch_node(state: AuditorState) -> AuditorState:
        for item in state["pending"]:
            if not item.resolved:
                continue
            try:
                item.ocr_text = fetcher.fetch(item.bucket, item.key)
            except OCRTextFetchError as exc:
                item.fetch_error = str(exc)
        return state

    return _fetch_node


def _make_judge_node(model: str, llm, dry_run: bool):
    def _judge_node(state: AuditorState) -> AuditorState:
        for item in state["pending"]:
            if not item.resolved or item.fetch_error is not None:
                reason = item.fetch_error or item.resolution_note or "document not resolved"
                item.verdicts = _unresolved_verdicts(item, reason)
                continue

            if dry_run:
                item.verdicts = _unresolved_verdicts(
                    item, "dry-run: judge (LLM) step skipped"
                )
                continue

            item.verdicts = judge_document(
                document_type=item.document_type,
                document_id=item.document_id or "",
                ocr_text=item.ocr_text or "",
                specifications=item.specifications,
                model=model,
                llm=llm,
            )
        return state

    return _judge_node


def _summarize(pending: list[PendingAudit]) -> dict:
    counts = {"satisfied": 0, "still_unsatisfied": 0, "needs_human_review": 0}
    for item in pending:
        for v in item.verdicts:
            counts[v.verdict] = counts.get(v.verdict, 0) + 1
    return {
        "total_documents_audited": len(pending),
        "documents_resolved": sum(1 for p in pending if p.resolved and p.fetch_error is None),
        "documents_unresolved": sum(
            1 for p in pending if not (p.resolved and p.fetch_error is None)
        ),
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
    """Build and compile the resolve -> fetch -> judge -> report StateGraph.

    `fetcher` is required explicitly (no default) so callers always make an
    intentional choice between `S3OCRTextFetcher` (real, requires access not
    yet arranged per spec open question #1) and `LocalDirOCRTextFetcher`
    (local fixtures, see scripts/run_local.py).
    """
    graph = StateGraph(AuditorState)
    graph.add_node("resolve", _resolve_node)
    graph.add_node("fetch", _make_fetch_node(fetcher))
    graph.add_node("judge", _make_judge_node(model, llm, dry_run))
    graph.add_node("report", _report_node)

    graph.set_entry_point("resolve")
    graph.add_edge("resolve", "fetch")
    graph.add_edge("fetch", "judge")
    graph.add_edge("judge", "report")
    graph.add_edge("report", END)

    return graph.compile()


def run_audit(
    *,
    document_requests: list[DocumentRequestInput],
    manifest: dict,
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
    final_state = app.invoke(
        {"document_requests": document_requests, "manifest": manifest}
    )
    return final_state["report"]
