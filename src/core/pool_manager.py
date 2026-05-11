"""
GPU Pool Manager — load-aware routing for multi-GPU server deployments.

When an Operator runs a server with multiple GPU pools (independent workers
and/or tensor-parallel pooled models), this module tracks pool health and
tells the ProviderChain which pools are busy vs. available.

Nexus does NOT manage GPU assignment, model loading, or CUDA configuration.
The Operator is responsible for launching the right inference server
(Ollama/vLLM) with the right CUDA_VISIBLE_DEVICES. This module just
watches the endpoints and provides routing signals.

Pool modes:
  independent — each GPU runs its own model (separate Ollama instances)
  pooled      — multiple GPUs serve one model via tensor parallelism (vLLM)

Load signals:
  vLLM exposes /metrics (Prometheus format) — queue depth and KV cache usage
  Ollama exposes no metrics — load inferred from response time tracking
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger("nexus.pool_manager")


class PoolMode(Enum):
    INDEPENDENT = "independent"
    POOLED = "pooled"


@dataclass
class PoolConfig:
    name: str
    mode: PoolMode
    provider_names: list[str]
    gpus: list[int] = field(default_factory=list)
    vram_per_gpu: int = 0
    metrics_url: str = ""
    description: str = ""


@dataclass
class PoolHealth:
    pool_name: str
    mode: str
    queue_depth: int = 0
    cache_usage: float = 0.0
    avg_response_ms: float = 0.0
    is_busy: bool = False
    last_checked: float = 0.0
    signal_source: str = "unknown"


class PoolManager:
    """
    Tracks GPU pool health and provides busy signals to the routing layer.

    Integrates with ProviderChain.select_provider() to prefer non-busy pools
    when routing. On all-busy conditions, routes normally rather than blocking.
    """

    def __init__(self, pools_config: dict):
        self.pools: dict[str, PoolConfig] = {}
        self.health: dict[str, PoolHealth] = {}
        self._response_times: dict[str, list[float]] = {}
        self._running = False

        busy_cfg = pools_config.get("routing", {})
        self.busy_queue_threshold = busy_cfg.get("busy_threshold", {}).get("queue_depth", 2)
        self.busy_cache_threshold = busy_cfg.get("busy_threshold", {}).get("cache_usage", 0.85)
        self.poll_interval = busy_cfg.get("poll_interval", 10)
        self.pool_fallback = busy_cfg.get("pool_fallback", True)

        self._load(pools_config.get("pools", {}))
        self._validate()

    def _load(self, pools_raw: dict) -> None:
        for pool_name, cfg in pools_raw.items():
            mode_str = cfg.get("mode", "independent")
            try:
                mode = PoolMode(mode_str)
            except ValueError:
                logger.warning(f"Pool '{pool_name}': unknown mode '{mode_str}', defaulting to independent")
                mode = PoolMode.INDEPENDENT

            # providers can be listed as a flat list or nested dict
            providers_raw = cfg.get("providers", [])
            if isinstance(providers_raw, dict):
                provider_names = list(providers_raw.keys())
            else:
                provider_names = list(providers_raw)

            self.pools[pool_name] = PoolConfig(
                name=pool_name,
                mode=mode,
                provider_names=provider_names,
                gpus=cfg.get("gpus", []),
                vram_per_gpu=cfg.get("vram_per_gpu", 0),
                metrics_url=cfg.get("metrics_url", ""),
                description=cfg.get("description", ""),
            )

            self.health[pool_name] = PoolHealth(
                pool_name=pool_name,
                mode=mode.value,
            )

            self._response_times[pool_name] = []

            logger.info(
                f"Pool '{pool_name}' loaded: mode={mode.value} "
                f"providers={provider_names} gpus={cfg.get('gpus', [])}"
            )

    def _validate(self) -> None:
        """Warn on configurations that will likely cause problems."""
        for pool_name, pool in self.pools.items():
            if pool.mode == PoolMode.POOLED:
                # Check for mixed VRAM — tensor parallel doesn't handle it well
                if pool.vram_per_gpu == 0:
                    logger.warning(
                        f"Pool '{pool_name}': pooled mode without vram_per_gpu set. "
                        "Add vram_per_gpu for validation and accurate logging."
                    )

                # Check for metrics_url — without it, pool load is invisible
                if not pool.metrics_url:
                    logger.warning(
                        f"Pool '{pool_name}': pooled mode without metrics_url. "
                        "Load-aware routing disabled for this pool (no vLLM /metrics endpoint configured)."
                    )

        # Check for mixed VRAM across pools (operators might miss this)
        pooled_vrams = {
            pool.name: pool.vram_per_gpu
            for pool in self.pools.values()
            if pool.mode == PoolMode.POOLED and pool.vram_per_gpu > 0
        }
        if len(set(pooled_vrams.values())) > 1:
            logger.warning(
                f"Pooled pools have different vram_per_gpu values: {pooled_vrams}. "
                "Tensor-parallel requires homogeneous VRAM. "
                "Consider grouping same-VRAM cards together."
            )

    # ── Public API ───────────────────────────────────────────────

    def get_pool_for_provider(self, provider_name: str) -> Optional[str]:
        """Find which pool a provider belongs to."""
        for pool_name, pool in self.pools.items():
            if provider_name in pool.provider_names:
                return pool_name
        return None

    def is_busy(self, provider_name: str) -> bool:
        """
        Return True if the pool containing this provider is busy.
        Returns False if provider is not in any pool, or pool has no metrics.
        """
        if not self.pool_fallback:
            return False

        pool_name = self.get_pool_for_provider(provider_name)
        if not pool_name:
            return False

        health = self.health.get(pool_name)
        if not health:
            return False

        # Only trust a signal that's been checked recently
        if time.time() - health.last_checked > self.poll_interval * 3:
            return False

        return health.is_busy

    def record_response_time(self, provider_name: str, elapsed_ms: float) -> None:
        """Called by bridge after each Ollama request to track response times."""
        pool_name = self.get_pool_for_provider(provider_name)
        if not pool_name:
            return

        pool = self.pools.get(pool_name)
        if not pool or pool.mode != PoolMode.INDEPENDENT:
            return

        times = self._response_times[pool_name]
        times.append(elapsed_ms)
        if len(times) > 20:
            times.pop(0)

        # Infer busy from response time degradation
        if len(times) >= 5:
            baseline = sorted(times)[:3]
            avg_baseline = sum(baseline) / len(baseline)
            recent_avg = sum(times[-3:]) / 3
            is_busy = recent_avg > avg_baseline * 2.5

            self.health[pool_name] = PoolHealth(
                pool_name=pool_name,
                mode=pool.mode.value,
                avg_response_ms=recent_avg,
                is_busy=is_busy,
                last_checked=time.time(),
                signal_source="response_time",
            )

    def status(self) -> dict:
        return {
            pool_name: {
                "mode": pool.mode.value,
                "providers": pool.provider_names,
                "gpus": pool.gpus,
                "vram_per_gpu": pool.vram_per_gpu,
                "health": {
                    "is_busy": self.health[pool_name].is_busy,
                    "queue_depth": self.health[pool_name].queue_depth,
                    "cache_usage": self.health[pool_name].cache_usage,
                    "signal_source": self.health[pool_name].signal_source,
                    "last_checked": self.health[pool_name].last_checked,
                },
            }
            for pool_name, pool in self.pools.items()
        }

    # ── Health polling loop ──────────────────────────────────────

    async def start(self) -> None:
        """Start background metrics polling for pooled providers."""
        self._running = True
        pooled = [p for p in self.pools.values() if p.mode == PoolMode.POOLED and p.metrics_url]
        if not pooled:
            logger.info("Pool manager: no pooled providers with metrics_url — load polling inactive")
            return

        logger.info(f"Pool manager: polling metrics for {len(pooled)} pooled pool(s)")
        while self._running:
            for pool in pooled:
                await self._poll_metrics(pool)
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _poll_metrics(self, pool: PoolConfig) -> None:
        """Poll vLLM /metrics endpoint and update health."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(pool.metrics_url)
                if resp.status_code != 200:
                    return

                metrics = self._parse_prometheus(resp.text)
                queue_depth = int(metrics.get("vllm:num_requests_waiting", 0))
                cache_usage = float(metrics.get("vllm:gpu_cache_usage_perc", 0.0))

                is_busy = (
                    queue_depth > self.busy_queue_threshold
                    or cache_usage > self.busy_cache_threshold
                )

                self.health[pool.name] = PoolHealth(
                    pool_name=pool.name,
                    mode=pool.mode.value,
                    queue_depth=queue_depth,
                    cache_usage=cache_usage,
                    is_busy=is_busy,
                    last_checked=time.time(),
                    signal_source="vllm_metrics",
                )

                if is_busy:
                    logger.info(
                        f"Pool '{pool.name}' is busy: "
                        f"queue={queue_depth} cache={cache_usage:.0%}"
                    )

        except Exception as e:
            logger.debug(f"Pool '{pool.name}' metrics unreachable: {e}")

    @staticmethod
    def _parse_prometheus(text: str) -> dict[str, float]:
        """Parse Prometheus text format into a simple metric → value dict."""
        metrics = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                # Format: metric_name{labels} value
                # or:     metric_name value
                if "{" in line:
                    name = line[:line.index("{")]
                    value_str = line.split("}")[-1].strip()
                else:
                    parts = line.rsplit(None, 1)
                    if len(parts) == 2:
                        name, value_str = parts
                    else:
                        continue
                metrics[name] = float(value_str)
            except (ValueError, IndexError):
                continue
        return metrics

    @classmethod
    def from_file(cls, pools_yaml: Path) -> Optional["PoolManager"]:
        """Load from pools.yaml. Returns None if file doesn't exist."""
        if not pools_yaml.exists():
            return None
        try:
            data = yaml.safe_load(pools_yaml.read_text()) or {}
            manager = cls(data)
            logger.info(f"Pool manager loaded {len(manager.pools)} pool(s) from {pools_yaml.name}")
            return manager
        except Exception as e:
            logger.error(f"Failed to load pools.yaml: {e}")
            return None
