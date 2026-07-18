"""Tests for the scribe lane — backend-chain ordering, env overrides, fail-soft."""
import pytest

from src.orchestration import scribe


def test_default_backend_is_nexus(monkeypatch):
    monkeypatch.delenv("SCRIBE_BACKENDS", raising=False)
    assert scribe._backends() == ["nexus"]


def test_backends_env_override(monkeypatch):
    monkeypatch.setenv("SCRIBE_BACKENDS", "ollama, nexus")
    assert scribe._backends() == ["ollama", "nexus"]


def test_ollama_lane_skipped_when_unconfigured(monkeypatch):
    # ollama first, but no model set -> lane skipped, falls through to nexus
    monkeypatch.setenv("SCRIBE_BACKENDS", "ollama,nexus")
    monkeypatch.delenv("SCRIBE_OLLAMA_MODEL", raising=False)
    calls = []

    def fake_post(url, prompt, system, model, max_tokens, timeout, api_key=None):
        calls.append((url, model))
        return "answer"

    monkeypatch.setattr(scribe, "_post_openai", fake_post)
    text, backend = scribe.complete("hi")
    assert (text, backend) == ("answer", "nexus")
    # only the nexus lane actually posted (ollama skipped for lack of model)
    assert len(calls) == 1
    assert calls[0][1] == "nexus-nano"


def test_nexus_lane_uses_env_model_and_url(monkeypatch):
    monkeypatch.setenv("SCRIBE_BACKENDS", "nexus")
    monkeypatch.setenv("NEXUS_SCRIBE_URL", "http://example/v1/chat/completions")
    monkeypatch.setenv("NEXUS_SCRIBE_MODEL", "nexus-standard")
    seen = {}

    def fake_post(url, prompt, system, model, max_tokens, timeout, api_key=None):
        seen["url"] = url
        seen["model"] = model
        return "ok"

    monkeypatch.setattr(scribe, "_post_openai", fake_post)
    text, backend = scribe.complete("q")
    assert backend == "nexus"
    assert seen == {"url": "http://example/v1/chat/completions", "model": "nexus-standard"}


def test_nexus_lane_sends_bearer_token(monkeypatch):
    # regression: the local Nexus :8080 API requires a bearer token; the nexus
    # lane 401'd end-to-end until scribe sent Authorization. Default key "nexus".
    monkeypatch.setenv("SCRIBE_BACKENDS", "nexus")
    monkeypatch.delenv("NEXUS_API_KEY", raising=False)
    seen = {}

    def fake_post(url, prompt, system, model, max_tokens, timeout, api_key=None):
        seen["api_key"] = api_key
        return "ok"

    monkeypatch.setattr(scribe, "_post_openai", fake_post)
    scribe.complete("q")
    assert seen["api_key"] == "nexus"

    monkeypatch.setenv("NEXUS_API_KEY", "secret123")
    scribe.complete("q")
    assert seen["api_key"] == "secret123"


def test_never_raises_when_all_lanes_fail(monkeypatch):
    monkeypatch.setenv("SCRIBE_BACKENDS", "nexus")

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(scribe, "_post_openai", boom)
    # best-effort: returns ("", "") rather than propagating
    assert scribe.complete("q") == ("", "")


def test_empty_response_advances_to_next_lane(monkeypatch):
    monkeypatch.setenv("SCRIBE_BACKENDS", "ollama,nexus")
    monkeypatch.setenv("SCRIBE_OLLAMA_MODEL", "tiny")
    order = []

    def fake_post(url, prompt, system, model, max_tokens, timeout, api_key=None):
        order.append(model)
        return "" if model == "tiny" else "final"

    monkeypatch.setattr(scribe, "_post_openai", fake_post)
    text, backend = scribe.complete("q")
    assert (text, backend) == ("final", "nexus")
    assert order == ["tiny", "nexus-nano"]
