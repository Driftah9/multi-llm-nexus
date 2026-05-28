"""
Behavioral layer — cross-platform preferences and message routing.

Provider-agnostic refactor of claude-brain's behaviors.py.
Instead of hardcoded Claude model names, this layer works in tiers
(nano / standard / deep) that map to whatever providers the operator
has configured.

Architecture:
    UserPreferences  — persistent user settings (saved to disk)
    NexusBehavior    — runtime behavioral engine (triage, routing, rules)
    BehaviorEvent    — change notification for cross-platform propagation

    [Platform Adapter] → [NexusBehavior] → [Router] → [Provider]

All platforms share the same preferences file. A model lock set in
Mattermost immediately applies to Telegram, Discord, etc.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from ..providers.registry import (
    TIER_NANO, TIER_STANDARD, TIER_DEEP,
    get_models_for_tier, PROVIDERS,
)
from .review_gate import ReviewGate, ReviewTrigger, ChangeScope

logger = logging.getLogger(__name__)

VALID_TIERS = {TIER_NANO, TIER_STANDARD, TIER_DEEP}

# Effort levels (provider-agnostic — maps to temperature/thinking hints)
VALID_EFFORTS = {"low", "medium", "high", "max"}


# ── Routing decision ──────────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    """Result of behavioral routing — tier, effort, and optional provider override."""
    tier: str = TIER_STANDARD       # nano / standard / deep
    effort: str = "medium"          # low / medium / high / max
    provider_key: Optional[str] = None   # force a specific provider (optional)
    source: str = "default"         # "triage" | "override" | "channel_override" | "default"


# Triage prompt — returns tier not model name
TRIAGE_PROMPT = """Classify this user message for an AI assistant. Return ONLY a JSON object.

Rules:
- greetings, status checks, yes/no, short confirmations -> {"tier":"nano","effort":"low"}
- simple lookups, explanations, single-file edits -> {"tier":"standard","effort":"medium"}
- multi-file code, debugging, moderate complexity -> {"tier":"standard","effort":"high"}
- architecture, complex multi-step tasks, deep analysis -> {"tier":"deep","effort":"high"}
- critical/production changes, system-wide refactors -> {"tier":"deep","effort":"max"}

