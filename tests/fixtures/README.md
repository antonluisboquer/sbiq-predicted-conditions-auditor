# Fixtures

- `sample_audit_requests.json` — the engine's real input shape
  (`bucket`/`key`/`document_type`/`specifications_unsatisfied`). Three
  entries: Purchase Contract and EMD Docs keys match real `.txt` files in
  `ocr_texts/` (fetchable), Appraisal Report's key does not (exercises the
  fetch-failure path). Specifications are real wording pulled from
  `predicted-conditions/data/canonical_doc_specs.json` (purchase contract
  items), paired with synthetic EMD/appraisal specs.
- `ocr_texts/*.txt` — **entirely synthetic** OCR text, just enough to
  exercise the judge prompt/parsing plumbing (not real-world OCR
  noise/formatting — see `ocr_auditor/ocr_fetcher.py`'s module docstring
  for what real Tasktile OCR artifacts actually look like).
- `sample_final_output.json` + `sample_manifest.json` — used only by
  `tests/test_manifest_to_audit_requests.py`, to test the dev/test-only
  `scripts/manifest_to_audit_requests.py` converter (NOT used by the core
  engine). `sample_manifest.json`'s Purchase Contract / EMD Docs entries
  are trimmed/copied verbatim from the real
  `predicted-conditions/compiled_inputs/montes_10x/manifest.json` sample
  (same `document_id`s, `source.key`s, categories, including a confirmed
  `artifacts[]` entry for each). A third document
  (`00000000-...-099`, "Appraisal Report") is synthetic and deliberately
  has **no** matching `artifacts[]` entry AND a non-derivable `source.key`,
  to exercise the "genuinely can't resolve an OCR location" conversion
  path. `sample_final_output.json` also references a `document_id` not
  present in the manifest at all (Credit Report) and a request with no
  `document_ids` at all (Rental Agreement).
- `sahay_appraisal_request_live.json` — real production `document_request`
  data, kept local-only (gitignored) per PII handling — see repo root
  `.gitignore` / chat history for provenance. **Do not commit this file.**
