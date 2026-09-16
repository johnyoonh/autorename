"""Idempotent document processing and routing for archival PDFs.

Generic policy belongs here: verify searchable text, canonical naming,
classification, readiness, collision safety, routing, and audit/cache state.
Personal destinations and scheduling belong outside this repository.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import time
from dataclasses import asdict
from typing import Iterable

import yaml

from _document_processing import generate_batch_id, harmonize_company_name, parse_document_date, rename_invoice
from _normalization import (
    NormalizationMetadata,
    assess_for_review,
    build_target_path,
    extract_normalization_metadata,
    inspect_collision,
    run_persistent_ocr,
    sha256_file,
)
from _pdf_utils import ExtractionResult, extract_content, extract_text


STATE_SCHEMA = 1


def _expand(value):
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, str):
        return os.path.expanduser(os.path.expandvars(value))
    return value


def load_routing_config(path: str) -> dict:
    with open(os.path.expanduser(path), "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("routing config must be a YAML mapping")
    return _expand(data)


def collect_pdfs(paths: Iterable[str], recursive: bool = False) -> list[str]:
    files: list[str] = []
    for raw in paths:
        path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isfile(path):
            if path.lower().endswith(".pdf"):
                files.append(path)
            continue
        if not os.path.isdir(path):
            continue
        if recursive:
            for root, _, names in os.walk(path):
                for name in names:
                    if name.lower().endswith(".pdf") and not name.startswith("."):
                        files.append(os.path.join(root, name))
        else:
            for name in os.listdir(path):
                candidate = os.path.join(path, name)
                if os.path.isfile(candidate) and name.lower().endswith(".pdf") and not name.startswith("."):
                    files.append(candidate)
    return sorted(dict.fromkeys(files))


def _audit_db_path(config: dict) -> str:
    configured = config.get("normalization", {}).get("audit_db", "~/.local/share/autorename/audit.sqlite3")
    return os.path.abspath(os.path.expanduser(os.path.expandvars(str(configured))))


class AuditStore:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata_cache (
                sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (sha256, provider, model)
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                sha256 TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get_metadata(self, digest: str, provider: str, model: str) -> NormalizationMetadata | None:
        row = self.db.execute(
            "SELECT metadata_json FROM metadata_cache WHERE sha256=? AND provider=? AND model=?",
            (digest, provider, model),
        ).fetchone()
        if not row:
            return None
        try:
            return NormalizationMetadata.model_validate_json(row[0])
        except Exception:
            return None

    def put_metadata(self, digest: str, provider: str, model: str, metadata: NormalizationMetadata) -> None:
        self.db.execute(
            """
            INSERT INTO metadata_cache (sha256, provider, model, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sha256, provider, model)
            DO UPDATE SET metadata_json=excluded.metadata_json, created_at=excluded.created_at
            """,
            (
                digest,
                provider,
                model,
                metadata.model_dump_json(),
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )
        self.db.commit()

    def event(self, event_type: str, payload: dict, digest: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO events (created_at, sha256, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (
                dt.datetime.now(dt.timezone.utc).isoformat(),
                digest,
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.db.commit()


def _canonical_category(value: str) -> str:
    return value.strip().casefold().replace(" ", "_").replace("-", "_")


def _category_matches(category: str, rule: dict) -> bool:
    """Match stable public categories against exact or human-friendly private aliases."""
    normalized = _canonical_category(category)
    values: list[str] = []
    if rule.get("category") is not None:
        values.append(str(rule["category"]))
    aliases = rule.get("categories")
    if isinstance(aliases, list):
        values.extend(str(value) for value in aliases)

    candidates = {_canonical_category(value) for value in values if str(value).strip()}
    if normalized in candidates:
        return True
    tokens = set(normalized.split("_"))
    return any(candidate in tokens for candidate in candidates if "_" not in candidate)


def _file_is_stable(path: str, config: dict) -> tuple[bool, str | None]:
    seconds = int(config.get("normalization", {}).get("min_file_age_seconds", 30))
    if seconds <= 0:
        return True, None
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError as exc:
        return False, str(exc)
    if age < seconds:
        return False, f"file_younger_than_{seconds}s"
    return True, None


def _embedded_ocr_state(path: str, config: dict) -> tuple[str, float, str]:
    max_pages = int(config.get("pdf", {}).get("max_pages", 3))
    threshold = float(config.get("pdf", {}).get("text_quality_threshold", 0.3))
    text, quality = extract_text(path, max_pages=max_pages)
    if text.strip() and quality >= threshold:
        return "ready", quality, "existing_text"
    return "needed", quality, "missing_or_low_quality_text"


def _cached_extraction(path: str, quality: float, config: dict) -> ExtractionResult:
    max_pages = int(config.get("pdf", {}).get("max_pages", 3))
    text, measured = extract_text(path, max_pages=max_pages)
    return ExtractionResult(text=text, quality_score=max(quality, measured), sources=["text"])


def process_document(
    path: str,
    config: dict,
    yaml_path: str,
    *,
    apply: bool = False,
    store: AuditStore | None = None,
) -> dict:
    """Ensure OCR/name/classification state and return a machine-readable record."""
    path = os.path.abspath(path)
    state = {
        "schema": STATE_SCHEMA,
        "path": path,
        "sha256": None,
        "state": "DISCOVERED",
        "ocr": {"status": "unknown", "method": None, "text_quality": 0.0},
        "rename": {"status": "unknown", "changed": False, "canonical": False, "proposed_name": None},
        "classification": {"category": None, "confidence": 0.0},
        "routing": {"ready": False, "needs_review": True, "reasons": []},
        "action_dates": [],
        "cache": {"metadata_hit": False},
        "warnings": [],
    }

    stable, stable_reason = _file_is_stable(path, config)
    if not stable:
        state["state"] = "DEFERRED"
        state["routing"]["reasons"] = [stable_reason or "file_not_stable"]
        return state

    provider = config["ai"]["provider"]
    model = config["ai"]["model"]
    normalization = config.get("normalization", {})
    min_confidence = float(normalization.get("min_confidence", 0.85))

    digest = sha256_file(path)
    state["sha256"] = digest
    ocr_status, embedded_quality, ocr_method = _embedded_ocr_state(path, config)
    state["ocr"] = {"status": ocr_status, "method": ocr_method, "text_quality": round(embedded_quality, 3)}

    cached_metadata = store.get_metadata(digest, provider, model) if store else None

    persistent = run_persistent_ocr(path, config, apply=apply)
    state["ocr"]["persistent"] = asdict(persistent)
    if persistent.warning:
        state["warnings"].append(persistent.warning)

    if persistent.status == "applied":
        digest = sha256_file(path)
        state["sha256"] = digest
        ocr_status, embedded_quality, ocr_method = _embedded_ocr_state(path, config)
        state["ocr"].update(
            {
                "status": ocr_status,
                "method": "ocrmypdf" if ocr_status == "ready" else ocr_method,
                "text_quality": round(embedded_quality, 3),
            }
        )
        cached_metadata = store.get_metadata(digest, provider, model) if store else None

    if cached_metadata is not None:
        metadata = cached_metadata
        state["cache"]["metadata_hit"] = True
        extraction = _cached_extraction(path, embedded_quality, config)
    else:
        extraction = extract_content(path, config)
        if extraction.warnings:
            state["warnings"].extend(extraction.warnings)
        if not (extraction.text.strip() or extraction.ocr_text.strip() or extraction.images):
            state["state"] = "REVIEW"
            state["routing"]["reasons"] = ["no_content_extracted"]
            if store:
                store.event("process", state, digest)
            return state
        metadata = extract_normalization_metadata(extraction, config)
        if metadata is None:
            state["state"] = "REVIEW"
            state["routing"]["reasons"] = ["metadata_extraction_failed"]
            if store:
                store.event("process", state, digest)
            return state
        organization = harmonize_company_name(metadata.organization, yaml_path, config)
        metadata = metadata.model_copy(update={"organization": organization})
        if store:
            store.put_metadata(digest, provider, model, metadata)

    assessment = assess_for_review(metadata, extraction, config)
    category_confidence = round(min(float(metadata.confidence), assessment.confidence), 3)
    state["classification"] = {
        "category": _canonical_category(metadata.category),
        "confidence": category_confidence,
    }
    state["action_dates"] = [item.model_dump() for item in metadata.action_dates]

    target_path = build_target_path(path, metadata, config)
    proposed_name = os.path.basename(target_path)
    canonical = os.path.basename(path) == proposed_name
    state["rename"] = {
        "status": "ready" if canonical else "needed",
        "changed": False,
        "canonical": canonical,
        "proposed_name": proposed_name,
    }

    reasons = list(assessment.reasons)
    if persistent.needed and persistent.status in {"unavailable", "failed"} and bool(normalization.get("review_on_ocr_failure", True)):
        reasons.append("persistent_ocr_not_completed")
    if state["ocr"]["status"] != "ready":
        reasons.append("searchable_pdf_not_ready")
    if category_confidence < min_confidence:
        reasons.append("classification_below_processing_threshold")

    collision = inspect_collision(path, target_path)
    if not canonical and collision.kind == "duplicate":
        state["state"] = "DUPLICATE"
        state["rename"]["status"] = "duplicate"
        state["rename"]["duplicate_of"] = collision.target_path
        reasons.append("exact_duplicate_target_exists")
    elif not canonical and collision.kind == "conflict":
        reasons.append("filename_collision_different_content")

    reasons = list(dict.fromkeys(reasons))

    if apply and not canonical and not reasons and collision.kind == "none":
        undo_log = os.path.join(os.path.dirname(path), ".autorename-log.json")
        renamed = rename_invoice(
            path,
            metadata.organization,
            parse_document_date(metadata.document_date),
            metadata.document_type,
            config,
            undo_log_path=undo_log,
            batch_id=generate_batch_id(),
            dry_run=False,
        )
        if renamed:
            path = os.path.abspath(renamed)
            state["path"] = path
            state["rename"].update({"status": "ready", "changed": True, "canonical": True})
            state["sha256"] = sha256_file(path)
        else:
            state["rename"].update({"status": "ready", "canonical": True})

    rename_ready = state["rename"]["status"] == "ready" and state["rename"]["canonical"]
    process_ready = state["ocr"]["status"] == "ready" and rename_ready and not reasons
    state["routing"] = {
        "ready": process_ready,
        "needs_review": bool(reasons),
        "reasons": reasons,
    }

    if state["state"] != "DUPLICATE":
        if reasons:
            state["state"] = "REVIEW"
        elif process_ready:
            state["state"] = "ROUTE_READY"
        elif state["ocr"]["status"] == "ready":
            state["state"] = "OCR_VERIFIED"
        else:
            state["state"] = "DISCOVERED"

    if store:
        store.event("process", state, state.get("sha256"))
    return state


def _destination_for_state(state: dict, routing: dict) -> tuple[str | None, bool, list[str]]:
    readiness = routing.get("readiness", {})
    required_conf = float(readiness.get("minimum_classification_confidence", 0.90))
    reasons = list(state.get("routing", {}).get("reasons", []))
    ready = bool(state.get("routing", {}).get("ready"))
    conf = float(state.get("classification", {}).get("confidence", 0.0) or 0.0)
    category = _canonical_category(str(state.get("classification", {}).get("category") or ""))

    if bool(readiness.get("require_searchable_pdf", True)) and state.get("ocr", {}).get("status") != "ready":
        ready = False
        reasons.append("searchable_pdf_not_ready")
    if bool(readiness.get("require_canonical_filename", True)) and not state.get("rename", {}).get("canonical"):
        ready = False
        reasons.append("canonical_filename_not_ready")
    if conf < required_conf:
        ready = False
        reasons.append("classification_below_routing_threshold")

    destination_key = None
    if ready:
        for rule in routing.get("routes", []):
            if isinstance(rule, dict) and _category_matches(category, rule):
                destination_key = rule.get("destination")
                break
        if not destination_key:
            ready = False
            reasons.append("no_route_for_category")

    if not ready:
        destination_key = routing.get("fallback", {}).get("destination")

    destination = routing.get("destinations", {}).get(destination_key or "", {})
    destination_path = destination.get("path") if isinstance(destination, dict) else None
    if not destination_path:
        reasons.append("destination_not_configured")
        return None, False, list(dict.fromkeys(reasons))
    return os.path.abspath(os.path.expanduser(os.path.expandvars(str(destination_path)))), ready, list(dict.fromkeys(reasons))


def _append_routing_audit(routing: dict, result: dict, digest: str | None) -> None:
    audit = routing.get("audit", {})
    if not isinstance(audit, dict) or not bool(audit.get("enabled", False)):
        return
    raw_path = audit.get("path")
    if not raw_path:
        return
    path = os.path.abspath(os.path.expanduser(os.path.expandvars(str(raw_path))))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sha256": digest,
        **result,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def route_document(state: dict, routing: dict, *, apply: bool = False, store: AuditStore | None = None) -> dict:
    """Route one already-verified state using private destination policy."""
    source = os.path.abspath(state["path"])
    root, normal_route, reasons = _destination_for_state(state, routing)
    result = {
        "source": source,
        "status": "planned" if not apply else "pending",
        "route_ready": normal_route,
        "destination": None,
        "reasons": reasons,
    }
    if root is None:
        result["status"] = "review"
        if store:
            store.event("route", result, state.get("sha256"))
        _append_routing_audit(routing, result, state.get("sha256"))
        return result

    target = os.path.join(root, os.path.basename(source))
    result["destination"] = target

    if os.path.abspath(source) == os.path.abspath(target):
        result["status"] = "already_routed"
    elif os.path.exists(target):
        collision = inspect_collision(source, target)
        if collision.kind == "duplicate":
            result["status"] = "duplicate"
            result["reasons"].append("exact_duplicate_destination_exists")
        else:
            result["status"] = "review"
            result["reasons"].append("destination_collision_different_content")
    elif apply:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
        result["status"] = "routed" if normal_route else "moved_to_review"
        result["destination"] = os.path.abspath(target)
    else:
        result["status"] = "planned" if normal_route else "planned_review"

    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    if store:
        store.event("route", result, state.get("sha256"))
    _append_routing_audit(routing, result, state.get("sha256"))
    return result


def process_paths(
    paths: Iterable[str],
    config: dict,
    yaml_path: str,
    *,
    apply: bool = False,
    recursive: bool = False,
    routing: dict | None = None,
    route_apply: bool | None = None,
) -> dict:
    """Process a batch and optionally route it in the same invocation."""
    files = collect_pdfs(paths, recursive=recursive)
    store = AuditStore(_audit_db_path(config))
    try:
        states = [process_document(path, config, yaml_path, apply=apply, store=store) for path in files]
        routes = []
        if routing is not None:
            do_apply = apply if route_apply is None else route_apply
            for state in states:
                if state.get("state") == "DEFERRED":
                    routes.append(
                        {
                            "source": state["path"],
                            "status": "deferred",
                            "destination": None,
                            "route_ready": False,
                            "reasons": state["routing"]["reasons"],
                        }
                    )
                    continue
                routes.append(route_document(state, routing, apply=do_apply, store=store))
        return {
            "schema": STATE_SCHEMA,
            "apply": apply,
            "total": len(states),
            "route_enabled": routing is not None,
            "states": states,
            "routes": routes,
        }
    finally:
        store.close()
