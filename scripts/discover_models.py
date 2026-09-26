#!/usr/bin/env python3
"""Discover newly available LLM models — daily zero-token read-only check.

Queries the model listing endpoints (`GET /v1/models`) for each OpenAI-compatible
provider in the registry and diffs against known models. Any live model ID not in
the catalog = a "new model available" candidate.

NOTHING is auto-added to the catalog — tier placement and labeling are the
operator's call. Findings are written to data/model_discovery.json.

Intended schedule: daily cron at ~2:05am ET (after discover_provider_limits.py).

Token impact: ZERO (listing endpoints only, no inference).

Usage: python3 scripts/discover_models.py [--no-notify] [--timeout SECONDS]

NEXUS:PORTABLE — queries are provider-agnostic (reads /v1/models); operator's
configured endpoints are in config/providers.yaml.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent / "data"))
_CONFIG_DIR = Path(os.environ.get("NEXUS_CONFIG_DIR", Path(__file__).parent.parent / "config"))
OUTPUT_FILE = _DATA_DIR / "model_discovery.json"
PROVIDERS_CONFIG = _CONFIG_DIR / "providers.yaml"


def load_providers_config() -> Dict[str, Any]:
    """Load providers.yaml to get endpoint URLs and API keys."""
    if not PROVIDERS_CONFIG.exists():
        logger.warning(f"discover_models: {PROVIDERS_CONFIG} not found")
        return {}
    try:
        import yaml
        return yaml.safe_load(PROVIDERS_CONFIG.read_text()) or {}
    except Exception as e:
        logger.error(f"discover_models: could not load providers config: {e}")
        return {}


def query_models(base_url: str, api_key: Optional[str] = None, timeout: int = 30) -> Optional[List[str]]:
    """Query /v1/models endpoint. Returns list of model IDs or None on error."""
    url = urljoin(base_url, "/v1/models")
    headers = {"User-Agent": "nexus-discover-models/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode())
            models = data.get("data", [])
            if isinstance(models, list):
                return [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
            return None
    except urllib.error.URLError as e:
        logger.debug(f"discover_models: {base_url} — network error: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.debug(f"discover_models: {base_url} — invalid JSON: {e}")
        return None
    except Exception as e:
        logger.debug(f"discover_models: {base_url} — error: {e}")
        return None


def load_known_models() -> Dict[str, set[str]]:
    """Load set of known models per provider from providers.yaml.

    Format: {provider_id: {model_id, model_id, ...}, ...}
    """
    known = {}
    config = load_providers_config()
    providers = config.get("providers", [])
    if not isinstance(providers, list):
        return known

    for provider_def in providers:
        if not isinstance(provider_def, dict):
            continue
        provider_id = provider_def.get("id")
        models = provider_def.get("models", [])
        if provider_id and isinstance(models, list):
            known[provider_id] = {m.get("name", m) if isinstance(m, dict) else m for m in models}

    return known


def discover_models(
    timeout: int = 30,
    providers_to_check: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Discover new models by querying /v1/models endpoints.

    Returns:
      checked_at: ISO timestamp
      providers: {provider_id: {new: [...], known: [...], errors: [...]}, ...}
    """
    config = load_providers_config()
    known_models = load_known_models()
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "providers": {},
    }

    for provider_def in config.get("providers", []):
        if not isinstance(provider_def, dict):
            continue
        provider_id = provider_def.get("id")
        if not provider_id:
            continue
        if providers_to_check and provider_id not in providers_to_check:
            continue

        # Only check OpenAI-compatible and Ollama providers (they have /v1/models endpoints)
        provider_type = provider_def.get("type", "").lower()
        if provider_type not in ("openai", "ollama"):
            continue

        base_url = provider_def.get("base_url")
        api_key = provider_def.get("api_key")
        if not base_url:
            logger.debug(f"discover_models: {provider_id} has no base_url — skipping")
            continue

        logger.info(f"discover_models: querying {provider_id} ({base_url})")
        live_models = query_models(base_url, api_key, timeout)

        if live_models is None:
            result["providers"][provider_id] = {
                "status": "error",
                "error": "could not query /v1/models endpoint",
                "base_url": base_url,
            }
            logger.warning(f"discover_models: {provider_id} query failed")
        else:
            live_set = set(live_models)
            known_set = known_models.get(provider_id, set())
            new_models = sorted(live_set - known_set)
            removed_models = sorted(known_set - live_set)

            result["providers"][provider_id] = {
                "status": "ok",
                "live_count": len(live_set),
                "known_count": len(known_set),
                "new": new_models,
                "removed": removed_models,
            }

            if new_models:
                logger.info(f"discover_models: {provider_id} has {len(new_models)} new model(s): {new_models[:3]}")
            if removed_models:
                logger.info(f"discover_models: {provider_id} removed {len(removed_models)} model(s)")

    return result


def save_output(result: Dict[str, Any]) -> None:
    """Write discovery results to data/model_discovery.json."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        OUTPUT_FILE.write_text(json.dumps(result, indent=2))
        logger.info(f"discover_models: wrote {OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"discover_models: save failed: {e}")


def main():
    """Discover and save newly available models."""
    parser = argparse.ArgumentParser(description="Discover newly available LLM models")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--providers", nargs="+", help="Specific providers to check (default: all)")
    parser.add_argument("--no-notify", action="store_true", help="Skip notification")
    args = parser.parse_args()

    logger.info("discover_models: starting")
    result = discover_models(timeout=args.timeout, providers_to_check=args.providers)
    save_output(result)

    # Count new models across all providers
    total_new = sum(len(p.get("new", [])) for p in result["providers"].values())
    total_removed = sum(len(p.get("removed", [])) for p in result["providers"].values())
    logger.info(
        f"discover_models: complete — "
        f"{len(result['providers'])} provider(s) checked, "
        f"{total_new} new model(s), {total_removed} removed"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
