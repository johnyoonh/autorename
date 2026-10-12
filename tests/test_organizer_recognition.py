from _organizer_recognition import resolve_destination


def test_agent_fallback_rejects_unknown_destination(monkeypatch):
    responses = iter([
        {"destination_id": "invented", "confidence": 0.99},
        {"destination_id": "finance", "confidence": 0.72},
    ])
    monkeypatch.setattr("_organizer_recognition._run_backend", lambda *_args: next(responses))

    result = resolve_destination(
        "Tax Packet", {"tax"}, [{"id": "finance", "tags": ["tax"]}], ["agy", "codex"]
    )

    assert result == ("finance", 0.72)
