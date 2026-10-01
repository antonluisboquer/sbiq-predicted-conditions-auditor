from ocr_auditor.contracts import AuditRequest


def test_audit_request_defaults():
    req = AuditRequest(bucket="tasktile-staging", key="a/b/c.txt")
    assert req.document_type == ""
    assert req.specifications_unsatisfied == []


def test_audit_request_accepts_specifications_unsatisfied_shape():
    """Real production document_requests use 'specifications_unsatisfied'."""
    raw = {
        "bucket": "tasktile-staging",
        "key": "clients/.../ocr/1893b393-b59b-4195-a181-05c01c4efac9.txt",
        "document_type": "Appraisal Report",
        "specifications_unsatisfied": ["spec C", "spec D"],
    }
    req = AuditRequest.model_validate(raw)
    assert req.specifications_unsatisfied == ["spec C", "spec D"]
    assert req.document_type == "Appraisal Report"


def test_audit_request_accepts_plain_specifications_alias():
    """'specifications' (plain list) is accepted as an alias for
    'specifications_unsatisfied', for convenience/older samples."""
    raw = {
        "bucket": "tasktile-staging",
        "key": "a/b/c.txt",
        "document_type": "Purchase Contract",
        "specifications": ["spec A"],
    }
    req = AuditRequest.model_validate(raw)
    assert req.specifications_unsatisfied == ["spec A"]


def test_audit_request_specifications_unsatisfied_takes_precedence():
    raw = {
        "bucket": "tasktile-staging",
        "key": "a/b/c.txt",
        "specifications": ["should be ignored"],
        "specifications_unsatisfied": ["spec A"],
    }
    req = AuditRequest.model_validate(raw)
    assert req.specifications_unsatisfied == ["spec A"]
