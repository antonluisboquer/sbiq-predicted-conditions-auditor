# Fixtures

These fixtures are a mix of **real, trimmed manifest shapes** and
**synthetic data**:

- `sample_manifest.json` — document and artifact entries for
  `c5f7e0a2-...` (Purchase Contract) and `53134a24-...` (EMD Docs) are
  trimmed/copied verbatim from the real
  `predicted-conditions/compiled_inputs/montes_10x/manifest.json` sample
  (same `document_id`s, `source.key`s, categories). A third document
  (`00000000-...-099`, "Appraisal Report") is entirely synthetic and
  deliberately has **no** matching `artifacts[]` entry, to exercise the
  "document found, but no OCR artifact" resolution path.
- `sample_document_requests.json` — specifications are real wording pulled
  from `predicted-conditions/data/canonical_doc_specs.json` (purchase
  contract items), paired with synthetic EMD/credit-report/rental-agreement
  specs to cover each resolution outcome:
  - resolvable (Purchase Contract, EMD Docs)
  - document found in manifest but no OCR artifact (Appraisal Report)
  - document_id not present in manifest at all (Credit Report)
  - no `document_ids` at all (Rental Agreement) — spec open question #4
- `ocr_texts/*.txt` — **entirely synthetic** OCR text. The real Tasktile
  OCR `.txt` format/content has not been inspected yet (spec open
  question #5); these fixtures only exercise the judge prompt/parsing
  plumbing, not real-world OCR noise/formatting.
