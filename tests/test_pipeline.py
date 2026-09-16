from __future__ import annotations

from pathlib import Path

import _pipeline
from _normalization import NormalizationMetadata, PersistentOcrResult, ReviewAssessment
from _pdf_utils import ExtractionResult


class FakeStore:
    def __init__(self, metadata):
        self.metadata = metadata
        self.events = []

    def get_metadata(self, digest, provider, model):
        return self.metadata

    def put_metadata(self, digest, provider, model, metadata):
        self.metadata = metadata

    def event(self, event_type, payload, digest=None):
        self.events.append((event_type, payload, digest))


def _config():
    return {
        "ai": {"provider": "openai", "model": "test-model"},
        "pdf": {"max_pages": 3, "text_quality_threshold": 0.3, "ocr": False, "vision": False},
        "normalization": {
            "min_confidence": 0.85,
            "min_file_age_seconds": 0,
            "persistent_ocr": "auto",
            "categories": ["identity_immigration", "education", "other"],
        },
        "output": {"date_format": "%Y%m%d"},
    }


def test_process_uses_cached_metadata_and_marks_ready(monkeypatch, tmp_path):
    pdf = tmp_path / "20260909 USCIS Biometrics Appointment Notice.pdf"
    pdf.write_bytes(b"synthetic")
    metadata = NormalizationMetadata(
        organization="USCIS",
        document_date="09.09.2026",
        document_type="Biometrics Appointment Notice",
        category="identity_immigration",
        confidence=0.97,
    )
    store = FakeStore(metadata)

    monkeypatch.setattr(_pipeline, "sha256_file", lambda path: "abc123")
    monkeypatch.setattr(_pipeline, "_embedded_ocr_state", lambda path, config: ("ready", 0.94, "existing_text"))
    monkeypatch.setattr(
        _pipeline,
        "run_persistent_ocr",
        lambda path, config, apply=False: PersistentOcrResult("not_needed", False, 0.94),
    )
    monkeypatch.setattr(
        _pipeline,
        "_cached_extraction",
        lambda path, quality: ExtractionResult(text="searchable document text", quality_score=0.94, sources=["text"]),
    )
    monkeypatch.setattr(
        _pipeline,
        "assess_for_review",
        lambda metadata, extraction, config: ReviewAssessment(0.95, False, [], 0.94, 0.95, 0.97),
    )
    monkeypatch.setattr(_pipeline, "build_target_path", lambda path, metadata, config: str(pdf))

    state = _pipeline.process_document(str(pdf), _config(), str(tmp_path / "names.yaml"), store=store)

    assert state["state"] == "ROUTE_READY"
    assert state["ocr"]["status"] == "ready"
    assert state["rename"]["canonical"] is True
    assert state["classification"]["category"] == "identity_immigration"
    assert state["routing"]["ready"] is True
    assert state["cache"]["metadata_hit"] is True


def test_route_uses_symbolic_category_destination(tmp_path):
    source = tmp_path / "20260909 USCIS Notice.pdf"
    source.write_bytes(b"content")
    destination_root = tmp_path / "identity"
    state = {
        "path": str(source),
        "sha256": "abc",
        "classification": {"category": "identity_immigration", "confidence": 0.97},
        "routing": {"ready": True, "needs_review": False, "reasons": []},
    }
    routing = {
        "readiness": {"minimum_classification_confidence": 0.90},
        "fallback": {"destination": "review"},
        "routes": [{"category": "identity_immigration", "destination": "identity"}],
        "destinations": {
            "identity": {"path": str(destination_root)},
            "review": {"path": str(tmp_path / "review")},
        },
    }

    result = _pipeline.route_document(state, routing, apply=False)

    assert result["status"] == "planned"
    assert result["route_ready"] is True
    assert result["destination"] == str(destination_root / source.name)


def test_route_below_threshold_goes_to_review(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"content")
    state = {
        "path": str(source),
        "sha256": "abc",
        "classification": {"category": "education", "confidence": 0.80},
        "routing": {"ready": True, "needs_review": False, "reasons": []},
    }
    review_root = tmp_path / "review"
    routing = {
        "readiness": {"minimum_classification_confidence": 0.90},
        "fallback": {"destination": "review"},
        "routes": [{"category": "education", "destination": "education"}],
        "destinations": {
            "education": {"path": str(tmp_path / "education")},
            "review": {"path": str(review_root)},
        },
    }

    result = _pipeline.route_document(state, routing, apply=False)

    assert result["status"] == "planned_review"
    assert result["route_ready"] is False
    assert "classification_below_routing_threshold" in result["reasons"]
    assert result["destination"] == str(review_root / source.name)


def test_apply_route_moves_one_file(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"content")
    destination_root = tmp_path / "education"
    state = {
        "path": str(source),
        "sha256": "abc",
        "classification": {"category": "education", "confidence": 0.96},
        "routing": {"ready": True, "needs_review": False, "reasons": []},
    }
    routing = {
        "readiness": {"minimum_classification_confidence": 0.90},
        "fallback": {"destination": "review"},
        "routes": [{"category": "education", "destination": "education"}],
        "destinations": {
            "education": {"path": str(destination_root)},
            "review": {"path": str(tmp_path / "review")},
        },
    }

    result = _pipeline.route_document(state, routing, apply=True)

    assert result["status"] == "routed"
    assert not source.exists()
    assert (destination_root / source.name).read_bytes() == b"content"
