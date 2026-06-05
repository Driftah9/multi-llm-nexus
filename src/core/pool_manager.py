"""
Pool Manager — load-aware routing for multi-GPU deployments AND rate-aware
routing for cloud/free-tier providers.

Two independent tracking systems that work together:

  1. GPU Pools (existing) — tracks hardware pool health via vLLM /metrics
     or response-time degradation for Ollama. Tells the routing layer which
     GPU workers are busy vs. available.

  2. Provider Rate State (new) — tracks per-provider rate limit windows
     (RPM/RPD/TPM/TPD) defined in providers.yaml. Tells the routing layer
     whether a free-tier or paid provider can accept another request right now.

Both systems feed into PoolRouter.select() which picks the best available
provider from a tier pool using cost-class priority:
  local → free_limited → paid_subscription

Pool modes (GPU):
  independent — each GPU runs its own model (separate Ollama instances)
  pooled      — multiple GPUs serve one model via tensor parallelism (vLLM)

Cost classes (provider rate state):
  local            — on-hardware inference; electricity only, unlimited
  free_limited     — rate-limited free tier (Cerebras, Groq, GitHub Models)
  paid_subscription — token-cost or subscription; unlimited if funded
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger("nexus.pool_manager")

# Cost-class priority for pool selection (lower = cheaper = preferred)
COST_CLASS_PRIORITY: dict[str, int] = {
    "local": 0,
    "free_limited": 1,
    "paid_subscription": 2,
    "unknown": 99,
}


# ── GPU Pool types ─────────────────────────────────────────────────────────────

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


# ── Provider Rate State ────────────────────────────────────────────────────────

@dataclass
class ProviderRateConfig:
    """Rate limits as declared in providers.yaml."""
    cost_class: str = "unknown"   # local | free_limited | paid_subscription
    rpm: int = 0                  # requests per minute (0 = unlimited)
    rpd: int = 0                  # requests per day
    tpm: int = 0                  # tokens per minute
    tpd: int = 0                  # tokens per day


@dataclass
class ProviderRateState:
    """Runtime rate-limit tracking for a single provider."""
    provider_name: str
    config: ProviderRateConfig

    # Sliding windows — stores epoch timestamps of recent requests
    _req_minute: deque = field(default_factory=lambda: deque())
    _req_day: deque = field(default_factory=lambda: deque())

    # Token windows — stores (epoch, tokens) tuples
    _tok_minute: deque = field(default_factory=lambda: deque())
    _tok_day: deque = field(default_factory=lambda: deque())

    # Hard cooldown (set when a rate-limit error is received from the API)
    cooldown_until: float = 0.0
    healthy: bool = True

    def _prune(self) -> None:
        """Discard entries outside the current time windows."""
        now = time.time()
        minute_ago = now - 60
        day_ago = now - 86400

        while self._req_minute and self._req_minute[0] < minute_ago:
            self._req_minute.popleft()
        while self._req_day and self._req_day[0] < day_ago:
            self._req_day.popleft()
        while self._tok_minute and self._tok_minute[0][0] < minute_ago:
            self._tok_minute.popleft()
        while self._tok_day and self._tok_day[0][0] < day_ago:
            self._tok_day.popleft()

    def record(self, tokens_used: int = 0) -> None:
        """Record a completed request and the tokens it consumed."""
        now = time.time()
        self._req_minute.append(now)
        self._req_day.append(now)
        if tokens_used > 0:
            self._tok_minute.append((now, tokens_used))
            self._tok_day.append((now, tokens_used))
        self._prune()

    def is_available(self) -> bool:
        """Return True if this provider can accept another request right now."""
        if not self.healthy:
            return False

        if time.time() < self.cooldown_until:
            remaining = self.cooldown_until - time.time()
            logger.debug(f"{self.provider_name}: cooldown active ({remaining:.0f}s remaining)")
            return False

        # Local providers are always available (no rate limits)
        if self.config.cost_class == "local":
            return True

        self._prune()
        cfg = self.config

        if cfg.rpm > 0 and len(self._req_minute) >= cfg.rpm:
            logger.debug(f"{self.provider_name}: RPM limit reached ({len(self._req_minute)}/{cfg.rpm})")
            return False

        if cfg.rpd > 0 and len(self._req_day) >= cfg.rpd:
            logger.debug(f"{self.provider_name}: RPD limit reached ({len(self._req_day)}/{cfg.rpd})")
            return False

        if cfg.tpm > 0:
            tokens_this_minute = sum(t for _, t in self._tok_minute)
            if tokens_this_minute >= cfg.tpm:
                logger.debug(f"{self.provider_name}: TPM limit reached ({tokens_this_minute}/{cfg.tpm})")
                return False

        if cfg.tpd > 0:
            tokens_today = sum(t for _, t in self._tok_day)
            if tokens_today >= cfg.tpd:
                logger.debug(f"{self.provider_name}: TPD limit reached ({tokens_today}/{cfg.tpd})")
                return False

        return True

    def set_cooldown(self, seconds: float) -> None:
        """Force a cooldown after receiving a 429 from the API."""
        self.cooldown_until = time.time() + seconds
        logger.info(f"{self.provider_name}: cooldown set for {seconds:.0f}s")

    def status(self) -> dict:
        self._prune()
        cfg = self.config
        tok_minute = sum(t for _, t in self._tok_minute)
        tok_day = sum(t for _, t in self._tok_day)
        return {
            "cost_class": cfg.cost_class,
            "available": self.is_available(),
            "healthy": self.healthy,
            "cooldown_until": self.cooldown_until,
            "req_last_minute": len(self._req_minute),
            "req_today": len(self._req_day),
            "tokens_last_minute": tok_minute,
            "tokens_today": tok_day,
            "limits": {
                "rpm": cfg.rpm, "rpd": cfg.rpd,
                "tpm": cfg.tpm, "tpd": cfg.tpd,
            },
        }


# ── Tier Pool config ───────────────────────────────────────────────────────────

@dataclass
class TierPoolConfig:
    """A named tier pool (nano / standard / deep / etc.) from providers.yaml."""
    name: str
    providers: list[str]
    parallelism: str = "failover"     # sequential | failover | parallel
    capability_tags: list[str] = field(default_factory=list)


# ── Pool Manager ───────────────────────────────────────────────────────────────

class PoolManager:
    """
    Unified pool state tracker.

    Tracks two independent systems:
      - GPU hardware pools (existing behaviour preserved)
      - Provider rate states (new: cost-class aware, sliding-window rate tracking)

    Integrates with PoolRouter for cost-class priority routing.
    """

    def __init__(self, pools_config: dict, providers_config: dict = None):
        # GPU pools
        self.pools: dict[str, PoolConfig] = {}
        self.health: dict[str, PoolHealth] = {}
        self._response_times: dict[str, list[float]] = {}
        self._running = False

        busy_cfg = pools_config.get("routing", {})
        self.busy_queue_threshold = busy_cfg.get("busy_threshold", {}).get("queue_depth", 2)
        self.busy_cache_threshold = busy_cfg.get("busy_threshold", {}).get("cache_usage", 0.85)
        self.poll_interval = busy_cfg.get("poll_interval", 10)
        self.pool_fallback = busy_cfg.get("pool_fallback", True)

        self._load_gpu_pools(pools_config.get("pools", {}))
        self._validate_gpu_pools()

        # Provider rate states
        self._rate_states: dict[str, ProviderRateState] = {}
        if providers_config:
            self._load_provider_states(providers_config)

        # Tier pool configs
        self._tier_pools: dict[str, TierPoolConfig] = {}

    # ── Provider Rate State API ────────────────────────────────────────────────

    def _load_provider_states(self, providers_config: dict) -> None:
        """Read rate limits + cost_class from providers.yaml provider definitions."""
        _tier_map = {
            "free": "free_limited",
            "trial": "free_limited",
            "paid": "paid_subscription",
            "unlimited": "paid_subscription",
            "local": "local",
            "free_limited": "free_limited",
            "paid_subscription": "paid_subscription",
        }
        # Provider types that are always local (no external API)
        _local_types = {"ollama", "vllm", "lm_studio", "ik_llama"}

        for name, cfg in providers_config.items():
            if not isinstance(cfg, dict):
                continue

            provider_type = cfg.get("type", "")

            # Determine cost class
            raw_class = cfg.get("cost_class") or cfg.get("access_tier", "")
            cost_class = _tier_map.get(raw_class, "")

            if not cost_class:
                if provider_type in _local_types:
                    cost_class = "local"
                elif not cfg.get("api_key") and provider_type in _local_types:
                    cost_class = "local"
                else:
                    cost_class = "unknown"

            rate_cfg = ProviderRateConfig(
                cost_class=cost_class,
                rpm=int(cfg.get("rpm", 0)),
                rpd=int(cfg.get("rpd", 0)),
                tpm=int(cfg.get("tpm", 0)),
                tpd=int(cfg.get("tpd", 0)),
            )

            self._rate_states[name] = ProviderRateState(
                provider_name=name,
                config=rate_cfg,
            )
            logger.debug(f"Rate state: {name} class={cost_class}")

    def load_tier_pools(self, tier_pools_config: dict) -> None:
        """Load tier pool definitions from providers.yaml tier_pools section."""
        for name, cfg in tier_pools_config.items():
            self._tier_pools[name] = TierPoolConfig(
                name=name,
                providers=cfg.get("providers", []),
                parallelism=cfg.get("parallelism", "failover"),
                capability_tags=cfg.get("capability_tags", []),
            )
            logger.info(f"Tier pool '{name}': {cfg.get('providers', [])}")

    def is_available(self, provider_name: str) -> bool:
        """Return True if the provider can accept a request right now."""
        state = self._rate_states.get(provider_name)
        if state is None:
            return True
        return state.is_available()

    def record_request(self, provider_name: str, tokens_used: int = 0) -> None:
        """Record a completed request against this provider's rate windows."""
        state = self._rate_states.get(provider_name)
        if state:
            state.record(tokens_used)

    def set_cooldown(self, provider_name: str, seconds: float = 60.0) -> None:
        """Force a cooldown after receiving a rate-limit error."""
        state = self._rate_states.get(provider_name)
        if state:
            state.set_cooldown(seconds)

    def get_cost_class(self, provider_name: str) -> str:
        state = self._rate_states.get(provider_name)
        return state.config.cost_class if state else "unknown"

    def best_available(self, candidates: list[str]) -> Optional[str]:
        """
        From a list of provider names, return the cheapest available one.

        Priority: local → free_limited → paid_subscription
        Falls back to first candidate if all are exhausted.
        """
        available = [p for p in candidates if self.is_available(p)]
        if not available:
            logger.warning(f"All providers exhausted: {candidates} — using first as last resort")
            return candidates[0] if candidates else None

        available.sort(key=lambda p: COST_CLASS_PRIORITY.get(self.get_cost_class(p), 99))
        return available[0]

    def ordered_pool(self, pool_name: str) -> list[str]:
        """
        Return a tier pool's providers ordered by: available first (cost-class sorted),
        then exhausted (cost-class sorted). This gives a natural failover order.
        """
        pool = self._tier_pools.get(pool_name)
        if not pool:
            return []

        available = [p for p in pool.providers if self.is_available(p)]
        exhausted = [p for p in pool.providers if not self.is_available(p)]

        available.sort(key=lambda p: COST_CLASS_PRIORITY.get(self.get_cost_class(p), 99))
        exhausted.sort(key=lambda p: COST_CLASS_PRIORITY.get(self.get_cost_class(p), 99))

        return available + exhausted

    def rate_status(self) -> dict:
        return {name: state.status() for name, state in self._rate_states.items()}

    def tier_pool_names(self) -> list[str]:
        return list(self._tier_pools.keys())

    def get_tier_pool(self, name: str) -> Optional[TierPoolConfig]:
        return self._tier_pools.get(name)

    def status(self) -> dict:
        """Combined status: GPU pools + provider rate states + tier pools."""
        gpu = {
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
        return {
            "gpu_pools": gpu,
            "provider_rate_states": self.rate_status(),
            "tier_pools": {
                name: pool.providers for name, pool in self._tier_pools.items()
            },
        }

    # ── GPU Pool (preserved) ───────────────────────────────────────────────────

    def _load_gpu_pools(self, pools_raw: dict) -> None:
        for pool_name, cfg in pools_raw.items():
            mode_str = cfg.get("mode", "independent")
            try:
                mode = PoolMode(mode_str)
            except ValueError:
                logger.warning(f"Pool '{pool_name}': unknown mode '{mode_str}', defaulting to independent")
                mode = PoolMode.INDEPENDENT

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
            self.health[pool_name] = PoolHealth(pool_name=pool_name, mode=mode.value)
            self._response_times[pool_name] = []
            logger.info(f"GPU pool '{pool_name}' loaded: mode={mode.value}")

    def _validate_gpu_pools(self) -> None:
        for pool_name, pool in self.pools.items():
            if pool.mode == PoolMode.POOLED:
                if pool.vram_per_gpu == 0:
                    logger.warning(f"GPU pool '{pool_name}': pooled mode without vram_per_gpu set.")
                if not pool.metrics_url:
                    logger.warning(
                        f"GPU pool '{pool_name}': pooled mode without metrics_url. "
                        "Load-aware routing disabled."
                    )
        pooled_vrams = {
            pool.name: pool.vram_per_gpu
            for pool in self.pools.values()
            if pool.mode == PoolMode.POOLED and pool.vram_per_gpu > 0
        }
        if len(set(pooled_vrams.values())) > 1:
            logger.warning(f"Pooled pools have different vram_per_gpu values: {pooled_vrams}.")

    def get_pool_for_provider(self, provider_name: str) -> Optional[str]:
        for pool_name, pool in self.pools.items():
            if provider_name in pool.provider_names:
                return pool_name
        return None

    def is_gpu_busy(self, provider_name: str) -> bool:
        """Legacy method: check GPU queue busy state. Use is_available() for routing."""
        if not self.pool_fallback:
            return False
        pool_name = self.get_pool_for_provider(provider_name)
        if not pool_name:
            return False
        health = self.health.get(pool_name)
        if not health:
            return False
        if time.time() - health.last_checked > self.poll_interval * 3:
            return False
        return health.is_busy

    # Keep old name as alias for backward compat
    def is_busy(self, provider_name: str) -> bool:
        return self.is_gpu_busy(provider_name)

    def record_response_time(self, provider_name: str, elapsed_ms: float) -> None:
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

    # ── vLLM metrics polling ───────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        pooled = [p for p in self.pools.values() if p.mode == PoolMode.POOLED and p.metrics_url]
        if not pooled:
            logger.info("Pool manager: no pooled GPU providers with metrics_url — polling inactive")
            return
        logger.info(f"Pool manager: polling vLLM metrics for {len(pooled)} pooled pool(s)")
        while self._running:
            for pool in pooled:
                await self._poll_metrics(pool)
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        self._running = False

    async def _poll_metrics(self, pool: PoolConfig) -> None:
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
                    logger.info(f"GPU pool '{pool.name}' busy: queue={queue_depth} cache={cache_usage:.0%}")
        except Exception as e:
            logger.debug(f"GPU pool '{pool.name}' metrics unreachable: {e}")

    @staticmethod
    def _parse_prometheus(text: str) -> dict[str, float]:
        metrics = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
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
    def from_file(cls, pools_yaml: Path, providers_config: dict = None) -> Optional["PoolManager"]:
        if not pools_yaml.exists():
            if providers_config:
                return cls.from_providers(providers_config)
            return None
        try:
            data = yaml.safe_load(pools_yaml.read_text()) or {}
            manager = cls(data, providers_config)
            logger.info(f"Pool manager loaded {len(manager.pools)} GPU pool(s) from {pools_yaml.name}")
            return manager
        except Exception as e:
            logger.error(f"Failed to load pools.yaml: {e}")
            return None

    @classmethod
    def from_providers(cls, providers_config: dict) -> "PoolManager":
        """Create a pool manager from providers config alone (no GPU pools file needed)."""
        return cls({}, providers_config)
