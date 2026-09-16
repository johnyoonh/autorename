#!/usr/bin/env python3
"""Canonical autorename command.

New generic ingestion routes live here. Legacy rename/organize/undo/config behavior
continues through autorename-pdf.py until the compatibility migration completes.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

from _config_loader import load_yaml_config
from _pipeline import AuditStore, collect_pdfs, load_routing_config, process_document, process_paths, route_document, _audit_db_path


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROUTING_CONFIG = "~/.config/autorename/routing.yaml"


def _shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", help="PDF files or directories")
    parser.add_argument("--recursive", "-r", action="store_true", help="Process directories recursively")
    parser.add_argument("--apply", action="store_true", help="Apply file mutations; default is preview")
    parser.add_argument("--config", dest="config_path", default=None, help="Path to config.yaml")
    parser.add_argument("--output", "-o", choices=["text", "json"], default="text")
    parser.add_argument("--provider", default=None, help="Override AI provider")
    parser.add_argument("--model", default=None, help="Override AI model")
    parser.add_argument("--ocr", action="store_true", help="Force PaddleOCR for metadata extraction")
    parser.add_argument("--vision", action="store_true", help="Enable page-image vision")
    parser.add_argument("--text-only", action="store_true", help="Disable temporary PaddleOCR and vision")
    parser.add_argument("--persistent-ocr", choices=["auto", "always", "never"], default=None)
    parser.add_argument("--min-confidence", type=float, default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autorename",
        description="OCR, canonical naming, classification, readiness, and safe routing for documents.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    process = sub.add_parser("process", help="Verify OCR, canonical name, classification, and route readiness")
    _shared(process)
    process.add_argument("--route", action="store_true", help="Route ready/review files in this same invocation")
    process.add_argument(
        "--routing-config",
        default=DEFAULT_ROUTING_CONFIG,
        help="Private routing policy used with --route",
    )

    route = sub.add_parser("route", help="Route files only after process-state verification")
    _shared(route)
    route.add_argument("--routing-config", default=DEFAULT_ROUTING_CONFIG)

    return parser


def _config(args: argparse.Namespace) -> tuple[dict, str]:
    config_path = os.path.abspath(os.path.expanduser(args.config_path)) if args.config_path else os.path.join(BASE_DIR, "config.yaml")
    config = load_yaml_config(config_path)
    if not config:
        raise RuntimeError(f"Could not load config: {config_path}")

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
        if not 0.0 <= args.min_confidence <= 1.0:
            raise RuntimeError("--min-confidence must be between 0 and 1")
        normalization["min_confidence"] = args.min_confidence
    return config, config_path


def _summary(payload: dict) -> None:
    states = payload.get("states", [])
    routes = payload.get("routes", [])
    counts: dict[str, int] = {}
    for state in states:
        key = state.get("state", "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    print("process:", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "no files")
    for state in states:
        print(
            f"  {state.get('state'):11} {os.path.basename(state.get('path', ''))} "
            f"ocr={state.get('ocr', {}).get('status')} "
            f"rename={state.get('rename', {}).get('status')} "
            f"category={state.get('classification', {}).get('category')} "
            f"confidence={state.get('classification', {}).get('confidence')}"
        )
        reasons = state.get("routing", {}).get("reasons", [])
        if reasons:
            print("    review:", ", ".join(reasons))
    if routes:
        print("route:")
        for route in routes:
            print(f"  {route.get('status'):14} {route.get('source')} -> {route.get('destination') or '-'}")


def _run_process(args: argparse.Namespace) -> int:
    config, config_path = _config(args)
    yaml_path = os.path.join(os.path.dirname(config_path), "harmonized-company-names.yaml")
    routing = load_routing_config(args.routing_config) if args.route else None
    payload = process_paths(
        args.paths,
        config,
        yaml_path,
        apply=args.apply,
        recursive=args.recursive,
        routing=routing,
    )
    if args.output == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _summary(payload)
    states = payload.get("states", [])
    return 5 if any(state.get("state") == "REVIEW" for state in states) else 0


def _run_route(args: argparse.Namespace) -> int:
    config, config_path = _config(args)
    yaml_path = os.path.join(os.path.dirname(config_path), "harmonized-company-names.yaml")
    routing = load_routing_config(args.routing_config)
    files = collect_pdfs(args.paths, recursive=args.recursive)
    store = AuditStore(_audit_db_path(config))
    states = []
    routes = []
    try:
        # Route never performs OCR/renaming itself. process_document runs in
        # verification/preview mode and reuses cached metadata when available.
        for path in files:
            state = process_document(path, config, yaml_path, apply=False, store=store)
            states.append(state)
            routes.append(route_document(state, routing, apply=args.apply, store=store))
    finally:
        store.close()
    payload = {"schema": 1, "apply": args.apply, "total": len(states), "states": states, "routes": routes}
    if args.output == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _summary(payload)
    return 5 if any(route.get("status") == "review" for route in routes) else 0


def _delegate_legacy(argv: list[str]) -> int:
    legacy = os.path.join(BASE_DIR, "autorename-pdf.py")
    old_argv = sys.argv
    try:
        sys.argv = ["autorename", *argv]
        runpy.run_path(legacy, run_name="__main__")
    finally:
        sys.argv = old_argv
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"process", "route"}:
        parser = build_parser()
        args = parser.parse_args(argv)
        try:
            if args.subcommand == "process":
                return _run_process(args)
            return _run_route(args)
        except (OSError, RuntimeError, ValueError) as exc:
            if getattr(args, "output", "text") == "json":
                print(json.dumps({"error": str(exc)}))
            else:
                print(f"autorename: {exc}", file=sys.stderr)
            return 3

    if not argv or argv in (["--help"], ["-h"]):
        print(
            "usage: autorename {process,route,rename,organize,undo,config} ...\n\n"
            "canonical routes:\n"
            "  process   verify/apply OCR, naming, classification, readiness; optionally route\n"
            "  route     route only after readiness verification\n\n"
            "legacy-compatible routes:\n"
            "  rename    AI PDF rename\n"
            "  organize  Downloads aging workflow\n"
            "  undo      reverse rename batches\n"
            "  config    inspect configuration\n\n"
            "Run 'autorename process --help' or the legacy route with --help for details."
        )
        return 0 if argv else 2

    return _delegate_legacy(argv)


if __name__ == "__main__":
    raise SystemExit(main())
