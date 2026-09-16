"""Document-normalization primitives for archival PDF ingestion.

This module intentionally keeps the existing rename pipeline backward-compatible.
It adds an opt-in normalization layer with:

* persistent searchable-PDF OCR via the external ``ocrmypdf`` CLI;
* structured AI metadata for organization, category, confidence, and action dates;
* deterministic confidence/review gating; and
* SHA-256 collision checks so filename collisions are not silently suffixed.

The higher-level CLI lives in ``normalize-documents.py``.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from _ai_processing import build_image_content, get_instructor_client
from _document_processing import parse_document_date
from _pdf_utils import ExtractionResult, assess_text_quality, extract_text
from _utils import DEFAULT_DATE, UNKNOWN_VALUE, is_valid_filename, normalize_unicode, sanitize_filename


DEFAULT_CATEGORIES = [
    "identity_immigration",
    "finance_tax",
    "education",
    "medical",
    "vehicle_insurance",
    "housing",
    "employment",
    "legal",
    "other",
]


class ActionDate(BaseModel):
    """A date in a document that may require a future action or calendar event."""

    date: str = Field(description="Date in YYYY-MM-DD when known, otherwise empty")
    time: str = Field(default="", description="Local time in HH:MM when explicitly stated, otherwise empty")
    title: str = Field(description="Short event/deadline title")
    action_required: bool = Field(default=True)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class NormalizationMetadata(BaseModel):
    """Metadata used by the safe document-normalization path."""

    organization: str = Field(
        description="Main external organization/counterparty; concise name without legal suffix when possible"
    )
    document_date: str = Field(
        description="Primary issue/statement/document date in dd.mm.YYYY format; empty if absent"
    )
    document_type: str = Field(description="Short descriptive document type in the configured output language")
    category: str = Field(description="One category from the configured normalization category list")
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Model-estimated confidence that organization/date/type/category are jointly correct",
    )
    action_dates: list[ActionDate] = Field(default_factory=list)


@dataclass
class ReviewAssessment:
    confidence: float
    needs_review: bool
    reasons: list[str] = field(default_factory=list)
    source_quality: float = 0.0
    heuristic_confidence: float = 0.0
    model_confidence: float = 0.0


@dataclass
class PersistentOcrResult:
    status: Literal["disabled", "not_needed", "planned", "applied", "unavailable", "failed"]
    needed: bool
    quality_before: float
    quality_after: float | None = None
    warning: str | None = None
    sha256_before: str | None = None
    sha256_after: str | None = None
    backup_path: str | None = None


@dataclass
class CollisionResult:
    kind: Literal["none", "same_path", "duplicate", "conflict"]
    target_path: str
    source_sha256: str | None = None
    target_sha256: str | None = None


def _combined_text(extraction: ExtractionResult) -> str:
    parts: list[str] = []
    if extraction.text.strip():
        parts.append(extraction.text)
    if extraction.ocr_text.strip():
        if parts:
            parts.append("\n--- OCR Text ---\n")
        parts.append(extraction.ocr_text)
    return "\n".join(parts)


def _normalization_prompt(config: dict) -> str:
    normalization = config.get("normalization", {})
    categories = normalization.get("categories") or DEFAULT_CATEGORIES
    language = config.get("output", {}).get("language", "English")
    own_company = config.get("company", {}).get("name", "")
    category_text = ", ".join(str(c) for c in categories)

    prompt = (
        "Extract archival document metadata. OCR may be noisy. Do not invent missing dates or organizations.\n\n"
        "organization: identify the primary external issuer/counterparty/agency. Use a concise recognizable name. "
        "If there is no identifiable organization, return an empty string.\n"
        "document_date: identify the primary issue, statement, letter, invoice, or notice date; return dd.mm.YYYY. "
        "Do not substitute a future appointment/deadline for the document date. If absent, return empty.\n"
        f"document_type: return a short descriptive type/subject in {language}.\n"
        f"category: choose exactly one of: {category_text}.\n"
        "confidence: estimate confidence from 0 to 1 for the combined organization/date/type/category extraction.\n"
        "action_dates: include explicitly stated appointments, response deadlines, expiration dates, hearings, "
        "payment deadlines, or other future/actionable dates. Do not copy the document date unless it is itself actionable.\n"
    )
    if own_company:
        prompt += f'\nThe user organization is "{own_company}"; do not use it as the external organization unless the document is actually issued by it.'
    extension = config.get("prompt_extension", "")
    if extension:
        prompt += f"\n\nAdditional instructions:\n{extension}"
    return prompt


def extract_normalization_metadata(extraction: ExtractionResult, config: dict) -> NormalizationMetadata | None:
    """Extract richer normalization metadata with one structured LLM call."""
    text = _combined_text(extraction)
    images = extraction.images
    if not text.strip() and not images:
        return None

    client = get_instructor_client(config)
    provider = config["ai"]["provider"]
    user_content: object
    if images:
        image_content = build_image_content(images, provider)
        user_content = [
            {"type": "text", "text": f"Normalize this document. Extracted text follows:\n\n{text}"},
            *image_content,
        ]
    else:
        user_content = f"Normalize this document. Extracted text follows:\n\n{text}"

    kwargs = {
        "model": config["ai"]["model"],
        "response_model": NormalizationMetadata,
        "max_retries": config["ai"].get("max_retries", 2),
        "temperature": config["ai"].get("temperature", 0.0),
        "messages": [
            {"role": "system", "content": _normalization_prompt(config)},
            {"role": "user", "content": user_content},
        ],
    }
    if provider == "anthropic":
        kwargs["max_tokens"] = 1600
    return client.chat.completions.create(**kwargs)


def clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def assess_for_review(
    metadata: NormalizationMetadata,
    extraction: ExtractionResult,
    config: dict,
) -> ReviewAssessment:
    """Conservatively combine model confidence, source quality, and field validity.

    Model confidence is not trusted by itself. A deterministic heuristic verifies
    that the fields required for a stable archival filename are present and that
    the source text/OCR is usable. The final confidence is capped by the model's
    own estimate.
    """
    normalization = config.get("normalization", {})
    threshold = float(normalization.get("min_confidence", 0.85))
    allowed_categories = set(normalization.get("categories") or DEFAULT_CATEGORIES)

    source_quality = max(
        clamp_confidence(extraction.quality_score),
        clamp_confidence(assess_text_quality(extraction.ocr_text)),
    )
    source_conf = 0.72 + (0.28 * source_quality) if (extraction.text.strip() or extraction.ocr_text.strip()) else 0.0

    org = metadata.organization.strip()
    doc_type = metadata.document_type.strip()
    parsed_date = parse_document_date(metadata.document_date)
    category = metadata.category.strip()

    org_conf = 0.95 if org and org.lower() not in {"unknown", "n/a", "none"} and len(org) >= 2 else 0.35
    date_conf = 0.97 if parsed_date else 0.20
    type_conf = 0.95 if doc_type and doc_type.lower() not in {"unknown", "document", "n/a"} else 0.45
    category_conf = 0.95 if category in allowed_categories else 0.40

    heuristic = (
        0.20 * source_conf
        + 0.20 * org_conf
        + 0.25 * date_conf
        + 0.25 * type_conf
        + 0.10 * category_conf
    )
    model_conf = clamp_confidence(metadata.confidence)
    final_conf = round(min(heuristic, model_conf), 3)

    reasons: list[str] = []
    if not parsed_date:
        reasons.append("missing_or_invalid_document_date")
    if org_conf < 0.8:
        reasons.append("missing_or_ambiguous_organization")
    if type_conf < 0.8:
        reasons.append("generic_or_missing_document_type")
    if category_conf < 0.8:
        reasons.append("invalid_category")
    if source_quality < float(config.get("pdf", {}).get("text_quality_threshold", 0.3)):
        reasons.append("low_text_quality")
    if model_conf < threshold:
        reasons.append("low_model_confidence")
    if final_conf < threshold:
        reasons.append("below_auto_rename_threshold")

    return ReviewAssessment(
        confidence=final_conf,
        needs_review=bool(reasons),
        reasons=list(dict.fromkeys(reasons)),
        source_quality=round(source_quality, 3),
        heuristic_confidence=round(heuristic, 3),
        model_confidence=round(model_conf, 3),
    )


def build_archival_filename(
    organization: str,
    document_date: datetime.date | None,
    document_type: str,
    config: dict,
) -> str:
    """Build the same stable filename shape as the legacy renamer without mutating."""
    date_format = config.get("output", {}).get("date_format", "%Y%m%d")
    org = sanitize_filename(normalize_unicode(organization.strip()))
    doc_type = sanitize_filename(normalize_unicode(document_type.strip()))
    if not is_valid_filename(org):
        org = UNKNOWN_VALUE
    if not is_valid_filename(doc_type):
        doc_type = UNKNOWN_VALUE

    date_text = document_date.strftime(date_format) if document_date else DEFAULT_DATE
    base = f"{date_text} {org} {doc_type}"
    max_base = 244
    if len(base) > max_base:
        overflow = len(base) - max_base
        org = org[: max(1, len(org) - overflow)].rstrip() or UNKNOWN_VALUE
        base = f"{date_text} {org} {doc_type}"
    return normalize_unicode(f"{base}.pdf")


def build_target_path(pdf_path: str, metadata: NormalizationMetadata, config: dict) -> str:
    parsed_date = parse_document_date(metadata.document_date)
    filename = build_archival_filename(metadata.organization, parsed_date, metadata.document_type, config)
    return os.path.join(os.path.dirname(pdf_path), filename)


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def inspect_collision(source_path: str, target_path: str) -> CollisionResult:
    """Distinguish exact duplicates from same-name/different-content conflicts."""
    source_abs = os.path.abspath(source_path)
    target_abs = os.path.abspath(target_path)
    if source_abs == target_abs:
        return CollisionResult(kind="same_path", target_path=target_abs)
    if not os.path.exists(target_abs):
        return CollisionResult(kind="none", target_path=target_abs)
    if not os.path.isfile(target_abs):
        return CollisionResult(kind="conflict", target_path=target_abs)

    source_hash = sha256_file(source_abs)
    target_hash = sha256_file(target_abs)
    kind = "duplicate" if source_hash == target_hash else "conflict"
    return CollisionResult(
        kind=kind,
        target_path=target_abs,
        source_sha256=source_hash,
        target_sha256=target_hash,
    )


def persistent_ocr_decision(mode: object, quality: float, threshold: float) -> bool:
    """Return whether persistent OCR should be attempted for this file."""
    if mode is True:
        return True
    if mode is False or mode is None:
        return False
    normalized = str(mode).strip().lower()
    if normalized in {"false", "off", "disabled", "never", "none"}:
        return False
    if normalized in {"true", "on", "always"}:
        return True
    if normalized == "auto":
        return quality < threshold
    raise ValueError(f"Unsupported normalization.persistent_ocr value: {mode!r}")


def _resolve_ocrmypdf_command(config: dict) -> str | None:
    command = str(config.get("normalization", {}).get("ocrmypdf_command", "ocrmypdf")).strip()
    if not command:
        return None
    if os.path.isabs(command):
        return command if os.path.isfile(command) else None
    return shutil.which(command)


def run_persistent_ocr(pdf_path: str, config: dict, apply: bool = False) -> PersistentOcrResult:
    """Optionally add a searchable text layer using OCRmyPDF.

    The source is never passed as both input and output. OCR is written to a
    temporary sibling file, checked for nonzero output, and only then atomically
    replaces the source. Preview mode reports ``planned`` without mutation.
    """
    pdf_cfg = config.get("pdf", {})
    normalization = config.get("normalization", {})
    max_pages = int(pdf_cfg.get("max_pages", 3))
    threshold = float(pdf_cfg.get("text_quality_threshold", 0.3))
    mode = normalization.get("persistent_ocr", "auto")

    _, quality = extract_text(pdf_path, max_pages=max_pages)
    try:
        needed = persistent_ocr_decision(mode, quality, threshold)
    except ValueError as exc:
        return PersistentOcrResult(status="failed", needed=False, quality_before=quality, warning=str(exc))

    if not needed:
        status = "disabled" if str(mode).lower() in {"false", "off", "disabled", "never", "none"} or mode is False else "not_needed"
        return PersistentOcrResult(status=status, needed=False, quality_before=quality)

    command = _resolve_ocrmypdf_command(config)
    if command is None:
        return PersistentOcrResult(
            status="unavailable",
            needed=True,
            quality_before=quality,
            warning="OCRmyPDF is required for persistent OCR but was not found",
        )
    if not apply:
        return PersistentOcrResult(status="planned", needed=True, quality_before=quality)

    original_hash = sha256_file(pdf_path)
    directory = os.path.dirname(os.path.abspath(pdf_path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".autorename-ocr-", suffix=".pdf", dir=directory)
    os.close(fd)
    try:
        os.unlink(temp_path)
        ocr_mode = str(normalization.get("ocrmypdf_mode", "skip")).strip() or "skip"
        extra_args = normalization.get("ocrmypdf_args", [])
        if not isinstance(extra_args, list):
            extra_args = []
        cmd = [command, "--mode", ocr_mode, *[str(x) for x in extra_args], pdf_path, temp_path]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            detail = (completed.stderr or completed.stdout or "OCRmyPDF failed").strip()
            return PersistentOcrResult(
                status="failed",
                needed=True,
                quality_before=quality,
                warning=detail[-1200:],
                sha256_before=original_hash,
            )

        _, quality_after = extract_text(temp_path, max_pages=max_pages)
        backup_path = None
        if bool(normalization.get("keep_original_before_ocr", False)):
            backup_dir = str(normalization.get("original_backup_dir", ".autorename-originals"))
            if not os.path.isabs(backup_dir):
                backup_dir = os.path.join(directory, backup_dir)
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, os.path.basename(pdf_path))
            if os.path.exists(backup_path):
                stem, ext = os.path.splitext(backup_path)
                backup_path = f"{stem}-{original_hash[:12]}{ext}"
            shutil.copy2(pdf_path, backup_path)

        os.replace(temp_path, pdf_path)
        after_hash = sha256_file(pdf_path)
        return PersistentOcrResult(
            status="applied",
            needed=True,
            quality_before=quality,
            quality_after=quality_after,
            sha256_before=original_hash,
            sha256_after=after_hash,
            backup_path=backup_path,
        )
    except Exception as exc:
        logging.exception("Persistent OCR failed for %s", pdf_path)
        return PersistentOcrResult(
            status="failed",
            needed=True,
            quality_before=quality,
            warning=str(exc),
            sha256_before=original_hash,
        )
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
