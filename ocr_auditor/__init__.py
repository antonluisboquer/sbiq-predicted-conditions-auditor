"""ocr_auditor — the OCR Auditor Agent.

See docs/ocr-auditor-agent-spec.md for the full design this package
implements. In one line: given an OCR artifact's exact S3 location
(`bucket`/`key`) plus predicted-conditions' unsatisfied document
specifications for that document, fetch the full raw OCR text and
re-adjudicate the specs against it instead of the sparse structured
`metadata` fields predicted-conditions is limited to.
"""

from .contracts import (
    AuditReport,
    AuditRequest,
    DocumentAuditResult,
    SpecVerdict,
    Verdict,
)

__all__ = [
    "AuditReport",
    "AuditRequest",
    "DocumentAuditResult",
    "SpecVerdict",
    "Verdict",
]
