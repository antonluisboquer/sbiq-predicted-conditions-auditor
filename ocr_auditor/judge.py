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

from .contracts import SpecVerdict, Verdict
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
    reasoning: str


class JudgeResponse(BaseModel):
    verdicts: list[RawVerdict]


def _get_llm(model: str):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=model, temperature=0).with_structured_output(JudgeResponse)


def judge_document(
    *,
    document_type: str,
    document_id: str,
    ocr_text: str,
    specifications: list[str],
    model: str = DEFAULT_MODEL,
    llm=None,
) -> list[SpecVerdict]:
    """Adjudicate every specification for one document in a single LLM call.

    `llm` can be injected (anything with an `.invoke(messages) -> JudgeResponse`-
    shaped return) for testing without making real API calls — see
    tests/test_judge.py.
    """
    if not specifications:
        return []

    user_prompt = build_user_prompt(
        document_type=document_type,
        document_id=document_id,
        ocr_text=ocr_text,
        specifications=specifications,
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
                "judge response missing verdict for spec on document_id=%s: %r",
                document_id,
                spec,
            )
            results.append(
                SpecVerdict(
                    document_id=document_id,
                    document_type=document_type,
                    specification=spec,
                    verdict="needs_human_review",
                    evidence_quote=None,
                    confidence=0.0,
                    reasoning="model response did not include a verdict for this specification",
                )
            )
            continue
        results.append(
            SpecVerdict(
                document_id=document_id,
                document_type=document_type,
                specification=spec,
                verdict=raw.verdict,
                evidence_quote=raw.evidence_quote,
                confidence=raw.confidence,
                reasoning=raw.reasoning,
            )
        )
    return results
