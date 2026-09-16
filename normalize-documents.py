#!/usr/bin/env python3
"""Preview-first archival document normalization CLI.

This command is intentionally separate from the legacy ``rename`` command so
existing users keep the old behavior. It performs persistent OCR planning,
richer metadata extraction, confidence/review gating, and exact-duplicate
collision detection before any filename mutation.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from dataclasses import asdict, dataclass, field

from rich.console import Console

from _config_loader import load_yaml_config
from _document_processing import generate_batch_id, harmonize_company_name, rename_invoice
from _normalization import (
    NormalizationMetadata,
    assess_for_review,
    build_target_path,
    extract_normalization_metadata,
    inspect_collision,
    run_persistent_ocr,
)
from _pdf_utils import extract_content
from _utils import normalize_unicode


PDF_EXTENSION = ".pdf"
console = Console()


@dataclass
class NormalizationFileResult:
    file: str
    status: str
    proposed_name: str | None = None
    new_path: str | None = None
    category: str | None = None
    organization: str | None = None
    date: str | None = None
    doc_type: str | None = None
    confidence: float | None = None
    heuristic_confidence: float | None = None
    model_confidence: float | None = None
    source_quality: float | None = None
    review_reasons: list[str] = field(default_factory=list)
    action_dates: list[dict] = field(default_factory=list)
    persistent_ocr: dict = field(default_factory=dict)
    duplicate_of: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizationBatchResult:
    apply: bool
    total: int
    renamed: int = 0
    proposed: int = 0
    review: int = 0
    duplicate: int = 0
    skipped: int = 0
    failed: int = 0
    files: list[NormalizationFileResult] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def collect_pdf_files(paths: list[str], recursive: bool) -> list[str]:
    result: list[str] = []
    for raw in paths:
        path = normalize_unicode(raw.strip('"').rstrip("\\").rstrip("/"))
        if os.path.isfile(path):
            if path.lower().endswith(PDF_EXTENSION):
                result.append(path)
            continue
        if not os.path.isdir(path):
            logging.warning("Not a valid file or directory: %s", path)
            continue
        if recursive:
            for root, _, files in os.walk(path):
                for name in files:
                    if name.lower().endswith(PDF_EXTENSION):
                        result.append(os.path.join(root, name))
        else:
            for name in os.listdir(path):
                candidate = os.path.join(path, name)
                if os.path.isfile(candidate) and name.lower().endswith(PDF_EXTENSION):
                    result.append(candidate)
    return sorted(dict.fromkeys(result))


def _display_file_result(result: NormalizationFileResult, apply: bool) -> None:
    label = os.path.basename(result.file)
    if result.status == "renamed":
        console.print(f"[green]renamed[/] {label} -> {os.path.basename(result.new_path or '')}")
    elif result.status == "proposed":
        console.print(f"[cyan]proposed[/] {label} -> {result.proposed_name}")
    elif result.status == "review":
        reasons = ", ".join(result.review_reasons) or "manual review required"
        console.print(f"[yellow]review[/] {label}: {reasons}")
    elif result.status == "duplicate":
        console.print(f"[yellow]duplicate[/] {label} == {result.duplicate_of}")
    elif result.status == "skipped":
        console.print(f"[dim]skipped[/] {label}")
    else:
        console.print(f"[red]failed[/] {label}: {result.error or 'unknown error'}")

    if result.category or result.confidence is not None:
        console.print(
            f"  [dim]category={result.category or '-'} confidence={result.confidence if result.confidence is not None else '-'} "
            f"ocr={result.persistent_ocr.get('status', '-')}[/]"
        )


def _apply_overrides(config: dict, args: argparse.Namespace) -> None:
    if args.provider:
        config["ai"]["provider"] = args.provider
    if args.model:
        config["ai"]["model"] = args.model
    if args.text_only:
        config["pdf"]["ocr"] = False
        config["pdf"]["vision"] = False
    else:
        if args.ocr:
            config["pdf"]["ocr"] = True
        if args.vision:
            config["pdf"]["vision"] = True

    normalization = config.setdefault("normalization", {})
    if args.persistent_ocr:
        normalization["persistent_ocr"] = args.persistent_ocr
    if args.min_confidence is not None:
        normalization["min_confidence"] = args.min_confidence


def normalize_one(
    pdf_path: str,
    config: dict,
    yaml_path: str,
    apply: bool,
    batch_id: str,
) -> NormalizationFileResult:
    result = NormalizationFileResult(
        file=normalize_unicode(os.path.abspath(pdf_path).replace("\\", "/")),
        status="failed",
    )
    try:
        # Persistent OCR is the archival/searchability layer. In preview mode this
        # only reports what would happen; PaddleOCR/vision can still provide
        # temporary text to the metadata model.
        persistent = run_persistent_ocr(pdf_path, config, apply=apply)
        result.persistent_ocr = asdict(persistent)
        if persistent.warning:
            result.warnings.append(persistent.warning)

        extraction = extract_content(pdf_path, config)
        result.warnings.extend(extraction.warnings)
        has_content = bool(extraction.text.strip() or extraction.ocr_text.strip() or extraction.images)
        if not has_content:
            result.error = "No text, OCR text, or page images could be extracted"
            return result

        metadata = extract_normalization_metadata(extraction, config)
        if metadata is None:
            result.error = "AI returned no normalization metadata"
            return result

        organization = harmonize_company_name(metadata.organization, yaml_path, config)
        metadata = metadata.model_copy(update={"organization": organization})
        assessment = assess_for_review(metadata, extraction, config)

        result.category = metadata.category
        result.organization = metadata.organization
        result.date = metadata.document_date or None
        result.doc_type = metadata.document_type
        result.confidence = assessment.confidence
        result.heuristic_confidence = assessment.heuristic_confidence
        result.model_confidence = assessment.model_confidence
        result.source_quality = assessment.source_quality
        result.review_reasons = list(assessment.reasons)
        result.action_dates = [item.model_dump() for item in metadata.action_dates]

        normalization_cfg = config.get("normalization", {})
        if (
            persistent.needed
            and persistent.status in {"unavailable", "failed"}
            and bool(normalization_cfg.get("review_on_ocr_failure", True))
        ):
            result.review_reasons.append("persistent_ocr_not_completed")

        target_path = build_target_path(pdf_path, metadata, config)
        result.proposed_name = os.path.basename(target_path)
        collision = inspect_collision(pdf_path, target_path)

        if collision.kind == "same_path":
            result.status = "skipped"
            result.new_path = os.path.abspath(pdf_path).replace("\\", "/")
            return result
        if collision.kind == "duplicate":
            result.status = "duplicate"
            result.duplicate_of = collision.target_path.replace("\\", "/")
            return result
        if collision.kind == "conflict":
            result.review_reasons.append("filename_collision_different_content")

        result.review_reasons = list(dict.fromkeys(result.review_reasons))
        if result.review_reasons:
            result.status = "review"
            return result

        if not apply:
            result.status = "proposed"
            result.new_path = os.path.abspath(target_path).replace("\\", "/")
            return result

        # Re-check immediately before mutation. rename_invoice retains the legacy
        # retry/undo behavior; this precondition prevents its numeric-suffix
        # collision fallback from becoming the normalizer's duplicate policy.
        collision = inspect_collision(pdf_path, target_path)
        if collision.kind == "duplicate":
            result.status = "duplicate"
            result.duplicate_of = collision.target_path.replace("\\", "/")
            return result
        if collision.kind not in {"none", "same_path"}:
            result.status = "review"
            result.review_reasons.append("filename_collision_changed_before_apply")
            return result

        undo_log = os.path.join(os.path.dirname(os.path.abspath(pdf_path)), ".autorename-log.json")
        renamed = rename_invoice(
            pdf_path,
            metadata.organization,
            __import__("_document_processing").parse_document_date(metadata.document_date),
            metadata.document_type,
            config,
            undo_log_path=undo_log,
            batch_id=batch_id,
            dry_run=False,
        )
        if renamed is None:
            result.status = "skipped"
            result.new_path = os.path.abspath(pdf_path).replace("\\", "/")
        else:
            result.status = "renamed"
            result.new_path = os.path.abspath(renamed).replace("\\", "/")
        return result
    except Exception as exc:
        logging.error("Normalization failed for %s: %s", pdf_path, exc)
        logging.debug(traceback.format_exc())
        result.error = str(exc)
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="normalize-documents",
        description=(
            "Preview-first archival PDF normalization: persistent OCR, AI naming/classification, "
            "review gating, and exact-duplicate detection."
        ),
    )
    parser.add_argument("paths", nargs="+", help="PDF files or directories")
    parser.add_argument("--recursive", "-r", action="store_true", help="Process directories recursively")
    parser.add_argument("--apply", action="store_true", help="Apply persistent OCR and approved renames; default is preview")
    parser.add_argument("--config", dest="config_path", default=None, help="Path to config.yaml")
    parser.add_argument("--output", "-o", choices=["text", "json"], default="text")
    parser.add_argument("--provider", default=None, help="Override AI provider")
    parser.add_argument("--model", default=None, help="Override AI model")
    parser.add_argument("--ocr", action="store_true", help="Force PaddleOCR for metadata extraction")
    parser.add_argument("--vision", action="store_true", help="Enable page-image vision for metadata extraction")
    parser.add_argument("--text-only", action="store_true", help="Disable temporary PaddleOCR and vision")
    parser.add_argument(
        "--persistent-ocr",
        choices=["auto", "always", "never"],
        default=None,
        help="Override searchable-PDF OCR policy",
    )
    parser.add_argument("--min-confidence", type=float, default=None, help="Auto-rename threshold from 0 to 1")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.abspath(args.config_path) if args.config_path else os.path.join(base_dir, "config.yaml")
    config = load_yaml_config(config_path)
    if config is None:
        message = {"error": f"Config file not found or invalid: {config_path}"}
        if args.output == "json":
            print(json.dumps(message, indent=2))
        else:
            console.print(f"[red]{message['error']}[/red]")
        return 3

    _apply_overrides(config, args)
    normalization = config.setdefault("normalization", {})
    threshold = float(normalization.get("min_confidence", 0.85))
    if not 0.0 <= threshold <= 1.0:
        console.print("[red]normalization.min_confidence must be between 0 and 1[/red]")
        return 3

    files = collect_pdf_files(args.paths, args.recursive)
    if not files:
        console.print("[yellow]No PDF files found.[/yellow]")
        return 4

    yaml_path = os.path.join(os.path.dirname(config_path), "harmonized-company-names.yaml")
    batch_id = generate_batch_id()
    batch = NormalizationBatchResult(apply=args.apply, total=len(files))

    for pdf_path in files:
        file_result = normalize_one(pdf_path, config, yaml_path, args.apply, batch_id)
        batch.files.append(file_result)
        if file_result.status == "renamed":
            batch.renamed += 1
        elif file_result.status == "proposed":
            batch.proposed += 1
        elif file_result.status == "review":
            batch.review += 1
        elif file_result.status == "duplicate":
            batch.duplicate += 1
        elif file_result.status == "skipped":
            batch.skipped += 1
        else:
            batch.failed += 1
        if args.output == "text":
            _display_file_result(file_result, args.apply)

    if args.output == "json":
        print(batch.to_json())
    else:
        mode = "apply" if args.apply else "preview"
        console.print(
            f"\n[bold]{mode}[/]: total={batch.total} renamed={batch.renamed} proposed={batch.proposed} "
            f"review={batch.review} duplicate={batch.duplicate} skipped={batch.skipped} failed={batch.failed}"
        )

    return 5 if batch.failed or batch.review else 0


if __name__ == "__main__":
    sys.exit(main())
