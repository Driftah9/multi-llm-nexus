"""Model Benchmarker — lightweight benchmark runner for newly discovered models.

Runs quick benchmark tasks across domains (coding, reasoning, writing) using
a cheap provider (nano tier). Results feed into capability_map pre-seed scores.

NOT a full capability-ladder implementation (that's in orchestration/);
this is a simple data-collection tool for model_intel.json.

NEXUS:PORTABLE — benchmarking via bridge (provider-agnostic).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Benchmark prompts per domain — quick, deterministic, scored by presence of expected patterns
BENCHMARKS = {
    "coding": {
        "prompt": "Write a Python function to reverse a string without slicing.",
        "expected_patterns": ["def", "return", "for", "while"],  # Must have function + loop
        "timeout_seconds": 10,
    },
    "reasoning": {
        "prompt": "If all roses are flowers, and some flowers are red, can we conclude roses are red? Explain your reasoning.",
        "expected_patterns": ["not necessarily", "some", "logic", "conclude", "assume"],
        "timeout_seconds": 10,
    },
    "writing": {
        "prompt": "Write a 2-3 sentence summary of why trees are important.",
        "expected_patterns": ["oxygen", "carbon", "ecosystem", "important"],
        "timeout_seconds": 8,
    },
}


async def benchmark_model(
    provider_id: str,
    model_id: str,
    bridge=None,  # Injected bridge for calling providers
) -> Dict[str, Any]:
    """Run benchmark suite for one model across all domains.

    Returns:
      {
        "provider": provider_id,
        "model": model_id,
        "timestamp": ISO,
        "domains": {
          "coding": {"score": 0.0-1.0, "latency_ms": N, "error": null},
          "reasoning": {...},
          "writing": {...}
        }
      }
    """
    if not bridge:
        logger.warning(f"benchmark_model: no bridge provided — skipping {provider_id}::{model_id}")
        return {
            "provider": provider_id,
            "model": model_id,
            "domains": {},
            "error": "no bridge provided",
        }

    from datetime import datetime, timezone
    import time

    result = {
        "provider": provider_id,
        "model": model_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "domains": {},
    }

    for domain, bench in BENCHMARKS.items():
        logger.debug(f"benchmark_model: {provider_id}::{model_id} — {domain}")
        start = time.time()
        try:
            # Call the provider with a short timeout
            response = await asyncio.wait_for(
                bridge.invoke(
                    provider_id,
                    prompt=bench["prompt"],
                    system="You are a helpful assistant. Be concise.",
                    max_tokens=256,
                ),
                timeout=bench["timeout_seconds"],
            )

            latency_ms = int((time.time() - start) * 1000)
            text = (response or "").lower()

            # Score: did the response contain expected patterns?
            patterns_found = sum(1 for p in bench["expected_patterns"] if p.lower() in text)
            score = min(1.0, patterns_found / len(bench["expected_patterns"]))

            result["domains"][domain] = {
                "score": round(score, 2),
                "latency_ms": latency_ms,
                "patterns_found": patterns_found,
                "total_patterns": len(bench["expected_patterns"]),
            }
        except asyncio.TimeoutError:
            latency_ms = int((time.time() - start) * 1000)
            result["domains"][domain] = {
                "score": 0.0,
                "latency_ms": latency_ms,
                "error": "timeout",
            }
            logger.warning(f"benchmark_model: {provider_id}::{model_id} — {domain} timed out")
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            result["domains"][domain] = {
                "score": 0.0,
                "latency_ms": latency_ms,
                "error": str(e)[:100],
            }
            logger.warning(f"benchmark_model: {provider_id}::{model_id} — {domain} failed: {e}")

    return result


async def benchmark_models_batch(
    models: Dict[str, list[str]],  # {provider_id: [model_id, ...], ...}
    bridge=None,
) -> Dict[str, Any]:
    """Benchmark multiple models in parallel.

    Args:
        models: {provider_id: [model_id, ...], ...}
        bridge: Injected bridge for provider calls

    Returns:
        {provider_id::model_id: {...result...}, ...}
    """
    tasks = []
    keys = []
    for provider_id, model_ids in models.items():
        for model_id in model_ids:
            key = f"{provider_id}::{model_id}"
            tasks.append(benchmark_model(provider_id, model_id, bridge))
            keys.append(key)

    if not tasks:
        return {}

    logger.info(f"benchmark_models_batch: running {len(tasks)} benchmark(s) in parallel")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = {}
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.error(f"benchmark_models_batch: {key} raised {result}")
            output[key] = {"error": str(result)[:100]}
        else:
            output[key] = result

    return output
