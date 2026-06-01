"""
Model Lifecycle Manager.

Monthly check flow:
  1. Load config/model_sources.yaml
  2. Load data/model_lifecycle_state.json (previous run state)
  3. Skip if checked this calendar month (unless --force)
  4. For each tracked model: fetch upstream digest via its source adapter
  5. Compare to installed digest in state
  6. Collect updates_available list
  7. Save updated state
  8. If any updates found: send DM via Notifier
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.lifecycle")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "model_sources.yaml"
STATE_PATH = Path(__file__).parent.parent.parent / "data" / "model_lifecycle_state.json"


class ModelLifecycleManager:

    def __init__(self, config_path: Path = CONFIG_PATH, state_path: Path = STATE_PATH):
        self.config_path = config_path
        self.state_path = state_path
        self._config: dict = {}
        self._state: dict = {}

    def run(self, force: bool = False, dry_run: bool = False) -> dict:
        """
        Execute the monthly check.

        Returns a summary dict with keys:
          skipped: bool
          updates: list of dicts (model_id, source, pull_command, reason)
          errors: list of str
        """
        self._load_config()
        self._load_state()

        if not force and self._already_checked_this_month():
            logger.info("Model lifecycle check already ran this month — skipping (use --force to override)")
            return {"skipped": True, "updates": [], "errors": []}

        updates = []
        errors = []

        sources = self._build_sources()
        tracked = self._config.get("tracked_models", [])

        for entry in tracked:
            source_id = entry.get("source", "")
            model_id = entry.get("model_id", "")
            quant = entry.get("quant") or None

            if not model_id or source_id not in sources:
                errors.append(f"Skipping entry with unknown source '{source_id}' or missing model_id")
                continue

            source = sources[source_id]
            try:
                info = source.fetch_version(model_id, quant)
            except Exception as e:
                errors.append(f"{source_id}/{model_id}: fetch failed — {e}")
                continue

            if info is None:
                errors.append(f"{source_id}/{model_id}: model not found on source")
                continue

            state_key = f"{source_id}::{model_id}"
            installed_digest = self._state.get("models", {}).get(state_key, {}).get("digest", "")

            if not installed_digest:
                # First run — record current digest, nothing to report
                self._update_model_state(state_key, info.digest, quant)
                logger.info(f"First-time record: {model_id} @ {info.digest[:12]}")
                continue

            if info.digest != installed_digest:
                reason = "Version updated"
                if not info.quant_confirmed and quant:
                    reason = f"Version updated but quant {quant} not yet available in new release"

                updates.append({
                    "source": source_id,
                    "model_id": model_id,
                    "quant": quant,
                    "old_digest": installed_digest[:12],
                    "new_digest": info.digest[:12],
                    "last_modified": info.last_modified,
                    "pull_command": source.pull_command(model_id, quant),
                    "reason": reason,
                    "url": info.url,
                })
                # Update state to new digest so we don't re-alert next month
                self._update_model_state(state_key, info.digest, quant)
            else:
                logger.info(f"No change: {model_id} digest {info.digest[:12]}")

        self._state["last_check"] = datetime.now(timezone.utc).isoformat()
        if not dry_run:
            self._save_state()
            if updates:
                self._notify(updates)

        return {"skipped": False, "updates": updates, "errors": errors}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"model_sources.yaml not found at {self.config_path}\n"
                f"Copy config/model_sources.yaml.example to get started."
            )
        import yaml
        self._config = yaml.safe_load(self.config_path.read_text()) or {}

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {}
        else:
            self._state = {}
        self._state.setdefault("models", {})

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2))

    def _already_checked_this_month(self) -> bool:
        last = self._state.get("last_check", "")
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
            now = datetime.now(timezone.utc)
            return last_dt.year == now.year and last_dt.month == now.month
        except ValueError:
            return False

    def _update_model_state(self, key: str, digest: str, quant: Optional[str]) -> None:
        self._state["models"][key] = {
            "digest": digest,
            "quant": quant,
            "updated": datetime.now(timezone.utc).isoformat(),
        }

    def _build_sources(self) -> dict:
        from .sources import SOURCE_REGISTRY
        sources = {}
        for entry in self._config.get("sources", []):
            source_type = entry.get("type", "")
            source_id = entry.get("id", "")
            cls = SOURCE_REGISTRY.get(source_type)
            if cls:
                sources[source_id] = cls(entry)
            else:
                logger.warning(f"Unknown source type '{source_type}' for source '{source_id}' — skipping")
        return sources

    def _notify(self, updates: list[dict]) -> None:
        try:
            from src.core.notify import Notifier
            notifier = Notifier.from_config()

            lines = ["**Model updates available** (monthly check)\n"]
            for u in updates:
                quant_str = f" [{u['quant']}]" if u['quant'] else ""
                lines.append(f"• **{u['model_id']}**{quant_str} via {u['source']}")
                lines.append(f"  {u['reason']}")
                lines.append(f"  `{u['pull_command']}`")
                if u.get("url"):
                    lines.append(f"  {u['url']}")
                lines.append("")

            notify_cfg = self._config.get("notify", {})
            dest = notify_cfg.get("destination", "dm")
            channel = notify_cfg.get("channel") if dest == "channel" else None

            notifier.send("\n".join(lines), destination=dest, channel=channel)
        except Exception as e:
            logger.error(f"Lifecycle notify failed: {e}")
