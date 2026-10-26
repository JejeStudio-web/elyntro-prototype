import os
from types import SimpleNamespace


def test_create_uses_model_when_provider_is_configured(monkeypatch):
    import app as mod

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        mod,
        "_create_with_model",
        lambda goal: (
            {"name": "demo.md", "media_type": "text/markdown", "content": "# Demo\n\nGenerated."},
            {"model": "test-model"},
        ),
    )
    monkeypatch.setattr(
        mod,
        "_validate_artifact",
        lambda artifact: {"status": "static_checks_passed", "checks": [{"name": "non_empty", "passed": True}]},
    )

    result = mod.handle_create({"goal": "Create a demo"}, SimpleNamespace())
    assert result.output["mode"] == "model-generated"
    assert result.artifacts[0]["name"] == "demo.md"
    assert result.output["static_validation"] == "static_checks_passed"


def test_create_does_not_claim_model_generation_without_provider(monkeypatch):
    import app as mod

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = mod.handle_create({"goal": "Create a demo"}, SimpleNamespace())
    assert result.output["mode"] == "deterministic-template"
    assert "no se ha generado con un modelo" in result.output["limitation"]


def test_create_reports_provider_failure_instead_of_fake_success(monkeypatch):
    import app as mod

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        mod,
        "_create_with_model",
        lambda goal: (None, {"error": "provider unavailable"}),
    )
    try:
        mod.handle_create({"goal": "Create a demo"}, SimpleNamespace())
    except RuntimeError as exc:
        assert "provider unavailable" in str(exc)
    else:
        raise AssertionError("A configured provider failure must not be reported as successful creation")
