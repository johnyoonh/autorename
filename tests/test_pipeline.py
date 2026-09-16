from __future__ import annotations

import json

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
            "categories": ["education", "finance_tax", "other"],
        },
        "output": {"date_format": "%Y%m%d"},
    }


def test_process_uses_cached_metadata_and_marks_ready(monkeypatch, tmp_path):
    pdf = tmp_path / "20260909 Example University Enrollment Confirmation.pdf"
    pdf.write_bytes(b"synthetic")
    metadata = NormalizationMetadata(
        organization="Example University",
        document_date="09.09.2026",
        document_type="Enrollment Confirmation",
        category="education",
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
        lambda path, quality, config: ExtractionResult(text="searchable document text", quality_score=0.94, sources=["text"]),
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
    assert state["classification"]["category"] == "education"
    assert state["routing"]["ready"] is True
    assert state["cache"]["metadata_hit"] is True


def test_route_uses_symbolic_category_destination(tmp_path):
    source = tmp_path / "20260909 Example University Confirmation.pdf"
    source.write_bytes(b"content")
    destination_root = tmp_path / "education"
    state = {
        "path": str(source),
        "sha256": "abc",
        "ocr": {"status": "ready"},
        "rename": {"canonical": True},
        "classification": {"category": "education", "confidence": 0.97},
        "routing": {"ready": True, "needs_review": False, "reasons": []},
    }
    routing = {
        "readiness": {
            "require_searchable_pdf": True,
            "require_canonical_filename": True,
            "minimum_classification_confidence": 0.90,
        },
        "fallback": {"destination": "review"},
        "routes": [{"category": "education", "destination": "education"}],
        "destinations": {
            "education": {"path": str(destination_root)},
            "review": {"path": str(tmp_path / "review")},
        },
    }

    result = _pipeline.route_document(state, routing, apply=False)

    assert result["status"] == "planned"
    assert result["route_ready"] is True
    assert result["destination"] == str(destination_root / source.name)


def test_route_accepts_private_alias_list_for_composite_category(tmp_path):
    source = tmp_path / "20260909 Example Bank Tax Statement.pdf"
    source.write_bytes(b"content")
    state = {
        "path": str(source),
        "sha256": "abc",
        "ocr": {"status": "ready"},
        "rename": {"canonical": True},
        "classification": {"category": "finance_tax", "confidence": 0.97},
        "routing": {"ready": True, "needs_review": False, "reasons": []},
    }
    financial = tmp_path / "financial"
    routing = {
        "readiness": {"minimum_classification_confidence": 0.90},
        "fallback": {"destination": "review"},
        "routes": [{"categories": ["financial", "finance", "tax"], "destination": "financial"}],
        "destinations": {
            "financial": {"path": str(financial)},
            "review": {"path": str(tmp_path / "review")},
        },
    }

    result = _pipeline.route_document(state, routing, apply=False)

    assert result["status"] == "planned"
    assert result["route_ready"] is True
    assert result["destination"] == str(financial / source.name)


def test_route_below_threshold_goes_to_review(tmp_path):
    source = tmp_path / "document.pdf"
    source.write_bytes(b"content")
    state = {
        "path": str(source),
        "sha256": "abc",
        "ocr": {"status": "ready"},
        "rename": {"canonical": True},
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


def test_apply_route_moves_one_file_and_writes_jsonl_audit(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"content")
    destination_root = tmp_path / "education"
    audit_path = tmp_path / "state" / "routing.jsonl"
    state = {
        "path": str(source),
        "sha256": "abc",
        "ocr": {"status": "ready"},
        "rename": {"canonical": True},
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
        "audit": {"enabled": True, "path": str(audit_path)},
    }

    result = _pipeline.route_document(state, routing, apply=True)

    assert result["status"] == "routed"
    assert not source.exists()
    assert (destination_root / source.name).read_bytes() == b"content"
    record = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert record["sha256"] == "abc"
    assert record["status"] == "routed"
