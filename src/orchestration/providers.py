"""Provider client — rate-limit-aware OpenAI-compatible completions for the council.

Used by the council executor and capability grader to fan out to multiple
providers in parallel. Reads provider specs from nexus's providers.yaml config
(via the provider registry) and environment variables for API keys.

Each provider gets an async token bucket to respect free-tier RPM limits.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class ProviderSpec:
    name: str             # logical name, e.g. "groq"
    base_url: str         # OpenAI-compatible /chat/completions base
    model: str            # provider model id
    api_key_env: str      # env var holding the key (None for local providers)
    rpm: int = 30         # requests/min cap for token bucket


def _load_providers_yaml() -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "providers.yaml"
    if not config_path.exists():
        return {}
    try:
        return yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return {}


def _parse_api_key_env(api_key_field: str | None) -> str | None:
    """Extract env var name from '${ENV_VAR}' template, or return as-is if plain."""
    if not api_key_field:
        return None
    s = str(api_key_field).strip()
    if s.startswith("${") and s.endswith("}"):
        return s[2:-1]   # '${GROQ_API_KEY}' -> 'GROQ_API_KEY'
    if s.startswith("$"):
        return s[1:]     # '$GROQ_API_KEY' -> 'GROQ_API_KEY'
    return s             # plain env var name already


def _build_registry() -> Dict[str, ProviderSpec]:
    """Build the council provider registry from providers.yaml config."""
    config = _load_providers_yaml()
    registry: Dict[str, ProviderSpec] = {}

    for provider_id, spec in config.get("providers", {}).items():
        # Only OpenAI-compatible providers with a base_url can join the council
        base_url = spec.get("base_url", "")
        if not base_url:
            continue

        api_key_raw = spec.get("api_key") or spec.get("api_key_env") or spec.get("api_key_var")
        api_key_env = _parse_api_key_env(api_key_raw)

        model = spec.get("model", "")
        rpm = int(spec.get("rpm") or 30)
        enabled = spec.get("enabled", True)

        if not enabled or not model:
            continue

        registry[provider_id] = ProviderSpec(
            name=provider_id,
            base_url=base_url.rstrip("/"),
            model=model,
            api_key_env=api_key_env,
            rpm=rpm,
        )

    return registry


# Build once at module load. Council uses whatever is in providers.yaml.
REGISTRY: Dict[str, ProviderSpec] = _build_registry()


class _RateLimiter:
    """Per-provider async token bucket. Refills rpm tokens over 60s."""

    def __init__(self, rpm: int):
        self.capacity = max(1, rpm)
        self.tokens = float(self.capacity)
        self.refill_per_sec = self.capacity / 60.0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self._last) * self.refill_per_sec,
            )
            self._last = now
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.refill_per_sec
                await asyncio.sleep(wait)
                self.tokens = 0.0
                self._last = time.monotonic()
            else:
                self.tokens -= 1.0


@dataclass
class ProviderClient:
    """Async client over the registry with per-provider rate limiting."""
    registry: Dict[str, ProviderSpec] = field(default_factory=lambda: dict(REGISTRY))
    timeout: float = 90.0
    _limiters: Dict[str, _RateLimiter] = field(default_factory=dict)

    def __post_init__(self):
        for name, spec in self.registry.items():
            self._limiters[name] = _RateLimiter(spec.rpm)

    def available(self) -> List[str]:
        """Providers whose API key is present in the environment (or local — no key)."""
        result = []
        for n, s in self.registry.items():
            if s.api_key_env is None or os.getenv(s.api_key_env):
                result.append(n)
        return result

    def missing_keys(self) -> List[str]:
        return sorted({
            s.api_key_env for s in self.registry.values()
            if s.api_key_env is not None and not os.getenv(s.api_key_env)
        })

    async def complete(self, provider: str, prompt: str,
                       system: Optional[str] = None) -> str:
        """Single completion from one provider. Raises on HTTP or auth error."""
        import httpx

        spec = self.registry[provider]
        key = os.getenv(spec.api_key_env) if spec.api_key_env else None
        if spec.api_key_env and not key:
            raise RuntimeError(f"{provider}: missing env var {spec.api_key_env}")

        await self._limiters[provider].acquire()

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        messages = (
            ([{"role": "system", "content": system}] if system else []) +
            [{"role": "user", "content": prompt}]
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{spec.base_url}/chat/completions",
                headers=headers,
                json={"model": spec.model, "messages": messages},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    async def fan_out(self, providers: List[str],
                      prompt: str) -> Dict[str, object]:
        """Query several providers in parallel. Returns {provider: text|Exception}."""
        results = await asyncio.gather(
            *[self.complete(p, prompt) for p in providers],
            return_exceptions=True,
        )
        return dict(zip(providers, results))


def health_summary() -> Dict[str, object]:
    """No-network readiness check: which providers have keys configured."""
    c = ProviderClient()
    return {
        "available": c.available(),
        "missing_keys": c.missing_keys(),
        "registry": sorted(c.registry.keys()),
    }
