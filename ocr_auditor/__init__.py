"""ocr_auditor — the OCR Auditor Agent.

See docs/ocr-auditor-agent-spec.md for the full design this package
implements. In one line: given predicted-conditions' unsatisfied document
specifications plus a Tasktile manifest, fetch each document's full raw OCR
text and re-adjudicate the specs against it instead of the sparse structured
`metadata` fields predicted-conditions is limited to.
"""

from .contracts import (
    AuditReport,
    DocumentAuditResult,
    DocumentRequestInput,
    SpecVerdict,
    Verdict,
)

__all__ = [
    "AuditReport",
    "DocumentAuditResult",
    "DocumentRequestInput",
    "SpecVerdict",
    "Verdict",
]
