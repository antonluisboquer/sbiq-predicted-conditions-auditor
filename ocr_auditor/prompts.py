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
specifications to adjudicate against it. For each specification:

- Decide independently whether the OCR text demonstrates the specification \
IS satisfied, IS NOT satisfied, or the text is inconclusive/ambiguous \
(needs_human_review).
- Ground every verdict ONLY in the provided OCR text. Do not guess, infer \
from general knowledge of what such documents usually contain, or assume \
content that is not actually present in the text.
- OCR text may have artifacts (misspellings, broken layout, missing \
whitespace, garbled characters). Use reasonable judgment to read through \
minor OCR noise, but do not fabricate content to fill gaps.
- When you find supporting or contradicting evidence, quote the exact \
verbatim excerpt from the OCR text (not a paraphrase).
- Some specifications ask about things OCR text fundamentally cannot show \
(e.g. "contains color photographs") — if the text includes photo captions, \
exhibit labels, or page references that strongly imply the images are \
present, that counts as evidence; if there's no such indication at all, \
verdict should be needs_human_review rather than a guess either way.
- If the OCR text is too short, garbled, or simply does not address a \
specification at all, verdict should be "needs_human_review", not a guess.

Respond with exactly one verdict per specification listed, in the same \
order given, repeating the specification text verbatim in your response \
so it can be matched back up.
"""

USER_PROMPT_TEMPLATE = """\
DOCUMENT TYPE: {document_type}
DOCUMENT ID: {document_id}

=== FULL OCR TEXT START ===
{ocr_text}
=== FULL OCR TEXT END ===

SPECIFICATIONS TO ADJUDICATE ({count} total):
{specifications_block}
"""


def build_user_prompt(
    *,
    document_type: str,
    document_id: str,
    ocr_text: str,
    specifications: list[str],
) -> str:
    specifications_block = "\n".join(
        f"{i + 1}. {spec}" for i, spec in enumerate(specifications)
    )
    return USER_PROMPT_TEMPLATE.format(
        document_type=document_type,
        document_id=document_id,
        ocr_text=ocr_text,
        count=len(specifications),
        specifications_block=specifications_block,
    )
