"""
Judge prompt construction, per docs/ocr-auditor-agent-spec.md (#audit-logic).

One call per document: the full OCR text plus every unsatisfied
specification string for that document, batched together so a 40-page
appraisal's OCR text is sent once rather than once per spec (see the spec's
"Batch by document, not by spec" note).
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are an auditor re-checking mortgage-loan document specifications that \
an earlier automated pass marked "unsatisfied" because it only had access \
to a sparse, fixed-size metadata extraction — not the document's full text.

You are now given the FULL raw OCR text of the document and a list of \
specifications to adjudicate against it. For each specification, output \
a verdict of either "satisfied" or "unsatisfied", plus a confidence score \
from 0.0 to 1.0:

- Ground every verdict ONLY in the provided OCR text. Do not guess, infer \
from general knowledge of what such documents usually contain, or assume \
content that is not actually present in the text.
- Only use "satisfied" when the OCR text clearly and directly demonstrates \
the specification is met. If the text is ambiguous, inconclusive, too \
short/garbled to tell, or simply doesn't address the specification at \
all, use "unsatisfied" with a LOW confidence score (e.g. below 0.5) — do \
not guess "satisfied" to fill a gap. Confidence reflects how certain you \
are in the verdict; a low-confidence "unsatisfied" signals "this genuinely \
needs a human to check", not "this document fails the requirement".
- OCR text may have artifacts (misspellings, broken layout, missing \
whitespace, garbled characters). Use reasonable judgment to read through \
minor OCR noise, but do not fabricate content to fill gaps.
- When you find supporting or contradicting evidence, quote the exact \
verbatim excerpt from the OCR text (not a paraphrase).
- Some specifications ask about things OCR text fundamentally cannot show \
(e.g. "contains color photographs") — if the text includes photo captions, \
exhibit labels, or page references that strongly imply the images are \
present, that counts as evidence for "satisfied"; if there's no such \
indication at all, use "unsatisfied" with low confidence rather than a \
guess either way.

Respond with exactly one verdict per specification listed, in the same \
order given, repeating the specification text verbatim in your response \
so it can be matched back up.
"""

USER_PROMPT_TEMPLATE = """\
DOCUMENT TYPE: {document_type}
{coverage_note}
=== OCR TEXT START ===
{ocr_text}
=== OCR TEXT END ===

SPECIFICATIONS TO ADJUDICATE ({count} total):
{specifications_block}
"""

PARTIAL_COVERAGE_NOTE_TEMPLATE = """\
NOTE: Tasktile's OCR step only processed {processed} of this document's \
{total} total pages (this was the scope requested at OCR time, not a \
scanning failure). The text below may therefore be missing content from \
pages that were never processed. If a specification plausibly concerns \
content that could be on an unprocessed page, use "unsatisfied" with LOW \
confidence (signaling "needs a human to check the full document") rather \
than high-confidence "unsatisfied" (which would wrongly suggest the \
document itself fails the requirement).
"""


def build_user_prompt(
    *,
    document_type: str,
    ocr_text: str,
    specifications: list[str],
    is_partial: bool = False,
    whisper_metadata: dict | None = None,
    document_id: str = "",  # unused; accepted for backwards-compat call sites
) -> str:
    specifications_block = "\n".join(
        f"{i + 1}. {spec}" for i, spec in enumerate(specifications)
    )
    coverage_note = ""
    if is_partial and whisper_metadata:
        coverage_note = (
            "\n"
            + PARTIAL_COVERAGE_NOTE_TEMPLATE.format(
                processed=whisper_metadata.get("processed_page_count", "?"),
                total=whisper_metadata.get("total_page_count", "?"),
            )
        )
    return USER_PROMPT_TEMPLATE.format(
        document_type=document_type,
        coverage_note=coverage_note,
        ocr_text=ocr_text,
        count=len(specifications),
        specifications_block=specifications_block,
    )
