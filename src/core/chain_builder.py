"""
Build ProviderChain from YAML configuration.

Helper for bootstrapping the failover system from providers.yaml.
Used by adapters and daemons to set up the bridge with chain support.
"""
from __future__ import annotations

import logging
from typing import Optional

from .provider_chain import ProviderChain, ProviderChainEntry, ChainConfig
from ..providers.base import BaseProvider

logger = logging.getLogger(__name__)


def build_provider_chain(
    providers: dict[str, BaseProvider],
    providers_config: dict,
) -> Optional[ProviderChain]:
    """
    Build a ProviderChain from the providers.yaml config.

    Args:
        providers: Dict of provider_name → BaseProvider instances
        providers_config: The parsed YAML config dict (with 'failover' key)

    Returns:
        ProviderChain if failover section exists, else None
    """
    failover_config = providers_config.get("failover")
    if not failover_config:
        logger.info("No failover config found — using Router mode (single provider)")
        return None

    # Build ChainConfig from YAML
    chain_config = ChainConfig(
        strategy=failover_config.get("strategy", "priority"),
        health_check_interval=failover_config.get("health_check_interval", 60),
        retry_attempts=failover_config.get("retry_attempts", 2),
        retry_delay=failover_config.get("retry_delay", 0.5),
        failure_threshold=failover_config.get("failure_threshold", 3),
        on_failure=failover_config.get("on_failure", "next_available"),
        enable_health_monitoring=failover_config.get("enable_health_monitoring", True),
        cooldown_seconds=failover_config.get("cooldown_seconds", 30.0),
    )

    # Build chain entries, sorted by priority
    entries: list[ProviderChainEntry] = []

    for provider_name, provider in providers.items():
        provider_def = providers_config.get("providers", {}).get(provider_name, {})

        priority = provider_def.get("priority")
        if priority is None:
            logger.debug(f"Provider {provider_name} has no priority — skipping chain entry")
            continue

        tier = provider_def.get("tier", "standard")
        entries.append(
            ProviderChainEntry(
                provider=provider,
                priority=priority,
                tier=tier,
                name=provider_name,
                display_prefix=provider_def.get("display_prefix", provider_name.title()),
                model_display=provider_def.get("model_display", provider_def.get("model", tier)),
                effort_levels=provider_def.get("effort_levels", False),
            )
        )

    if not entries:
        logger.warning("No providers with priority — chain will be empty")
        return None

    entries.sort(key=lambda e: e.priority)

    logger.info(
        f"Built ProviderChain with {len(entries)} providers: "
        f"{' → '.join(e.name for e in entries)}"
    )

    return ProviderChain(entries=entries, config=chain_config)
