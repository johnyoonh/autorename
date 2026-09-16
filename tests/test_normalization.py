from __future__ import annotations

import datetime

from _normalization import (
    NormalizationMetadata,
    assess_for_review,
    build_archival_filename,
    inspect_collision,
    persistent_ocr_decision,
)
from _pdf_utils import ExtractionResult


def _config(**normalization_overrides):
    normalization = {
        "min_confidence": 0.85,
        "categories": [
            "identity_immigration",
            "finance_tax",
            "education",
            "medical",
            "vehicle_insurance",
            "housing",
            "employment",
            "legal",
            "other",
        ],
    }
    normalization.update(normalization_overrides)
    return {
        "pdf": {"text_quality_threshold": 0.3},
        "output": {"date_format": "%Y%m%d"},
        "normalization": normalization,
    }


def test_persistent_ocr_auto_only_for_low_quality():
    assert persistent_ocr_decision("auto", 0.1, 0.3) is True
    assert persistent_ocr_decision("auto", 0.3, 0.3) is False
    assert persistent_ocr_decision("always", 1.0, 0.3) is True
    assert persistent_ocr_decision("never", 0.0, 0.3) is False


def test_archival_filename_matches_stable_shape():
    name = build_archival_filename(
        "USCIS",
        datetime.date(2026, 9, 2),
        "Biometrics Appointment Notice",
        _config(),
    )
    assert name == "20260902 USCIS Biometrics Appointment Notice.pdf"


def test_review_gate_accepts_complete_high_confidence_metadata():
    metadata = NormalizationMetadata(
        organization="USCIS",
        document_date="02.09.2026",
        document_type="Biometrics Appointment Notice",
        category="identity_immigration",
        confidence=0.97,
    )
    extraction = ExtractionResult(
        text="USCIS appointment notice " * 40,
        quality_score=0.95,
        sources=["text"],
    )

    assessment = assess_for_review(metadata, extraction, _config())

    assert assessment.needs_review is False
    assert assessment.confidence >= 0.85
    assert assessment.reasons == []


def test_review_gate_rejects_missing_date_even_with_high_model_confidence():
    metadata = NormalizationMetadata(
        organization="USCIS",
        document_date="",
        document_type="Biometrics Appointment Notice",
        category="identity_immigration",
        confidence=0.99,
    )
    extraction = ExtractionResult(
        text="USCIS appointment notice " * 40,
        quality_score=0.95,
        sources=["text"],
    )

    assessment = assess_for_review(metadata, extraction, _config())

    assert assessment.needs_review is True
    assert "missing_or_invalid_document_date" in assessment.reasons


def test_collision_identifies_exact_duplicate(tmp_path):
    source = tmp_path / "scan.pdf"
    target = tmp_path / "20260902 USCIS Notice.pdf"
    content = b"same bytes"
    source.write_bytes(content)
    target.write_bytes(content)

    collision = inspect_collision(str(source), str(target))

    assert collision.kind == "duplicate"
    assert collision.source_sha256 == collision.target_sha256


def test_collision_does_not_treat_same_name_different_content_as_duplicate(tmp_path):
    source = tmp_path / "scan.pdf"
    target = tmp_path / "20260902 USCIS Notice.pdf"
    source.write_bytes(b"first document")
    target.write_bytes(b"second document")

    collision = inspect_collision(str(source), str(target))

    assert collision.kind == "conflict"
    assert collision.source_sha256 != collision.target_sha256
