"""
Multi-LLM Nexus — main entry point.

Bootstrap sequence:
  1. Load .env + config/providers.yaml + config/adapters.yaml
  2. Instantiate all configured providers
  3. Build router, sessions, bridge, triage, behavior
  4. Build the engine (ACTIVE/STANDBY hybrid tick cycle)
  5. Start all configured adapters + engine concurrently
  6. Run until interrupted (SIGTERM/SIGINT)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent

# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging(level: str = "INFO", log_dir: Path = None) -> None:
    log_dir = log_dir or (PROJECT_ROOT / "data" / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_dir / "nexus.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        ),
    ]

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    for noisy in ("httpx", "httpcore", "aiohttp", "urllib3", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("nexus.main")


# ── Config loading ────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    import re
    def replace_env(match):
        key = match.group(1) or match.group(2)
        return os.environ.get(key, match.group(0))
    text = re.sub(r'\$\{([^}]+)\}|\$([A-Z_][A-Z0-9_]*)', replace_env, text)
    return yaml.safe_load(text) or {}


def _load_env(path: Path) -> None:
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
    from .core.router import Router
    return Router(providers=providers, routing_config=routing_config)


# ── Adapter bootstrap ─────────────────────────────────────────────────────────

def _build_adapters(adapters_config: dict, engine, bridge, sessions, behavior) -> list:
    adapters = []

    if "mattermost" in adapters_config and adapters_config["mattermost"].get("enabled", True):
        try:
            from .adapters.mattermost.adapter import MattermostAdapter
            cfg = {**adapters_config, "mattermost": adapters_config["mattermost"]}
            adapter = MattermostAdapter(cfg, bridge, sessions, behavior)
            adapter.engine = engine
            adapters.append(adapter)
            logger.info("Mattermost adapter loaded")
        except Exception as e:
            logger.error(f"Mattermost adapter failed: {e}")

    if "discord" in adapters_config and adapters_config["discord"].get("enabled", True):
        try:
            from .adapters.discord.adapter import DiscordAdapter
            adapter = DiscordAdapter(adapters_config, bridge, sessions, behavior)
            adapter.engine = engine
            adapters.append(adapter)
            logger.info("Discord adapter loaded")
        except Exception as e:
            logger.error(f"Discord adapter failed: {e}")

    if "telegram" in adapters_config and adapters_config["telegram"].get("enabled", True):
        try:
            from .adapters.telegram.adapter import TelegramAdapter
            adapter = TelegramAdapter(adapters_config, bridge, sessions, behavior)
            adapter.engine = engine
            adapters.append(adapter)
            logger.info("Telegram adapter loaded")
        except Exception as e:
            logger.error(f"Telegram adapter failed: {e}")

    return adapters


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(providers_yaml: Path, adapters_yaml: Path, config_dir: Path) -> None:
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
    from .core.triage import Triage
    from .core.engine import Engine

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
    triage = Triage(provider=triage_provider)
    behavior = NexusBehavior(config_dir=str(config_dir), triage_provider=triage_provider)

    # Build engine
    engine_config = {
        "tick_interval": routing_config.get("tick_interval", 30),
        "idle_timeout": routing_config.get("idle_timeout", 300),
        "operator_name": adapters_config.get("operator_name", "Operator"),
        "agent_name": adapters_config.get("agent_name", "Nexus"),
    }
    engine = Engine(
        router=router,
        session_store=sessions,
        triage=triage,
        config=engine_config,
    )

    # Build adapters (with engine reference)
    adapters = _build_adapters(adapters_config, engine, bridge, sessions, behavior)
    if not adapters:
        logger.warning("No adapters configured — Nexus will start but has no platform connections")

    default_provider = router.providers.get(routing_config.get("default", "primary"))
    logger.info(f"Primary provider: {default_provider}")
    logger.info(f"Triage provider: {triage_provider or '(keyword heuristics)'}")
    logger.info(f"Adapters active: {len(adapters)}")
    logger.info("Nexus is running.")

    # Signal handling for graceful shutdown
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # Run engine + adapters concurrently
    tasks = [asyncio.create_task(engine.start(), name="engine")]
    for adapter in adapters:
        tasks.append(asyncio.create_task(adapter.run(), name=adapter.__class__.__name__))

    # Wait for shutdown signal
    await stop.wait()
    logger.info("Shutting down...")

    await engine.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Stopped.")


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
