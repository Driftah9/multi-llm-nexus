"""
Standardized command registry — provider-agnostic.

Commands are defined ONCE here and mapped to platform-specific syntax.
Tier commands (!nano/!standard/!deep) work regardless of which AI
providers are configured.

Claude aliases (!haiku/!sonnet/!opus) are included and resolve to
tiers — they work even if the operator isn't using Claude.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class CommandScope(Enum):
    GLOBAL   = "global"    # Affects all platforms
    CHANNEL  = "channel"   # Affects current channel only
    SESSION  = "session"   # Affects current session only
    PLATFORM = "platform"  # Platform-specific


class CommandCategory(Enum):
    TIER    = "Tier & Effort"
    SESSION = "Session"
    INFO    = "Information"
    ADMIN   = "Administration"


@dataclass
class Command:
    name: str
    description: str
    category: CommandCategory
    scope: CommandScope
    platform_syntax: Dict[str, str] = field(default_factory=dict)
    behavioral: bool = True
    args: str = ""


# ── Command definitions ───────────────────────────────────────────────────────

COMMANDS: List[Command] = [

    # ── Tier control (universal — work with any provider) ─────────────────

    Command(
        name="nano",
        description="Force nano tier — fastest, cheapest (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!nano",
            "discord":    "!nano",
            "telegram":   "!nano",
            "slack":      "/nexus-nano",
            "matrix":     "!nano",
        },
        args="[effort]",
    ),
    Command(
        name="standard",
        description="Force standard tier — balanced (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!standard",
            "discord":    "!standard",
            "telegram":   "!standard",
            "slack":      "/nexus-standard",
            "matrix":     "!standard",
        },
        args="[effort]",
    ),
    Command(
        name="deep",
        description="Force deep tier — most capable (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!deep",
            "discord":    "!deep",
            "telegram":   "!deep",
            "slack":      "/nexus-deep",
            "matrix":     "!deep",
        },
        args="[effort]",
    ),
    Command(
        name="auto",
        description="Re-enable auto triage — classify each message (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!auto",
            "discord":    "!auto",
            "telegram":   "!auto",
            "slack":      "/nexus-auto",
            "matrix":     "!auto",
        },
    ),
    Command(
        name="provider",
        description="Switch to a named provider from providers.yaml (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!provider",
            "discord":    "!provider",
            "telegram":   "!provider",
            "slack":      "/nexus-provider",
            "matrix":     "!provider",
        },
        args="<name>",
    ),

    # ── Claude aliases (resolve to tiers — work even without Claude) ──────

    Command(
        name="haiku",
        description="Force nano tier — alias for !nano (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!haiku",
            "discord":    "!haiku",
            "telegram":   "!haiku",
            "slack":      "/nexus-haiku",
            "matrix":     "!haiku",
        },
    ),
    Command(
        name="sonnet",
        description="Force standard tier — alias for !standard (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!sonnet",
            "discord":    "!sonnet",
            "telegram":   "!sonnet",
            "slack":      "/nexus-sonnet",
            "matrix":     "!sonnet",
        },
    ),
    Command(
        name="opus",
        description="Force deep tier — alias for !deep (all platforms)",
        category=CommandCategory.TIER,
        scope=CommandScope.GLOBAL,
        platform_syntax={
            "mattermost": "!opus",
            "discord":    "!opus",
            "telegram":   "!opus",
            "slack":      "/nexus-opus",
            "matrix":     "!opus",
        },
    ),

    # ── Session ───────────────────────────────────────────────────────────

    Command(
        name="new",
        description="Start a fresh session (clears history)",
        category=CommandCategory.SESSION,
        scope=CommandScope.CHANNEL,
        platform_syntax={
            "mattermost": "!new",
            "discord":    "!new",
            "telegram":   "!new",
            "slack":      "/nexus-new",
            "matrix":     "!new",
        },
    ),
    Command(
        name="reset",
        description="Same as !new",
        category=CommandCategory.SESSION,
        scope=CommandScope.CHANNEL,
        platform_syntax={
            "mattermost": "!reset",
            "discord":    "!reset",
            "telegram":   "!reset",
            "slack":      "/nexus-reset",
            "matrix":     "!reset",
        },
    ),

    # ── Information ───────────────────────────────────────────────────────

    Command(
        name="status",
        description="Show session info, tier, effort, provider",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!status",
            "discord":    "!status",
            "telegram":   "!status",
            "slack":      "/nexus-status",
            "matrix":     "!status",
        },
    ),
    Command(
        name="providers",
        description="List configured providers and their status",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!providers",
            "discord":    "!providers",
            "telegram":   "!providers",
            "slack":      "/nexus-providers",
            "matrix":     "!providers",
        },
    ),
    Command(
        name="costs",
        description="Show token costs per channel",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!costs",
            "discord":    "!costs",
            "telegram":   "!costs",
            "slack":      "/nexus-costs",
            "matrix":     "!costs",
        },
    ),
    Command(
        name="help",
        description="Show available commands",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!help",
            "discord":    "!help",
            "telegram":   "!help",
            "slack":      "/nexus-help",
            "matrix":     "!help",
        },
    ),

    # ── Administration ────────────────────────────────────────────────────

    Command(
        name="clean",
        description="Delete bot messages",
        category=CommandCategory.ADMIN,
        scope=CommandScope.PLATFORM,
        behavioral=False,
        platform_syntax={
            "mattermost": "!clean",
            "discord":    "!clean",
            "telegram":   "!clean",
            "slack":      "/nexus-clean",
            "matrix":     "!clean",
        },
        args="<n|all>",
    ),
    Command(
        name="spaces",
        description="List registered operator spaces",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!spaces",
            "discord":    "!spaces",
            "telegram":   "!spaces",
            "slack":      "/nexus-spaces",
            "matrix":     "!spaces",
        },
    ),
    Command(
        name="specialists",
        description="List available specialist profiles for the current workspace",
        category=CommandCategory.INFO,
        scope=CommandScope.SESSION,
        behavioral=False,
        platform_syntax={
            "mattermost": "!specialists",
            "discord":    "!specialists",
            "telegram":   "!specialists",
            "slack":      "/nexus-specialists",
            "matrix":     "!specialists",
        },
    ),
]


# ── Command registry ──────────────────────────────────────────────────────────

class CommandRegistry:
    """
    Looks up commands by platform-specific syntax.

    Usage:
        registry = CommandRegistry("mattermost")
        cmd, args = registry.parse("!deep high")
        # cmd.name == "deep", args == "high", cmd.behavioral == True
        # → pass to NexusBehavior.handle_command("!deep high", ...)

        help_text = registry.help_text()
    """

    def __init__(self, platform: str):
        self.platform = platform
        self._lookup: Dict[str, Command] = {}
        for cmd in COMMANDS:
            syntax = cmd.platform_syntax.get(platform, "")
            if syntax:
                self._lookup[syntax.lower()] = cmd

    def parse(self, text: str) -> Tuple[Optional[Command], str]:
        text = text.strip()
        if not text:
            return None, ""

        lower = text.lower()
        if lower in self._lookup:
            return self._lookup[lower], ""

        parts = text.split(None, 1)
        cmd_part = parts[0].lower()
        args_part = parts[1] if len(parts) > 1 else ""

        if cmd_part in self._lookup:
            return self._lookup[cmd_part], args_part

        return None, ""

    def is_command(self, text: str) -> bool:
        cmd, _ = self.parse(text)
        return cmd is not None

    def help_text(self) -> str:
        lines = ["**Commands:**\n"]
        current_category = None

        for cmd in COMMANDS:
            syntax = cmd.platform_syntax.get(self.platform, "")
            if not syntax:
                continue
            if cmd.category != current_category:
                current_category = cmd.category
                lines.append(f"\n**{cmd.category.value}**")
            args_str = f" `{cmd.args}`" if cmd.args else ""
            scope_tag = " *(global)*" if cmd.scope == CommandScope.GLOBAL else ""
            lines.append(f"`{syntax}`{args_str} — {cmd.description}{scope_tag}")

        return "\n".join(lines)

    def behavioral_commands(self) -> List[Command]:
        return [c for c in COMMANDS if c.behavioral and c.platform_syntax.get(self.platform)]

    def platform_commands(self) -> List[Command]:
        return [c for c in COMMANDS if not c.behavioral and c.platform_syntax.get(self.platform)]
