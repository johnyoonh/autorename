# Archival document normalization

`autorename process` is the generic, preview-first ingestion path for scanned and downloaded PDFs. The existing `rename`, `organize`, `undo`, and `config` routes remain available through the canonical `autorename` command during the compatibility migration.

## Ownership boundary

This repository owns deterministic document-processing behavior:

- searchable-PDF verification and optional OCRmyPDF execution;
- canonical filename analysis and rename execution;
- structured document classification;
- confidence/review decisions;
- SHA-256 collision checks;
- process/readiness state;
- generic routing mechanics and audit/cache storage.

Private destination mappings, machine paths, and scheduling do not belong here. Supply them through a routing file such as `~/.config/autorename/routing.yaml`, normally owned by private dotfiles.

## State machine

```text
DISCOVERED
   ↓
OCR_VERIFIED
   ↓
NAMING_VERIFIED
   ↓
CLASSIFIED
   ↓
ROUTE_READY
   ↓
ROUTED
```

The implementation exposes the combined result rather than persisting fragile filesystem xattrs. Uncertain files report `REVIEW`; newly copied files can report `DEFERRED`; exact same-content target collisions report `DUPLICATE`.

The important readiness predicate is equivalent to:

```text
ocr_ready
&& rename_ready
&& classification confidence is sufficient
&& !needs_review
```

A private route policy may impose a stricter classification threshold than the generic processing threshold.

## Output state

`autorename process -o json ...` emits one record per PDF similar to:

```json
{
  "path": "/path/to/20260909 USCIS Biometrics Appointment Notice.pdf",
  "sha256": "...",
  "state": "ROUTE_READY",
  "ocr": {
    "status": "ready",
    "method": "existing_text",
    "text_quality": 0.94
  },
  "rename": {
    "status": "ready",
    "changed": false,
    "canonical": true,
    "proposed_name": "20260909 USCIS Biometrics Appointment Notice.pdf"
  },
  "classification": {
    "category": "identity_immigration",
    "confidence": 0.97
  },
  "routing": {
    "ready": true,
    "needs_review": false,
    "reasons": []
  }
}
```

## Two OCR layers

The project distinguishes two jobs:

- `normalization.persistent_ocr`: external OCRmyPDF writes a searchable text layer into the PDF when needed.
- `pdf.ocr`: PaddleOCR can temporarily help the metadata model understand a scan. It does not itself make the source PDF searchable.

A ScanSnap profile that already produces a good searchable PDF should pass the embedded-text check and skip OCRmyPDF.

## Metadata cache and audit

The process engine uses SQLite at `normalization.audit_db` (default `~/.local/share/autorename/audit.sqlite3`). It is keyed by SHA-256 plus AI provider/model.

The actual PDF is still inspected for embedded text on every pass. Cached metadata only avoids repeated classification/LLM work when the content hash is unchanged. This keeps OCR readiness grounded in the current file while making an already-OCR'd, already-renamed repeat pass cheap.

The database also records process and route events for provenance and debugging. It is not the authoritative proof that a file is searchable; the current PDF is.

## Preview and apply

Preview is the default:

```bash
autorename process ~/Documents/DocumentInbox/00_inbox
```

Apply OCR/rename changes:

```bash
autorename process --apply ~/Documents/DocumentInbox/00_inbox
```

Recursive JSON output:

```bash
autorename process -r -o json ~/Documents/DocumentInbox/00_inbox
```

`normalization.min_file_age_seconds` protects against files that are still being copied or written by a scanner.

## Routing

Routing is deliberately driven by a separate private YAML policy:

```yaml
version: 1
readiness:
  require_searchable_pdf: true
  require_canonical_filename: true
  minimum_classification_confidence: 0.90
fallback:
  destination: review
routes:
  - category: identity_immigration
    destination: google_records_identity
destinations:
  google_records_identity:
    path: "${DOCUMENTS_GOOGLE}/10_identity"
  review:
    path: "${DOCUMENT_INBOX}/90_review"
```

Preview routing only:

```bash
autorename route --routing-config ~/.config/autorename/routing.yaml ~/Documents/DocumentInbox/00_inbox
```

Apply routing:

```bash
autorename route --apply --routing-config ~/.config/autorename/routing.yaml ~/Documents/DocumentInbox/00_inbox
```

For a single periodic ingestion job, process and route in one invocation:

```bash
autorename process --apply --route \
  --routing-config ~/.config/autorename/routing.yaml \
  ~/Documents/DocumentInbox/00_inbox
```

That avoids independent OCR, rename, and route watchers racing one another.

## Review gate

The model confidence is not sufficient by itself. The normalizer also validates:

- parseable primary document date;
- identifiable organization;
- non-generic document type;
- configured category;
- searchable embedded text after any applied persistent OCR;
- safe target filename/collision state.

Typical review reasons include:

- `missing_or_invalid_document_date`
- `missing_or_ambiguous_organization`
- `generic_or_missing_document_type`
- `low_text_quality`
- `low_model_confidence`
- `persistent_ocr_not_completed`
- `searchable_pdf_not_ready`
- `filename_collision_different_content`
- `classification_below_processing_threshold`
- `classification_below_routing_threshold`

## Duplicate policy

The process and route engines do not resolve same-name collisions by blindly adding `_(1)` or `_(2)`.

1. Source and target are SHA-256 hashed.
2. Matching bytes report `duplicate`; neither copy is deleted automatically.
3. Different bytes report a review collision.

Deduplication/deletion remains a separate reviewed action.

## Downloads organizer

`autorename organize` remains the backward-compatible age-based Downloads convenience workflow. The generic reusable boundary is now `process` plus `route`; future organizer policy can call those primitives without moving personal destinations into this public repository.
