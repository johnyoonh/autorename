# Archival document normalization

`normalize-documents.py` is an opt-in, preview-first ingestion path for scanned and downloaded PDFs. It does not change the existing `autorename-pdf.py rename` behavior.

## Pipeline

```text
PDF intake
  -> inspect existing searchable text
  -> persistent OCR with OCRmyPDF when needed
  -> text/PaddleOCR/vision extraction
  -> one structured AI metadata call
  -> confidence + field validation
  -> build YYYYMMDD ORGANIZATION DOCUMENT-TYPE.pdf
  -> SHA-256 collision check
  -> rename only when safe
```

The structured result also includes an archival category and explicitly stated actionable dates. Calendar creation is intentionally outside this command; action dates are output for a separate reviewed workflow.

## Two OCR layers

The project now distinguishes two different jobs:

- `normalization.persistent_ocr`: uses the external `ocrmypdf` command to write a searchable text layer into the PDF. In `auto` mode this only runs when existing embedded text quality is below `pdf.text_quality_threshold`.
- `pdf.ocr`: existing PaddleOCR support used to help the metadata model understand a scan. It does not persist OCR text into the source PDF.

For a ScanSnap profile already producing good searchable PDFs, persistent OCR normally reports `not_needed`.

## Configuration

Add or copy the `normalization` section from `config.yaml.example`.

Recommended starting point:

```yaml
pdf:
  ocr: "auto"
  vision: false
  text_quality_threshold: 0.3

normalization:
  persistent_ocr: "auto"
  ocrmypdf_command: "ocrmypdf"
  ocrmypdf_mode: "skip"
  ocrmypdf_args:
    - "--output-type"
    - "pdf"
  keep_original_before_ocr: false
  min_confidence: 0.85
  review_on_ocr_failure: true
```

`--mode skip` leaves pages that already contain text alone and OCRs pages that need it. Keeping `--output-type pdf` avoids an unnecessary PDF/A conversion when the goal is simply a searchable archival PDF.

OCRmyPDF is optional for the legacy renamer but required when the normalizer decides persistent OCR is necessary. Install it separately and make sure the `ocrmypdf` executable is available on `PATH`, or configure an absolute path with `normalization.ocrmypdf_command`.

## Preview

Preview is the default. It may call the configured AI provider, but it does not replace PDFs or rename files.

```bash
python normalize-documents.py ~/Documents/DocumentInbox/00_inbox
```

Recursive preview:

```bash
python normalize-documents.py -r ~/Documents/DocumentInbox/00_inbox
```

JSON is intended for automation:

```bash
python normalize-documents.py -o json ~/Documents/DocumentInbox/00_inbox
```

## Apply

After reviewing the preview:

```bash
python normalize-documents.py --apply ~/Documents/DocumentInbox/00_inbox
```

Apply can change the PDF bytes when persistent OCR is needed, then rename the file if it clears the review gate. Rename operations use the existing `.autorename-log.json` undo mechanism in the source directory.

If `normalization.keep_original_before_ocr` is enabled, the pre-OCR PDF is copied to `.autorename-originals/` (or the configured backup directory) before replacement.

## Review gate

The model's own confidence is not sufficient to rename automatically. The normalizer also validates:

- parseable primary document date;
- identifiable organization;
- non-generic document type;
- valid configured category;
- usable embedded/OCR text quality.

The final confidence is capped by the model estimate. Any failed required check produces `status: review` and no filename mutation.

Typical review reasons include:

- `missing_or_invalid_document_date`
- `missing_or_ambiguous_organization`
- `generic_or_missing_document_type`
- `low_text_quality`
- `low_model_confidence`
- `persistent_ocr_not_completed`
- `filename_collision_different_content`

## Duplicate policy

The normalizer does not use the legacy `_(1)`, `_(2)` suffix behavior for collisions.

When the proposed target filename already exists:

1. the source and target are hashed with SHA-256;
2. matching bytes produce `status: duplicate` and neither file is deleted;
3. different bytes produce `status: review`.

Deletion/deduplication is intentionally left to a later reviewed step.

## Categories

The default categories are:

- `identity_immigration`
- `finance_tax`
- `education`
- `medical`
- `vehicle_insurance`
- `housing`
- `employment`
- `legal`
- `other`

They are output as metadata for a downstream organizer. This command deliberately does not move files into cloud folders yet; renaming/classification can be validated independently before routing is automated.
