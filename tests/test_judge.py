from ocr_auditor.judge import JudgeResponse, RawVerdict, judge_document


class _FakeLLM:
    """Duck-typed stand-in for `ChatAnthropic(...).with_structured_output(...)`
    — just needs `.invoke(messages) -> JudgeResponse`."""

    def __init__(self, response: JudgeResponse):
        self._response = response
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        return self._response


def test_judge_document_maps_verdicts_back_to_specs_in_order():
    specs = ["spec A", "spec B"]
    fake = _FakeLLM(
        JudgeResponse(
            verdicts=[
                RawVerdict(
                    specification="spec B",
                    verdict="satisfied",
                    evidence_quote="quote B",
                    confidence=0.9,
                    reasoning="reason B",
                ),
                RawVerdict(
                    specification="spec A",
                    verdict="still_unsatisfied",
                    evidence_quote=None,
                    confidence=0.6,
                    reasoning="reason A",
                ),
            ]
        )
    )

    results = judge_document(
        document_type="Purchase Contract",
        document_id="doc-1",
        ocr_text="irrelevant for this test",
        specifications=specs,
        llm=fake,
    )

    assert [r.specification for r in results] == specs  # preserves input order
    assert results[0].verdict == "still_unsatisfied"
    assert results[1].verdict == "satisfied"
    assert results[1].evidence_quote == "quote B"


def test_judge_document_fails_safe_when_model_drops_a_spec():
    fake = _FakeLLM(JudgeResponse(verdicts=[]))

    results = judge_document(
        document_type="Purchase Contract",
        document_id="doc-1",
        ocr_text="irrelevant for this test",
        specifications=["spec A"],
        llm=fake,
    )

    assert len(results) == 1
    assert results[0].verdict == "needs_human_review"
    assert "did not include a verdict" in results[0].reasoning


def test_judge_document_returns_empty_list_for_no_specifications():
    fake = _FakeLLM(JudgeResponse(verdicts=[]))
    results = judge_document(
        document_type="Purchase Contract",
        document_id="doc-1",
        ocr_text="irrelevant",
        specifications=[],
        llm=fake,
    )
    assert results == []
    assert fake.last_messages is None  # never even called the model
