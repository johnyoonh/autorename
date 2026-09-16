from __future__ import annotations

import pytest

import autorename


def test_process_config_is_private_routing_policy():
    parser = autorename.build_parser()
    args = parser.parse_args([
        "process",
        "/tmp/inbox",
        "--config",
        "/tmp/routing.yaml",
        "--app-config",
        "/tmp/config.yaml",
        "--apply",
    ])

    assert args.subcommand == "process"
    assert args.routing_config == "/tmp/routing.yaml"
    assert args.app_config_path == "/tmp/config.yaml"
    assert args.apply is True


def test_routing_config_remains_alias_for_config():
    parser = autorename.build_parser()
    args = parser.parse_args([
        "route",
        "/tmp/inbox",
        "--routing-config",
        "/tmp/private-routing.yaml",
    ])

    assert args.routing_config == "/tmp/private-routing.yaml"


def test_unresolved_destination_environment_fails_closed():
    routing = {
        "destinations": {
            "records": {"path": "${MISSING_RECORDS_ROOT}/30_education"},
            "review": {"path": "/tmp/review"},
        }
    }

    with pytest.raises(RuntimeError, match="Unresolved environment variable"):
        autorename._validate_routing_paths(routing)


def test_unresolved_enabled_audit_environment_fails_closed():
    routing = {
        "destinations": {"review": {"path": "/tmp/review"}},
        "audit": {"enabled": True, "path": "${MISSING_STATE_DIR}/routing.jsonl"},
    }

    with pytest.raises(RuntimeError, match="audit.path"):
        autorename._validate_routing_paths(routing)


def test_review_states_do_not_abort_process_wrapper(monkeypatch, tmp_path):
    app_config = tmp_path / "config.yaml"
    app_config.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(
        autorename,
        "_config",
        lambda args: ({"normalization": {}, "ai": {}, "pdf": {}}, str(app_config)),
    )
    monkeypatch.setattr(
        autorename,
        "process_paths",
        lambda *args, **kwargs: {
            "schema": 1,
            "apply": True,
            "total": 1,
            "route_enabled": False,
            "states": [
                {
                    "state": "REVIEW",
                    "path": "/tmp/document.pdf",
                    "ocr": {},
                    "rename": {},
                    "classification": {},
                    "routing": {"reasons": ["needs_review"]},
                }
            ],
            "routes": [],
        },
    )

    args = autorename.build_parser().parse_args(["process", "/tmp/inbox", "--apply"])
    assert autorename._run_process(args) == 0


def test_combined_process_preserves_operational_route_failure(monkeypatch, tmp_path):
    app_config = tmp_path / "config.yaml"
    app_config.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(
        autorename,
        "_config",
        lambda args: ({"normalization": {}, "ai": {}, "pdf": {}}, str(app_config)),
    )
    monkeypatch.setattr(autorename, "_routing", lambda args: {"routes": [], "destinations": {}})
    monkeypatch.setattr(
        autorename,
        "process_paths",
        lambda *args, **kwargs: {
            "schema": 1,
            "apply": True,
            "total": 1,
            "route_enabled": True,
            "states": [{"state": "ROUTE_READY", "path": "/tmp/document.pdf", "ocr": {}, "rename": {}, "classification": {}, "routing": {"reasons": []}}],
            "routes": [{"status": "review", "source": "/tmp/document.pdf", "destination": None, "reasons": ["destination_collision_different_content"]}],
        },
    )

    args = autorename.build_parser().parse_args(["process", "/tmp/inbox", "--apply", "--route"])
    assert autorename._run_process(args) == 5
