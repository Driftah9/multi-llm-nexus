"""
Nexus Notifier — protocol-agnostic out-of-band notification dispatch.

Used by watchers, health checks, cron scripts, and any internal component
that needs to send a message independently of the normal request/response cycle.

Reads the active adapter config at call time — never hardcodes a protocol.
Changing the configured adapter in adapters.yaml is the only change needed to
reroute all internal notifications.

Usage:
    from src.core.notify import Notifier
    n = Notifier.from_config()
    n.send("Deploy failed on worker-3")
    n.send("New submission received", destination="channel", channel="submissions")
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.notify")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "adapters.yaml"
_NOTIFY_SECTION = "notify"


class Notifier:
    """
    Routes out-of-band notifications through the configured default adapter.

    destination: "dm" | "channel"
    channel: channel/room name (used when destination="channel")
    protocol: override the default (e.g. "telegram") — use sparingly
    """

    def __init__(self, config: dict):
        self._cfg = config

    @classmethod
    def from_config(cls, config_path: Path = CONFIG_PATH) -> "Notifier":
        import yaml  # soft dependency — yaml is always present in nexus venv
        raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        return cls(raw.get(_NOTIFY_SECTION, {}))

    def send(
        self,
        message: str,
        destination: str = None,
        channel: str = None,
        protocol: str = None,
    ) -> bool:
        cfg = self._cfg
        proto = protocol or cfg.get("default_protocol", "")
        dest = destination or cfg.get("default_destination", "dm")

        if not proto:
            logger.warning("Notifier: no default_protocol configured — message dropped")
            return False

        proto_cfg = cfg.get("protocols", {}).get(proto, {})
        if not proto_cfg:
            logger.warning(f"Notifier: protocol '{proto}' not configured — message dropped")
            return False

        try:
            if proto == "mattermost":
                return self._send_mattermost(message, dest, channel, proto_cfg)
            elif proto == "slack":
                return self._send_slack(message, dest, channel, proto_cfg)
            elif proto == "discord":
                return self._send_discord(message, proto_cfg)
            elif proto == "telegram":
                return self._send_telegram(message, proto_cfg)
            else:
                logger.warning(f"Notifier: protocol '{proto}' has no send implementation")
                return False
        except Exception as e:
            logger.error(f"Notifier send failed ({proto}): {e}")
            return False

    # ── Protocol implementations ──────────────────────────────────────────

    def _send_mattermost(self, message: str, dest: str, channel: Optional[str], cfg: dict) -> bool:
        url = cfg["url"].rstrip("/")
        token = cfg["bot_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        if dest == "dm":
            channel_id = cfg.get("dm_channel_id", "")
        elif dest == "channel" and channel:
            channel_id = self._resolve_mm_channel(url, token, cfg.get("team", ""), channel)
        else:
            channel_id = cfg.get("dm_channel_id", "")

        if not channel_id:
            logger.error("Notifier(mattermost): could not resolve channel ID")
            return False

        payload = json.dumps({"channel_id": channel_id, "message": message}).encode()
        req = urllib.request.Request(f"{url}/api/v4/posts", data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return "id" in json.loads(resp.read())

    def _resolve_mm_channel(self, url: str, token: str, team: str, channel_name: str) -> str:
        headers = {"Authorization": f"Bearer {token}"}
        req = urllib.request.Request(f"{url}/api/v4/teams/name/{team}", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            team_id = json.loads(r.read())["id"]
        req2 = urllib.request.Request(
            f"{url}/api/v4/teams/{team_id}/channels/name/{channel_name}", headers=headers
        )
        with urllib.request.urlopen(req2, timeout=10) as r:
            return json.loads(r.read())["id"]

    def _send_slack(self, message: str, dest: str, channel: Optional[str], cfg: dict) -> bool:
        token = cfg["bot_token"]
        target = channel if dest == "channel" and channel else cfg.get("default_channel", "")
        if not target:
            logger.error("Notifier(slack): no channel configured")
            return False
        payload = json.dumps({"channel": target, "text": message}).encode()
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)

    def _send_discord(self, message: str, cfg: dict) -> bool:
        webhook = cfg.get("webhook_url", "")
        if not webhook:
            logger.error("Notifier(discord): no webhook_url configured")
            return False
        payload = json.dumps({"content": message[:2000]}).encode()
        req = urllib.request.Request(webhook, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 204)

    def _send_telegram(self, message: str, cfg: dict) -> bool:
        bot_token = cfg["bot_token"]
        chat_id = cfg["chat_id"]
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=data, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
