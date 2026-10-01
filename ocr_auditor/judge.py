"""
LLM adjudication step, per docs/ocr-auditor-agent-spec.md (#audit-logic).

Sends one document's full OCR text plus its unsatisfied specifications to
the model in a single structured-output call and parses the response into a
list of `SpecVerdict`, one per specification requested.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from .contracts import CONFIDENCE_THRESHOLD, SpecVerdict, Verdict
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# Long-context model: a 40-page appraisal's OCR text can be tens of
# thousands of tokens (spec #audit-logic "Long-context handling"). Override
# via the `model` kwarg / AUDITOR_MODEL env var (see api/main.py) if a
# different model is preferred.
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


class RawVerdict(BaseModel):
    specification: str = Field(
        description="Verbatim copy of the specification being adjudicated"
    )
    verdict: Verdict
    evidence_quote: Optional[str] = Field(
        default=None,
        description="Verbatim excerpt from the OCR text, or null if none applies",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    # Asked of the model (forces it to show its work, which measurably
    # improves verdict quality) but deliberately NOT exposed on the
    # `SpecVerdict` output contract -- see contracts.py. Still logged at
    # debug level so it's not lost entirely, just not part of the API
    # surface.
    reasoning: str


class JudgeResponse(BaseModel):
    verdicts: list[RawVerdict]


def _get_llm(model: str):
    from langchain_anthropic import ChatAnthropic

    # Default max_tokens (1024) is nowhere near enough once a document_request
    # has a couple dozen specs, each needing its own reasoning + quote --
    # hit in practice against Sahay's real 22-spec Appraisal Report request
    # (truncated tool call -> empty/invalid structured output). 8192 covers
    # every real sample checked so far; revisit if a document ever needs more.
    return ChatAnthropic(model=model, temperature=0, max_tokens=8192).with_structured_output(
        JudgeResponse
    )


def judge_document(
    *,
    document_type: str,
    ocr_text: str,
    specifications: list[str],
    model: str = DEFAULT_MODEL,
    llm=None,
    is_partial: bool = False,
    whisper_metadata: Optional[dict] = None,
) -> list[SpecVerdict]:
    """Adjudicate every specification for one document in a single LLM call.

    `llm` can be injected (anything with an `.invoke(messages) -> JudgeResponse`-
    shaped return) for testing without making real API calls — see
    tests/test_judge.py.

    `is_partial`/`whisper_metadata` (from `ocr_fetcher.OCRTextResult`) flag
    when Tasktile only OCR'd some of the document's pages, so the model is
    told not to treat "not mentioned in this text" as "not in the document"
    — see `prompts.PARTIAL_COVERAGE_NOTE_TEMPLATE`.
    """
    if not specifications:
        return []

    user_prompt = build_user_prompt(
        document_type=document_type,
        ocr_text=ocr_text,
        specifications=specifications,
        is_partial=is_partial,
        whisper_metadata=whisper_metadata,
    )

    chain = llm if llm is not None else _get_llm(model)
    response = chain.invoke([("system", SYSTEM_PROMPT), ("human", user_prompt)])

    by_spec = {v.specification: v for v in response.verdicts}
    results: list[SpecVerdict] = []
    for spec in specifications:
        raw = by_spec.get(spec)
        if raw is None:
            # Model dropped/reworded a spec rather than echoing it verbatim —
            # fail safe to human review instead of silently omitting it.
            logger.warning(
                "judge response missing verdict for spec on document_type=%s: %r",
                document_type,
                spec,
            )
            results.append(
                SpecVerdict(
                    specification=spec,
                    verdict="unsatisfied",
                    evidence_quote=None,
                    confidence=0.0,
                )
            )
            continue
        logger.debug("spec=%r verdict=%r reasoning=%r", spec, raw.verdict, raw.reasoning)
        results.append(
            SpecVerdict(
                specification=spec,
                verdict=_apply_confidence_gate(raw.verdict, raw.confidence),
                evidence_quote=raw.evidence_quote,
                confidence=raw.confidence,
            )
        )
    return results


def _apply_confidence_gate(verdict: Verdict, confidence: float) -> Verdict:
    """A "satisfied" verdict only survives if the model's own confidence
    clears CONFIDENCE_THRESHOLD; otherwise it's downgraded to "unsatisfied"
    rather than passed through. "unsatisfied" verdicts are never upgraded by
    this gate -- it only ever makes a verdict stricter, never looser."""
    if verdict == "satisfied" and confidence < CONFIDENCE_THRESHOLD:
        return "unsatisfied"
    return verdict
