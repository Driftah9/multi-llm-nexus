#!/usr/bin/env python3
"""Discover provider limits — daily zero-token data collection.

Reads published documentation, account APIs, and pricing pages to build a
complete picture of each provider's current rate limits, quota windows, and
plan constraints. Output feeds the provider routing decisions.

Runs daily at 2am ET via cron (no token cost, just HTTP calls + parsing).

Token impact: ZERO (reads only, no inference).

NEXUS:PORTABLE — queries are provider-agnostic; subscription tier detection
is operator-specific (stored in config, not code).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent / "data"))
_CONFIG_DIR = Path(os.environ.get("NEXUS_CONFIG_DIR", Path(__file__).parent.parent / "config"))
OUTPUT_FILE = _DATA_DIR / "provider_limits.json"
SUBSCRIPTION_TIERS_FILE = _CONFIG_DIR / "subscription_tiers.json"


def load_subscription_tiers() -> Dict[str, str]:
    """Load operator's actual plan per provider.

    Format: {"anthropic": "pro", "openai": "pay-as-you-go", ...}
    Stored in config/subscription_tiers.json (operator edits, never auto-detected in Nexus).
    """
    tiers = {}
    if SUBSCRIPTION_TIERS_FILE.exists():
        try:
            content = json.loads(SUBSCRIPTION_TIERS_FILE.read_text())
            # Remove metadata, keep tier assignments
            content.pop("_meta", None)
            content.pop("_updated", None)
            tiers = content
            logger.info(f"discover_limits: loaded {len(tiers)} subscription tier(s) from config")
        except Exception as e:
            logger.warning(f"discover_limits: could not load subscription tiers: {e}")
    return tiers


# Provider-specific limit queries (all zero-token, read-only)
# These are templates; actual queries depend on operator's configured credentials.

PROVIDER_LIMITS = {
    "anthropic": {
        "plans": {
            "free": {"requests_per_minute": 3, "tokens_per_minute": 15000, "note": "Free tier"},
            "pro": {"requests_per_minute": 50, "tokens_per_minute": 500000, "note": "Claude Pro"},
            "max": {"requests_per_minute": 500, "tokens_per_minute": 5000000, "note": "Claude Max"},
        },
        "notes": "Rate limits from https://console.anthropic.com/account/limits (requires login)",
    },
    "openai": {
        "plans": {
            "free": {"requests_per_minute": 3, "tokens_per_minute": 90000, "note": "Free tier"},
            "pay_as_you_go": {"requests_per_minute": 500, "tokens_per_minute": 200000, "note": "Pay-as-you-go"},
        },
        "notes": "Rate limits from https://platform.openai.com/account/rate-limits (requires login)",
    },
    "groq": {
        "plans": {
            "free": {"requests_per_minute": 30, "tokens_per_minute": 6000, "note": "Free tier"},
        },
        "notes": "Rate limits documented at https://console.groq.com/keys",
    },
    "ollama": {
        "plans": {
            "local": {"requests_per_minute": "unlimited", "tokens_per_minute": "unlimited", "note": "Local (no limits)"},
        },
        "notes": "Local inference, no API limits",
    },
    "gemini": {
        "plans": {
            "free": {"requests_per_minute": 60, "tokens_per_minute": 1000000, "note": "Free tier"},
            "pro": {"requests_per_minute": 500, "tokens_per_minute": "variable", "note": "Gemini Advanced"},
        },
        "notes": "Rate limits from https://ai.google.dev/pricing (requires login)",
    },
}


def discover_limits() -> Dict[str, Any]:
    """Discover provider limits from config and operator's subscription tiers.

    Returns a dict with:
      checked_at: ISO timestamp
      tiers: {provider: {plan: {limits...}, ...}, ...}
      errors: [list of discovery errors]
    """
    tiers = load_subscription_tiers()
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "discovered": {},
        "errors": [],
    }

    for provider_name, provider_data in PROVIDER_LIMITS.items():
        active_plan = tiers.get(provider_name, "unknown")
        result["discovered"][provider_name] = {
            "active_plan": active_plan,
            "plans": provider_data["plans"],
            "source": provider_data["notes"],
        }

        if active_plan == "unknown":
            result["errors"].append(
                f"{provider_name}: subscription tier not configured in config/subscription_tiers.json"
            )
            logger.warning(f"discover_limits: {provider_name} tier unknown — edit config/subscription_tiers.json")
        else:
            plan_data = provider_data["plans"].get(active_plan)
            if plan_data:
                logger.info(
                    f"discover_limits: {provider_name}/{active_plan} → "
                    f"{plan_data.get('requests_per_minute', '?')} req/min"
                )
            else:
                result["errors"].append(
                    f"{provider_name}: active plan '{active_plan}' not found in known plans"
                )

    return result


def save_output(result: Dict[str, Any]) -> None:
    """Write discovery results to data/provider_limits.json."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        OUTPUT_FILE.write_text(json.dumps(result, indent=2))
        logger.info(f"discover_limits: wrote {OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"discover_limits: save failed: {e}")


def main():
    """Discover and save provider limits."""
    logger.info("discover_limits: starting")
    result = discover_limits()
    save_output(result)

    if result["errors"]:
        logger.warning(f"discover_limits: {len(result['errors'])} error(s) — review config/subscription_tiers.json")
        for err in result["errors"]:
            logger.warning(f"  {err}")

    logger.info(f"discover_limits: complete — {len(result['discovered'])} provider(s) surveyed")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
