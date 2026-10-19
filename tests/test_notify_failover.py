"""Notifier failover + persisted per-protocol circuit breaker (ported from live 2026-10-19)."""
from datetime import timedelta

import pytest

from src.core import comms_breaker as breaker
from src.core.notify import Notifier


class _Fake(Notifier):
    """Real Notifier routing/breaker logic; only the network sends are faked."""

    def __init__(self, cfg, up):
        super().__init__(cfg)
        self.up, self.log = dict(up), []

    def _send_mattermost(self, message, dest, channel, cfg):
        return self._rec("mattermost", message)

    def _send_telegram(self, message, cfg):
        return self._rec("telegram", message)

    def _send_discord(self, message, cfg):
        return self._rec("discord", message)

    def _rec(self, proto, message):
        self.log.append((proto, message))
        if self.up.get(proto) == "raise":
            raise ConnectionError("boom")
        return bool(self.up.get(proto))


CFG = {"default_protocol": "mattermost", "default_destination": "dm",
       "protocols": {"mattermost": {"url": "x"}, "telegram": {"bot_token": "t"},
                     "discord": {"webhook_url": "w"}}}


def test_primary_healthy_no_failover():
    n = _Fake(CFG, {"mattermost": True, "telegram": True})
    assert n.send("hi") and n.log == [("mattermost", "hi")]


def test_primary_down_falls_through_in_config_order_with_banner():
    n = _Fake(CFG, {"mattermost": False, "telegram": True})
    assert n.send("disk full")
    assert [p for p, _ in n.log] == ["mattermost", "telegram"]
    assert n.log[1][1].startswith("⚠️ [mattermost unreachable — fallback via telegram]")


def test_explicit_fallback_order_is_respected():
    cfg = dict(CFG, fallback_order=["discord", "telegram"])
    n = _Fake(cfg, {"mattermost": False, "discord": True, "telegram": True})
    assert n.send("x") and [p for p, _ in n.log] == ["mattermost", "discord"]


def test_unconfigured_protocols_are_never_tried():
    cfg = dict(CFG, fallback_order=["slack", "telegram"])
    n = _Fake(cfg, {"mattermost": False, "telegram": True})
    assert n.send("x") and [p for p, _ in n.log] == ["mattermost", "telegram"]


def test_breaker_opens_after_threshold_and_skips_primary():
    n = _Fake(CFG, {"mattermost": "raise", "telegram": True})
    for _ in range(breaker.THRESHOLD):
        n.send("m")
    assert breaker.is_open("mattermost")
    n.log.clear()
    assert n.send("next")
    assert [p for p, _ in n.log] == ["telegram"]            # open breaker → primary skipped


def test_recovery_message_after_cooldown(monkeypatch):
    n = _Fake(CFG, {"mattermost": False, "telegram": True})
    for _ in range(breaker.THRESHOLD):
        n.send("m")
    assert breaker.outage("mattermost")["rerouted"] == breaker.THRESHOLD
    monkeypatch.setattr(breaker, "COOLDOWN", timedelta(0))  # half-open now
    n.up["mattermost"] = True
    n.log.clear()
    assert n.send("back?")
    proto, body = n.log[0]
    assert proto == "mattermost" and body.startswith("✅ mattermost recovered")
    assert f"{breaker.THRESHOLD} message(s) delivered via fallback" in body
    assert not breaker.is_open("mattermost")


def test_half_open_trial_failure_reopens(monkeypatch):
    n = _Fake(CFG, {"mattermost": False, "telegram": True})
    for _ in range(breaker.THRESHOLD):
        n.send("m")
    monkeypatch.setattr(breaker, "COOLDOWN", timedelta(0))
    n.send("trial")                                          # trial fails → re-opened
    monkeypatch.setattr(breaker, "COOLDOWN", timedelta(minutes=5))
    assert not breaker.allow("mattermost")


def test_everything_down_returns_false():
    n = _Fake(CFG, {})
    assert n.send("x") is False and len(n.log) == 3


def test_breaker_state_persists_across_instances():
    _Fake(CFG, {"mattermost": False, "telegram": True}).send("a")
    _Fake(CFG, {"mattermost": False, "telegram": True}).send("b")
    _Fake(CFG, {"mattermost": False, "telegram": True}).send("c")
    assert breaker.is_open("mattermost")                     # three processes, one breaker


def test_no_default_protocol_drops():
    assert Notifier({"protocols": {}}).send("x") is False
