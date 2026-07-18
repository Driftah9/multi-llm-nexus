"""Tests for the worker-pool shim — tier roster, worker selection, voice reservation."""
import pytest

from src.orchestration import worker_pool


_FAKE_CFG = {
    "providers": {
        "groq":       {"enabled": True,  "base_url": "u", "model": "m", "tier": "nano",     "priority": 2},
        "huggingface":{"enabled": True,  "base_url": "u", "model": "m", "tier": "nano",     "priority": 10},
        "cerebras":   {"enabled": True,  "base_url": "u", "model": "m", "tier": "standard", "priority": 1},
        "mistral":    {"enabled": True,  "base_url": "u", "model": "m", "tier": "standard", "priority": 5},
        "openrouter": {"enabled": True,  "base_url": "u", "model": "m", "tier": "standard", "priority": 7},
        "sambanova":  {"enabled": True,  "base_url": "u", "model": "m", "tier": "deep",     "priority": 6},
        "cohere":     {"enabled": False, "base_url": "u", "model": "m", "tier": "standard", "priority": 8},
        "no_url":     {"enabled": True,  "base_url": "",  "model": "m", "tier": "nano",     "priority": 3},
    }
}


@pytest.fixture(autouse=True)
def _patch_cfg(monkeypatch):
    monkeypatch.setattr(worker_pool, "_load_providers_yaml", lambda: _FAKE_CFG)
    monkeypatch.delenv("NEXUS_COMMUNICATOR_PROVIDERS", raising=False)


def test_enabled_filters_disabled_and_urlless():
    ids = set(worker_pool._enabled_providers())
    assert "cohere" not in ids       # disabled
    assert "no_url" not in ids        # not council-eligible (no base_url)
    assert {"groq", "cerebras", "sambanova"} <= ids


def test_tier_roster_ordered_by_priority():
    roster = worker_pool.tier_roster()
    assert roster["nano"] == ["groq", "huggingface"]              # prio 2 then 10
    assert roster["standard"] == ["cerebras", "mistral", "openrouter"]  # 1,5,7
    assert roster["deep"] == ["sambanova"]


def test_communicator_defaults_to_deep_tier():
    assert worker_pool.communicator_providers() == {"sambanova"}


def test_communicator_env_override(monkeypatch):
    monkeypatch.setenv("NEXUS_COMMUNICATOR_PROVIDERS", "cerebras, openrouter")
    assert worker_pool.communicator_providers() == {"cerebras", "openrouter"}


def test_is_nano_tier():
    assert worker_pool.is_nano_tier("groq") is True
    assert worker_pool.is_nano_tier("cerebras") is False
    assert worker_pool.is_nano_tier("sambanova") is False


def test_worker_candidates_cheapest_first_excludes_voice():
    # all providers available; deep (sambanova) reserved as voice
    picks = worker_pool.worker_candidates(
        available={"groq", "huggingface", "cerebras", "mistral", "openrouter", "sambanova"},
        max_workers=99)
    assert "sambanova" not in picks
    # nano tier first (by priority), then standard
    assert picks == ["groq", "huggingface", "cerebras", "mistral", "openrouter"]


def test_worker_candidates_respects_availability_and_cap():
    picks = worker_pool.worker_candidates(
        available={"cerebras", "openrouter"},  # groq/huggingface not ready
        max_workers=1)
    assert picks == ["cerebras"]               # cap honored, cheapest available standard


def test_worker_candidates_explicit_exclude():
    picks = worker_pool.worker_candidates(
        available={"groq", "cerebras", "mistral"},
        max_workers=99, exclude={"groq"})
    assert "groq" not in picks
    assert picks == ["cerebras", "mistral"]
