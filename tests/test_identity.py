"""Identity resolver tests — owner floor, people registry, agnostic platforms, graceful."""
import json
import importlib

import pytest

from src.core import identity


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    def _write(data):
        p = tmp_path / "identity.json"
        p.write_text(json.dumps(data))
        monkeypatch.setenv("NEXUS_IDENTITY_CONFIG", str(p))
        identity.reload()
        return identity
    yield _write
    identity._cache = None


def test_owner_floor_resolves(cfg):
    i = cfg({"owner": {"person_id": "owner",
                       "platforms": {"mattermost": {"ids": ["stryder"]},
                                     "discord": {"ids": ["151160889318309889"]}}}})
    assert i.resolve("mattermost", "stryder") == "owner"
    assert i.resolve("discord", 151160889318309889) == "owner"   # int matches str id
    assert i.is_owner("mattermost", "stryder") is True


def test_unknown_handle_is_none(cfg):
    i = cfg({"owner": {"person_id": "owner", "platforms": {"mattermost": {"ids": ["stryder"]}}}})
    assert i.resolve("mattermost", "someone-else") is None
    assert i.is_owner("discord", "999") is False


def test_people_registry_resolves(cfg):
    i = cfg({"owner": {"person_id": "owner", "platforms": {"slack": {"ids": ["boss"]}}},
             "people": {"alice": {"platforms": {"discord": {"ids": ["42"]}}}}})
    assert i.resolve("discord", "42") == "alice"
    assert i.is_owner("discord", "42") is False     # a person, not the owner


def test_agnostic_platform_no_hardcoding(cfg):
    # a platform the live code never knew about (e.g. 'matrix') works with no code change
    i = cfg({"owner": {"person_id": "owner", "platforms": {"matrix": {"ids": ["@op:server"]}}}})
    assert i.resolve("matrix", "@op:server") == "owner"


def test_missing_config_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_IDENTITY_CONFIG", str(tmp_path / "does-not-exist.json"))
    identity.reload()
    # fresh install, no identity configured → everyone unresolved, nothing crashes
    assert identity.resolve("mattermost", "anyone") is None
    assert identity.is_owner("mattermost", "anyone") is False
    identity._cache = None


def test_handles_and_notify_priority(cfg):
    i = cfg({"owner": {"person_id": "owner",
                       "platforms": {"mattermost": {"ids": ["stryder"]},
                                     "discord": {"ids": ["1", "2"]}},
                       "notify_priority": ["mattermost", "discord"]}})
    h = i.handles("owner")
    assert h["mattermost"] == ["stryder"] and h["discord"] == ["1", "2"]
    assert i.notify_priority("owner") == ["mattermost", "discord"]
    assert i.notify_priority("nobody") == []
