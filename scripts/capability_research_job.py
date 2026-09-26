#!/usr/bin/env python3
"""Capability Research Job — benchmark newly discovered models, update capability map.

Reads data/model_discovery.json (output of discover_models.py), identifies models
new since the last run, and routes them to a cheap provider for quick benchmarking.
Results feed into the capability_map pre-seed scores so the swarm learns real
provider performance over time.

Event-gated: fires only on new models (no unconditional nightly re-run waste).
Runs daily at 3:20am ET (after discover_models.py at 2:05am).

Output: data/model_intel.json — durable research roster, keyed by "provider::model_id".
Findings: accuracy, reasoning quality, latency estimates per domain.

Zero LLM tokens spent unless new models exist — everything else is mechanical
diff + file I/O. When benchmarking fires, uses ONE cheap provider (nano tier,
configurable) to score the model across domains (coding, reasoning, writing).

Token impact: DEPENDS ON DISCOVERY. No discovery = zero tokens. Discovery of N
new models = ~N * (3-5 benchmark prompts) tokens.

NEXUS:PORTABLE — benchmarking is provider-agnostic (via bridge.py + agent_loop);
only notification protocol is operator-specific (via Notifier).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths
_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent / "data"))
_CONFIG_DIR = Path(os.environ.get("NEXUS_CONFIG_DIR", Path(__file__).parent.parent / "config"))
_SRC_DIR = Path(__file__).parent.parent / "src"

MODEL_DISCOVERY_PATH = _DATA_DIR / "model_discovery.json"
MODEL_INTEL_PATH = _DATA_DIR / "model_intel.json"
CAPABILITY_MAP_PATH = _DATA_DIR / "capability_map.json"

sys.path.insert(0, str(_SRC_DIR))


def load_model_discovery() -> Dict[str, Any]:
    """Load model_discovery.json (output of discover_models.py)."""
    try:
        if MODEL_DISCOVERY_PATH.exists():
            return json.loads(MODEL_DISCOVERY_PATH.read_text())
    except Exception as e:
        logger.warning(f"capability_research: could not load model_discovery: {e}")
    return {"providers": {}}


def load_model_intel() -> Dict[str, Any]:
    """Load the durable per-model research roster."""
    try:
        if MODEL_INTEL_PATH.exists():
            return json.loads(MODEL_INTEL_PATH.read_text())
    except Exception as e:
        logger.warning(f"capability_research: could not load model_intel: {e}")
    return {"schema_version": 1, "models": {}, "last_updated": None}


def save_model_intel(data: Dict[str, Any]) -> None:
    """Atomic write of model_intel.json."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_INTEL_PATH.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(MODEL_INTEL_PATH)
        logger.info(f"capability_research: saved {MODEL_INTEL_PATH}")
    except Exception as e:
        logger.error(f"capability_research: save failed: {e}")


def get_new_models() -> Dict[str, List[str]]:
    """Identify models new since last research run.

    Returns: {provider_id: [model_id, ...], ...}
    """
    discovery = load_model_discovery()
    intel = load_model_intel()
    known_models = intel.get("models", {})

    new_by_provider = {}
    for provider_id, provider_data in discovery.get("providers", {}).items():
        if provider_data.get("status") != "ok":
            continue
        new_models = provider_data.get("new", [])
        if not new_models:
            continue

        # Filter to models not yet researched
        already_known = [m for m in new_models if f"{provider_id}::{m}" in known_models]
        still_new = [m for m in new_models if f"{provider_id}::{m}" not in known_models]

        if still_new:
            new_by_provider[provider_id] = still_new
            logger.info(f"capability_research: {len(still_new)} new model(s) in {provider_id}")

    return new_by_provider


def benchmark_model_stub(provider_id: str, model_id: str) -> Dict[str, Any]:
    """Stub: benchmark one model (placeholder until agent_loop is wired).

    In the live implementation, this would call a Scribe agent to run quick
    benchmark prompts across domains. For Nexus Phase 1, we collect the
    model info but don't run expensive benchmarks yet.

    TODO: Wire into src/core/agent_loop.py for real benchmarking.
    """
    return {
        "provider": provider_id,
        "model_id": model_id,
        "benchmark_date": datetime.now(timezone.utc).isoformat(),
        "domains": {
            "coding": {"status": "placeholder", "score": None},
            "reasoning": {"status": "placeholder", "score": None},
            "writing": {"status": "placeholder", "score": None},
        },
        "note": "Benchmarking not yet implemented — model registered for future evaluation",
    }


async def run_capability_research(
    max_new_per_run: int = 15,
) -> Dict[str, Any]:
    """Main research job: discover new models, benchmark, update capability_map.

    Args:
        max_new_per_run: Cap how many new models to benchmark in one run
                        (to avoid CI/token budget shocks)

    Returns:
        Summary dict with researched models and any errors.
    """
    logger.info("capability_research: starting")
    new_models = get_new_models()
    intel = load_model_intel()
    models_dict = intel.get("models", {})

    total_new = sum(len(m) for m in new_models.values())
    logger.info(f"capability_research: discovered {total_new} total new model(s)")

    if total_new > max_new_per_run:
        logger.info(
            f"capability_research: capping at {max_new_per_run} per run "
            f"(use --max N to override)"
        )

    researched_count = 0
    for provider_id, model_list in new_models.items():
        for model_id in model_list[:max_new_per_run - researched_count]:
            key = f"{provider_id}::{model_id}"
            logger.info(f"capability_research: benchmarking {key}")

            result = benchmark_model_stub(provider_id, model_id)
            models_dict[key] = result
            researched_count += 1

            if researched_count >= max_new_per_run:
                break
        if researched_count >= max_new_per_run:
            break

    # Update metadata
    intel["models"] = models_dict
    intel["last_updated"] = datetime.now(timezone.utc).isoformat()
    intel["total_researched"] = len(models_dict)

    save_model_intel(intel)
    logger.info(
        f"capability_research: complete — "
        f"researched {researched_count} model(s), "
        f"{len(models_dict)} total in roster"
    )

    return {
        "researched": researched_count,
        "total": len(models_dict),
        "errors": [],
    }


async def main():
    """Entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Capability research job")
    parser.add_argument(
        "--max",
        type=int,
        default=15,
        help="Max new models to benchmark per run (default: 15)",
    )
    args = parser.parse_args()

    result = await run_capability_research(max_new_per_run=args.max)
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