Message: """


async def triage_message(prompt: str, triage_provider, timeout: int = 10) -> RoutingDecision:
    """
    Use the configured triage provider to classify message complexity.
    Falls back to standard/medium on any failure.
    """
    from ..providers.base import Message
    try:
        import asyncio
        full_prompt = TRIAGE_PROMPT + prompt[:500]
        response = await asyncio.wait_for(
            triage_provider.send([Message(role="user", content=full_prompt)]),
            timeout=timeout,
        )
        raw = response.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        tier = data.get("tier", TIER_STANDARD)
        if tier not in VALID_TIERS:
            tier = TIER_STANDARD
        effort = data.get("effort", "medium")
        if effort not in VALID_EFFORTS:
            effort = "medium"

        return RoutingDecision(tier=tier, effort=effort, source="triage")

    except Exception as e:
        logger.debug(f"Triage failed ({e}), defaulting to standard/medium")
        return RoutingDecision()


# ── User preferences ──────────────────────────────────────────────────────────

@dataclass
class UserPreferences:
    """
    Persistent user preferences — shared across ALL platforms.

    Saved to config/user_preferences.json. Any platform can read or write.
    A tier lock set in Mattermost propagates to Discord, Telegram, etc.
    """
    # Tier/effort routing
    tier_override: Optional[str] = None     # None = auto triage
    effort_override: Optional[str] = None   # None = auto triage
    provider_override: Optional[str] = None # None = use router default
    auto_triage: bool = True

    # Response style
    verbose: bool = False
    show_tier_in_status: bool = True
    show_timing: bool = True

    # Per-channel overrides — channel_key → {"tier": "deep", "effort": "high", "provider": "openai"}
    channel_overrides: Dict[str, dict] = field(default_factory=dict)

    last_modified: float = 0.0
    modified_by: str = ""

    def save(self, path: Path) -> None:
        self.last_modified = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "UserPreferences":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to load preferences: {e}")
            return cls()

    def set_tier(self, tier: Optional[str], platform: str) -> None:
        if tier is None:
            self.tier_override = None
            self.auto_triage = True
        elif tier in VALID_TIERS:
            self.tier_override = tier
            self.auto_triage = False
        self.modified_by = platform

    def set_effort(self, effort: Optional[str], platform: str) -> None:
        if effort is None or effort in VALID_EFFORTS:
            self.effort_override = effort
        self.modified_by = platform

    def set_provider(self, provider_key: Optional[str], platform: str) -> None:
        self.provider_override = provider_key
        self.modified_by = platform

    def set_channel_override(self, channel_key: str, tier: str = None,
                              effort: str = None, provider: str = None,
                              platform: str = "") -> None:
        override = self.channel_overrides.get(channel_key, {})
        if tier:    override["tier"] = tier
        if effort:  override["effort"] = effort
        if provider: override["provider"] = provider
        self.channel_overrides[channel_key] = override
        self.modified_by = platform

    def clear_channel_override(self, channel_key: str, platform: str = "") -> None:
        self.channel_overrides.pop(channel_key, None)
        self.modified_by = platform


# ── Behavior events ───────────────────────────────────────────────────────────

class BehaviorEventType(Enum):
    TIER_CHANGED     = "tier_changed"
    EFFORT_CHANGED   = "effort_changed"
    PROVIDER_CHANGED = "provider_changed"
    AUTO_ENABLED     = "auto_enabled"
    SESSION_RESET    = "session_reset"


@dataclass
class BehaviorEvent:
    event_type: BehaviorEventType
    source_platform: str
    detail: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ── Behavior engine ───────────────────────────────────────────────────────────

class NexusBehavior:
    """
    Runtime behavioral engine — the decision-making layer between
    adapters and the provider bridge.

    Sits between adapters and the bridge. Handles:
      - Message triage (which tier + effort to use)
      - Preference management (load, save, propagate)
      - Cross-platform behavioral consistency
      - Command interpretation

    The triage_provider is typically the nano-tier provider — it classifies
    each message before the primary provider processes it.
    """

    def __init__(self, config_dir: str, triage_provider=None):
        self.config_dir = Path(config_dir)
        self.prefs_path = self.config_dir / "user_preferences.json"
        self.prefs = UserPreferences.load(self.prefs_path)
        self.triage_provider = triage_provider
        self._prefs_mtime: float = 0.0
        self.review_gate = ReviewGate()

    def set_triage_provider(self, provider) -> None:
        self.triage_provider = provider

    def _reload_if_changed(self) -> None:
        try:
            mtime = self.prefs_path.stat().st_mtime
            if mtime > self._prefs_mtime:
                self.prefs = UserPreferences.load(self.prefs_path)
                self._prefs_mtime = mtime
        except FileNotFoundError:
            pass

    async def route_message(self, message: str, channel_key: str = "", platform: str = "") -> RoutingDecision:
        """
        Decide which tier and effort level to use for a message.

        Priority:
          1. Per-channel override
          2. Global tier/effort/provider override
          3. Auto-triage via nano provider
          4. Default (standard/medium)
        """
        self._reload_if_changed()

        # 1. Per-channel override
        if channel_key and channel_key in self.prefs.channel_overrides:
            ov = self.prefs.channel_overrides[channel_key]
            return RoutingDecision(
                tier=ov.get("tier", TIER_STANDARD),
                effort=ov.get("effort", "medium"),
                provider_key=ov.get("provider"),
                source=f"channel_override:{channel_key}",
            )

        # 2. Global override
        if self.prefs.tier_override and not self.prefs.auto_triage:
            return RoutingDecision(
                tier=self.prefs.tier_override,
                effort=self.prefs.effort_override or "medium",
                provider_key=self.prefs.provider_override,
                source="global_override",
            )

        # 3. Auto triage
        if self.triage_provider:
            return await triage_message(message, self.triage_provider)

        # 4. Default
        return RoutingDecision()

    def handle_command(self, command: str, channel_key: str = "", platform: str = "") -> Optional[BehaviorEvent]:
        """
        Process behavioral commands. Returns BehaviorEvent if state changed.

        Tier commands (universal):
          !nano     — force nano tier
          !standard — force standard tier
          !deep     — force deep tier
          !auto     — return to auto triage

        Legacy Claude aliases (resolved to tiers):
          !haiku  → nano
          !sonnet → standard
          !opus   → deep

        Provider commands:
          !provider <key> — switch to a named provider from providers.yaml
        """
        parts = command.strip().lower().split(None, 1)
        cmd = parts[0].lstrip("!")
        arg = parts[1] if len(parts) > 1 else ""

        # Tier commands
        tier_map = {
            "nano": TIER_NANO, "standard": TIER_STANDARD, "deep": TIER_DEEP,
            # Claude aliases
            "haiku": TIER_NANO, "sonnet": TIER_STANDARD, "opus": TIER_DEEP,
        }
        if cmd in tier_map:
            tier = tier_map[cmd]
            self.prefs.set_tier(tier, platform)
            if arg in VALID_EFFORTS:
                self.prefs.set_effort(arg, platform)
            self.prefs.save(self.prefs_path)
            return BehaviorEvent(
                BehaviorEventType.TIER_CHANGED, platform,
                f"Tier locked to {tier}" + (f" · {arg}" if arg in VALID_EFFORTS else "") + " (all platforms)",
            )

        if cmd == "auto":
            self.prefs.set_tier(None, platform)
            self.prefs.set_effort(None, platform)
            self.prefs.set_provider(None, platform)
            self.prefs.save(self.prefs_path)
            return BehaviorEvent(BehaviorEventType.AUTO_ENABLED, platform, "Auto triage re-enabled (all platforms)")

        if cmd == "provider" and arg:
            self.prefs.set_provider(arg, platform)
            self.prefs.save(self.prefs_path)
            return BehaviorEvent(BehaviorEventType.PROVIDER_CHANGED, platform, f"Provider locked to {arg} (all platforms)")

        if cmd in VALID_EFFORTS:
            self.prefs.set_effort(cmd, platform)
            self.prefs.save(self.prefs_path)
            return BehaviorEvent(BehaviorEventType.EFFORT_CHANGED, platform, f"Effort set to {cmd} (all platforms)")

        return None

    def get_status(self) -> dict:
        self._reload_if_changed()
        return {
            "auto_triage": self.prefs.auto_triage,
            "tier_override": self.prefs.tier_override,
            "effort_override": self.prefs.effort_override,
            "provider_override": self.prefs.provider_override,
            "channel_overrides": len(self.prefs.channel_overrides),
            "last_modified_by": self.prefs.modified_by,
        }

    def check_review_trigger(self, files: list[str], lines_added: int, lines_deleted: int, is_commit_point: bool = False) -> tuple[ReviewTrigger, Optional[str]]:
        """
        Check if a changeset should trigger external review.

        Args:
            files: List of changed file paths
            lines_added: Lines added in this changeset
            lines_deleted: Lines deleted in this changeset
            is_commit_point: Whether this is about to be committed

        Returns:
            (ReviewTrigger, suggestion_message_or_none)
        """
        core, is_test, is_security, is_provider = ReviewGate.classify_files(files)

        scope = ChangeScope(
            files_changed=len(files),
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            core_modules_touched=core,
            is_commit_point=is_commit_point,
            is_test_change=is_test,
            is_security_change=is_security,
            touched_providers=is_provider,
            touched_adapters=any("adapter" in f.lower() for f in files),
        )

        trigger = self.review_gate.analyze(scope)
        message = self.review_gate.suggestion_message(scope, trigger)

        return trigger, message


# ── Helpers ───────────────────────────────────────────────────────────────────

def tier_label(tier: str) -> str:
    """Human-friendly tier name with emoji."""
    return {
        TIER_NANO:     "Nano",
        TIER_STANDARD: "Standard",
        TIER_DEEP:     "Deep",
    }.get(tier, tier.title())


def model_label(model_id: str) -> str:
    """Human-friendly model name from a model ID string."""
    lower = model_id.lower()
    if "opus"    in lower: return "Opus"
    if "sonnet"  in lower: return "Sonnet"
    if "haiku"   in lower: return "Haiku"
    if "gpt-4o-mini" in lower: return "GPT-4o mini"
    if "gpt-4o"  in lower: return "GPT-4o"
    if "flash"   in lower: return "Gemini Flash"
    if "pro"     in lower: return "Gemini Pro"
    if "llama"   in lower:
        parts = model_id.split(":")
        return f"Llama {parts[-1].upper()}" if len(parts) > 1 else "Llama"
    # Generic: first segment before - or :
    return model_id.split("-")[0].split(":")[0].title()
