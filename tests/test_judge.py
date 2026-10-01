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
                    verdict="unsatisfied",
                    evidence_quote=None,
                    confidence=0.6,
                    reasoning="reason A",
                ),
            ]
        )
    )

    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant for this test",
        specifications=specs,
        llm=fake,
    )

    assert [r.specification for r in results] == specs  # preserves input order
    assert results[0].verdict == "unsatisfied"
    assert results[1].verdict == "satisfied"
    assert results[1].evidence_quote == "quote B"
    # `reasoning` is used internally but never exposed on the output contract.
    assert not hasattr(results[0], "reasoning")


def test_judge_document_downgrades_low_confidence_satisfied_to_unsatisfied():
    """A "satisfied" verdict below CONFIDENCE_THRESHOLD (0.8) is forced to
    "unsatisfied" -- never passed through as a satisfied call just because
    the model said so."""
    fake = _FakeLLM(
        JudgeResponse(
            verdicts=[
                RawVerdict(
                    specification="spec A",
                    verdict="satisfied",
                    evidence_quote="weak evidence",
                    confidence=0.5,
                    reasoning="not fully sure",
                ),
            ]
        )
    )

    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant",
        specifications=["spec A"],
        llm=fake,
    )

    assert results[0].verdict == "unsatisfied"
    assert results[0].confidence == 0.5  # confidence score itself is untouched
    assert results[0].evidence_quote == "weak evidence"


def test_judge_document_keeps_high_confidence_satisfied():
    fake = _FakeLLM(
        JudgeResponse(
            verdicts=[
                RawVerdict(
                    specification="spec A",
                    verdict="satisfied",
                    evidence_quote="strong evidence",
                    confidence=0.8,
                    reasoning="clear",
                ),
            ]
        )
    )

    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant",
        specifications=["spec A"],
        llm=fake,
    )

    assert results[0].verdict == "satisfied"  # exactly at threshold -> keeps


def test_judge_document_never_upgrades_unsatisfied_regardless_of_confidence():
    """The confidence gate only ever makes a verdict stricter -- a model-
    reported "unsatisfied" is never flipped to "satisfied", however high
    its confidence."""
    fake = _FakeLLM(
        JudgeResponse(
            verdicts=[
                RawVerdict(
                    specification="spec A",
                    verdict="unsatisfied",
                    evidence_quote="clear contradicting evidence",
                    confidence=1.0,
                    reasoning="clear",
                ),
            ]
        )
    )

    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant",
        specifications=["spec A"],
        llm=fake,
    )

    assert results[0].verdict == "unsatisfied"


def test_judge_document_fails_safe_when_model_drops_a_spec():
    fake = _FakeLLM(JudgeResponse(verdicts=[]))

    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant for this test",
        specifications=["spec A"],
        llm=fake,
    )

    assert len(results) == 1
    assert results[0].verdict == "unsatisfied"
    assert results[0].confidence == 0.0


def test_judge_document_returns_empty_list_for_no_specifications():
    fake = _FakeLLM(JudgeResponse(verdicts=[]))
    results = judge_document(
        document_type="Purchase Contract",
        ocr_text="irrelevant",
        specifications=[],
        llm=fake,
    )
    assert results == []
    assert fake.last_messages is None  # never even called the model
