"""
Multi-LLM Nexus — main entry point.

Bootstrap sequence:
  1. Load config/providers.yaml + config/adapters.yaml
  2. Instantiate all configured providers
  3. Build the router from routing config
  4. Build the bridge (uses the router)
  5. Start the behavioral layer
  6. Start all configured adapters concurrently
  7. Run until interrupted

Run with:
  python -m src.main
  or: python -m src.main --config /path/to/providers.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent

# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "aiohttp", "urllib3", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("nexus.main")


# ── Config loading ────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    """Load a YAML file with env var substitution (${VAR} patterns)."""
    if not path.exists():
        return {}
    text = path.read_text()
    # Substitute ${VAR} and $VAR patterns from environment
    import re
    def replace_env(match):
        key = match.group(1) or match.group(2)
        return os.environ.get(key, match.group(0))
    text = re.sub(r'\$\{([^}]+)\}|\$([A-Z_][A-Z0-9_]*)', replace_env, text)
    return yaml.safe_load(text) or {}


def _load_env(path: Path) -> None:
    """Load .env file into os.environ (simple KEY=VALUE parser)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ── Provider bootstrap ────────────────────────────────────────────────────────

def _build_providers(providers_config: dict) -> dict:
    """Instantiate all providers from config. Returns name→provider dict."""
    from .providers import load_provider

    instances = {}
    for name, cfg in providers_config.items():
        provider_type = cfg.get("type")
        if not provider_type:
            logger.warning(f"Provider '{name}' has no type — skipping")
            continue
        try:
            instances[name] = load_provider(provider_type, cfg)
            logger.info(f"Provider '{name}' loaded ({provider_type} / {cfg.get('model', '?')})")
        except ImportError as e:
            logger.warning(f"Provider '{name}' skipped — missing package: {e}")
        except Exception as e:
            logger.error(f"Provider '{name}' failed to load: {e}")

    return instances


def _build_router(providers: dict, routing_config: dict):
    """Build the router from providers + routing config."""
    from .core.router import Router
    return Router(providers=providers, routing_config=routing_config)


# ── Adapter bootstrap ─────────────────────────────────────────────────────────

def _build_adapters(adapters_config: dict, bridge, sessions, behavior) -> list:
    """Instantiate adapters for each configured platform."""
    adapters = []

    if "mattermost" in adapters_config and adapters_config["mattermost"].get("enabled", True):
        try:
            from .adapters.mattermost.adapter import MattermostAdapter
            cfg = {**adapters_config, "mattermost": adapters_config["mattermost"]}
            adapters.append(MattermostAdapter(cfg, bridge, sessions, behavior))
            logger.info("Mattermost adapter loaded")
        except Exception as e:
            logger.error(f"Mattermost adapter failed: {e}")

    if "discord" in adapters_config and adapters_config["discord"].get("enabled", True):
        try:
            from .adapters.discord.adapter import DiscordAdapter
            adapters.append(DiscordAdapter(adapters_config, bridge, sessions, behavior))
            logger.info("Discord adapter loaded")
        except Exception as e:
            logger.error(f"Discord adapter failed: {e}")

    if "telegram" in adapters_config and adapters_config["telegram"].get("enabled", True):
        try:
            from .adapters.telegram.adapter import TelegramAdapter
            adapters.append(TelegramAdapter(adapters_config, bridge, sessions, behavior))
            logger.info("Telegram adapter loaded")
        except Exception as e:
            logger.error(f"Telegram adapter failed: {e}")

    return adapters


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(providers_yaml: Path, adapters_yaml: Path, config_dir: Path) -> None:
    # Load env vars first (so ${VAR} substitution works)
    _load_env(PROJECT_ROOT / ".env")

    providers_config_raw = _load_yaml(providers_yaml)
    adapters_config = _load_yaml(adapters_yaml)

    providers_defs = providers_config_raw.get("providers", {})
    routing_config = providers_config_raw.get("routing", {})

    if not providers_defs:
        logger.error(f"No providers configured in {providers_yaml}")
        logger.error("Run: python -m src.setup.wizard")
        sys.exit(1)

    # Build core
    from .core.session import SessionStore
    from .core.bridge import NexusBridge
    from .core.behaviors import NexusBehavior

    providers = _build_providers(providers_defs)
    if not providers:
        logger.error("No providers loaded — check config and API keys")
        sys.exit(1)

    router = _build_router(providers, routing_config)
    sessions = SessionStore(str(config_dir / "sessions.json"))
    bridge = NexusBridge(
        router=router,
        sessions=sessions,
        system_prompt=adapters_config.get("system_prompt", ""),
    )

    triage_provider_name = routing_config.get("triage")
    triage_provider = providers.get(triage_provider_name) if triage_provider_name else None
    behavior = NexusBehavior(config_dir=str(config_dir), triage_provider=triage_provider)

    # Build adapters
    adapters = _build_adapters(adapters_config, bridge, sessions, behavior)
    if not adapters:
        logger.warning("No adapters configured — Nexus will start but has no platform connections")
        logger.warning("Configure adapters in config/adapters.yaml")

    default_provider = router.providers.get(routing_config.get("default", "primary"))
    logger.info(f"Primary provider: {default_provider}")
    logger.info(f"Triage provider: {triage_provider or '(keyword heuristics)'}")
    logger.info(f"Adapters active: {len(adapters)}")
    logger.info("Nexus is running.")

    # Run all adapters concurrently
    if adapters:
        await asyncio.gather(*[a.run() for a in adapters])
    else:
        # No adapters — just park until interrupted
        await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-LLM Nexus")
    parser.add_argument("--providers", default=str(PROJECT_ROOT / "config" / "providers.yaml"),
                        help="Path to providers.yaml")
    parser.add_argument("--adapters", default=str(PROJECT_ROOT / "config" / "adapters.yaml"),
                        help="Path to adapters.yaml")
    parser.add_argument("--config-dir", default=str(PROJECT_ROOT / "config"),
                        help="Directory for runtime config files")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)

    try:
        asyncio.run(run(
            providers_yaml=Path(args.providers),
            adapters_yaml=Path(args.adapters),
            config_dir=Path(args.config_dir),
        ))
    except KeyboardInterrupt:
        logger.info("Nexus stopped.")


if __name__ == "__main__":
    main()
