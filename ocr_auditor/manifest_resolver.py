"""
Resolves the document_ids referenced by an unsatisfied specification's
document_request to their OCR `.txt` artifact locations, per
docs/ocr-auditor-agent-spec.md (#ocr-retrieval-mechanism).

Also cross-checks the resolved document's manifest category against the
document_request's declared `document_type` as a sanity check — a mismatch
doesn't block resolution (category naming conventions differ between this
pipeline's `document_type` strings and Tasktile's `category_name`, e.g.
"Appraisal Report" vs. "Appraisal"), but is surfaced in
`category_mismatch_note` so a human reviewer can catch upstream mapping
drift rather than silently trusting a wrong document.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import DocumentRequestInput, ManifestArtifact, ManifestDocument


@dataclass
class ResolvedDocument:
    """Resolution outcome for a single document_id."""

    document_id: str
    bucket: str | None = None
    key: str | None = None
    category_name: str | None = None
    resolved: bool = False
    reason: str = ""  # populated when resolved is False
    category_mismatch_note: str = ""


@dataclass
class ResolutionResult:
    document_type: str
    resolved_documents: list[ResolvedDocument] = field(default_factory=list)

    @property
    def any_resolved(self) -> bool:
        return any(d.resolved for d in self.resolved_documents)


def _index_manifest(
    manifest: dict,
) -> tuple[dict[str, ManifestDocument], dict[str, ManifestArtifact]]:
    docs_by_id: dict[str, ManifestDocument] = {}
    for raw in manifest.get("documents", []) or []:
        doc = ManifestDocument.from_raw(raw)
        if doc.id:
            docs_by_id[doc.id] = doc

    artifacts_by_doc_id: dict[str, ManifestArtifact] = {}
    for raw in manifest.get("artifacts", []) or []:
        artifact = ManifestArtifact.from_raw(raw)
        if artifact:
            artifacts_by_doc_id[artifact.document_id] = artifact

    return docs_by_id, artifacts_by_doc_id


def resolve_document_request(
    request: DocumentRequestInput,
    manifest: dict,
) -> ResolutionResult:
    """Resolve every document_id on `request` to its OCR artifact location.

    A request with no document_ids at all yields an empty
    `resolved_documents` list — per spec open question #4, callers (see
    `graph.py`) treat that as `needs_human_review` since there's nothing to
    audit against.

    A manifest with no top-level `artifacts` key at all (the "documents-only"
    payload shape flagged in the spec's open question #2) resolves every
    document_id to `resolved=False` with an explanatory reason, rather than
    raising — roughly 40% of real sampled cases have this shape today.
    """
    docs_by_id, artifacts_by_doc_id = _index_manifest(manifest)
    has_artifacts_key = "artifacts" in manifest

    result = ResolutionResult(document_type=request.document_type)

    for doc_id in request.document_ids:
        manifest_doc = docs_by_id.get(doc_id)
        artifact = artifacts_by_doc_id.get(doc_id)

        resolved = ResolvedDocument(document_id=doc_id)

        if manifest_doc is None:
            resolved.reason = f"document_id {doc_id!r} not found in manifest.documents[]"
            result.resolved_documents.append(resolved)
            continue

        resolved.category_name = manifest_doc.category_name
        if (
            manifest_doc.category_name
            and request.document_type
            and manifest_doc.category_name.strip().lower()
            != request.document_type.strip().lower()
        ):
            resolved.category_mismatch_note = (
                f"manifest category {manifest_doc.category_name!r} does not "
                f"match requested document_type {request.document_type!r}"
            )

        if artifact is None:
            if not has_artifacts_key:
                resolved.reason = (
                    "manifest has no top-level 'artifacts' key at all — this "
                    "is the 'documents-only' manifest shape flagged in the "
                    "spec's open question #2; OCR text cannot be resolved "
                    "for any document in this manifest"
                )
            else:
                resolved.reason = (
                    f"no OCR artifact (type=='ocr') found in manifest.artifacts[] "
                    f"for document_id {doc_id!r}"
                )
            result.resolved_documents.append(resolved)
            continue

        resolved.bucket = artifact.bucket
        resolved.key = artifact.key
        resolved.resolved = True
        result.resolved_documents.append(resolved)

    return result
