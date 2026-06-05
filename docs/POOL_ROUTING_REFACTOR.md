# Pool-Based Routing Refactor — Implementation Plan

**Scope:** Unify Nexus provider routing from single-assignment to organizational (pool-based, cost-aware, rate-limited) model.

**Status:** Planning (2026-06-04)

---

## Overview

Convert Nexus from:
```
message → triage(tier) → route(default_provider) → invoke
```

To:
```
message → triage(tier + urgency + capability + value)
        → pool_router(tier_pool + cost_class + rate_state)
        → select_best_from_pool(availability + cost priority)
        → invoke_with_failover
        → record_state(tokens, outcome)
```

---

## Changes Required

### 1. `pool_manager.py` — Extend to Provider Rate Tracking

**Current state:** GPU pool health tracking only (queue depth, cache usage)

**New state:** Add provider rate-limit tracking alongside GPU pools

```python
@dataclass
class ProviderState:
    """Runtime state of a provider."""
    provider_name: str
    cost_class: str              # "local" | "free_limited" | "paid_subscription"
    
    # Rate limits (from providers.yaml)
    rpm_limit: int               # requests per minute
    rpd_limit: int               # requests per day
    tpm_limit: int               # tokens per minute
    tpd_limit: int               # tokens per day
    
    # Current usage (tracked at runtime)
    requests_in_minute: int      # requests in last 60s
    requests_in_day: int         # requests in last 24h
    tokens_in_minute: int        # tokens consumed in last 60s
    tokens_in_day: int           # tokens consumed in last 24h
    
    # State
    healthy: bool
    exhausted: bool              # rate limit hit
    cooldown_until: float        # epoch timestamp if rate-limited
    last_request_at: float
```

**New methods:**
- `is_available(provider_name) -> bool` — check rate limits, health, cooldown
- `record_request(provider_name, tokens_used: int) -> None` — update counters
- `get_best_available(tier_pool: list[str]) -> Optional[str]` — select from pool by cost class + availability
- `pool_status() -> dict` — report current state of all providers

**No breaking changes to GPU pool tracking.** GPU pools and provider pools coexist.

---

### 2. `providers.yaml` — Pool-Based Routing Config

**Current:**
```yaml
routing:
  default: cerebras
  triage: groq
```

**New:**
```yaml
# Provider definitions (existing, plus cost_class field)
providers:
  cerebras:
    type: openai
    model: qwen-3-235b-a22b-instruct-2507
    tier: standard
    cost_class: free_limited     # NEW
    rpm: 30
    rpd: 14400
    tpm: 60000
    tpd: 1000000
  
  groq:
    type: openai
    model: llama-3.1-8b-instant
    tier: nano
    cost_class: free_limited     # NEW
    rpm: 30
    tpm: 6000
    tpd: 500000
  
  ollama_local:
    type: ollama
    model: llama3.1:8b
    tier: standard
    cost_class: local            # NEW
    endpoint: http://localhost:11434
  
  claude_opus:
    type: anthropic
    model: claude-opus-4
    tier: deep
    cost_class: paid_subscription # NEW
    rpm: 20
    tpm: 100000

# Tier pools (NEW)
tier_pools:
  nano:
    providers: [ollama_local, groq, cerebras]    # local first, free-tier, fallback
    parallelism: sequential                       # or "parallel"
    capability_tags: [general, code, search]
  
  standard:
    providers: [ollama_local, cerebras, claude_sonnet]
    parallelism: failover
    capability_tags: [general, code, reasoning, search]
  
  deep:
    providers: [ollama_cluster, claude_opus]
    parallelism: failover
    capability_tags: [reasoning, code, deep_analysis]

# Routing (NEW)
routing:
  default_pool: standard
  triage_pool: nano
  research_pool: deep
  code_pool: standard
  fallback: claude_opus
```

**Key changes:**
- `tier_pools` replaces `default`/`triage` single assignments
- Each pool is a list (cost-class priority order: local → free → paid)
- Pool has `parallelism` hint: sequential vs failover vs parallel
- Capability tags hint what the pool is suited for

---

### 3. `triage.py` — Extended Classification

**Current `TriageResult`:**
```python
@dataclass
class TriageResult:
    task_type: str
    priority: str
    is_command: bool
    command: Optional[str]
    confidence: float
```

**New:**
```python
@dataclass
class TriageResult:
    task_type: str                           # code | research | support | chat | system
    priority: str                            # high | normal | low
    is_command: bool
    command: Optional[str]
    confidence: float
    
    # NEW FIELDS
    urgency: str = "normal"                  # immediate | normal | deferred
    task_value: str = "routine"              # routine | important | critical
    capability_required: str = "general"     # general | code | search | reasoning | voice
    estimated_complexity: str = "nano"       # nano | standard | deep
```

**Updated `_llm_classify()` prompt:**
```
Classify this message:
- Category: code, research, support, chat, system
- Urgency: immediate (need response <2s), normal (up to 10s), deferred (no urgency)
- Task Value: routine, important (high-stakes), critical (blocks others)
- Capability: general, code, search, reasoning, voice
- Complexity: nano (small model ok), standard (mid model), deep (frontier)

Reply format: category|urgency|value|capability|complexity

Message: {message}
```

**Urgency logic:**
- `immediate` → prefer local (low latency) or very fast free-tier (Groq)
- `normal` → standard cost-class routing
- `deferred` → prefer free-tier and local (no time pressure)

---

### 4. `pool_router.py` (NEW) or Enhanced `router.py`

**Responsibility:** Select from tier pool based on triage + pool state

```python
class PoolRouter:
    """Routes based on tier pools + provider availability + cost class."""
    
    def __init__(
        self,
        providers: dict[str, BaseProvider],
        pool_manager: PoolManager,
        routing_config: dict
    ):
        self.providers = providers
        self.pool_manager = pool_manager
        self.tier_pools = routing_config.get("tier_pools", {})
        self.routing_rules = routing_config.get("routing", {})
    
    async def select_provider(
        self,
        triage: TriageResult
    ) -> BaseProvider:
        """
        Select a provider from the appropriate tier pool.
        
        1. Determine which pool based on capability_required
        2. Query pool_manager for available providers in pool
        3. Select by cost class priority: local → free → paid
        4. Return selected provider
        """
        # Determine pool
        pool_name = self._select_pool(triage)
        pool_providers = self.tier_pools.get(pool_name, [])
        
        if not pool_providers:
            # Fallback to default pool
            pool_name = self.routing_rules.get("default_pool", "standard")
            pool_providers = self.tier_pools.get(pool_name, [])
        
        # Find best available in pool
        available = []
        for provider_name in pool_providers:
            if self.pool_manager.is_available(provider_name):
                available.append(provider_name)
        
        if not available:
            # All exhausted — pick least-exhausted by rate window recovery time
            return self.providers[pool_providers[0]]
        
        # Cost-class priority: local > free > paid
        selected = self._select_by_cost_class(available)
        return self.providers[selected]
    
    def _select_pool(self, triage: TriageResult) -> str:
        """Determine which tier pool to use."""
        if triage.is_command:
            return "command"
        
        # Check capability routing
        capability = triage.capability_required
        capability_pools = {
            "code": "code_pool",
            "search": "research_pool",
            "reasoning": "deep_pool",
            "voice": "voice_pool",
        }
        
        if capability in capability_pools:
            return self.routing_rules.get(capability_pools[capability], "standard")
        
        return self.routing_rules.get("default_pool", "standard")
    
    def _select_by_cost_class(self, available_providers: list[str]) -> str:
        """Sort by cost class priority: local → free → paid."""
        cost_priority = {
            "local": 0,
            "free_limited": 1,
            "paid_subscription": 2,
        }
        
        sorted_by_cost = sorted(
            available_providers,
            key=lambda p: cost_priority.get(
                self._get_cost_class(p), 999
            )
        )
        
        return sorted_by_cost[0]
    
    def _get_cost_class(self, provider_name: str) -> str:
        provider = self.providers.get(provider_name)
        if hasattr(provider, "cost_class"):
            return provider.cost_class
        return "unknown"
```

---

### 5. `bridge.py` — Rate-Aware Invocation

**Current:** Single invoke, no rate checking

**New:**
```python
async def invoke_with_failover(
    self,
    messages: list,
    system: str,
    provider_name: str = None,
    tier_pool: list[str] = None,
) -> ProviderResponse:
    """
    Invoke with failover across tier pool if rate limits hit.
    """
    
    if not provider_name and tier_pool:
        # Select from pool
        provider_name = await self.pool_router.select_provider(...)
    
    pool = tier_pool or [provider_name]
    
    for attempt, prov_name in enumerate(pool):
        try:
            # Check availability before invoke
            if not self.pool_manager.is_available(prov_name):
                logger.debug(f"{prov_name} not available (rate limit)")
                continue
            
            # Invoke
            response = await self.providers[prov_name].send(messages, system)
            
            # Record successful request
            tokens_used = self._estimate_tokens(response.content)
            self.pool_manager.record_request(prov_name, tokens_used)
            
            return response
        
        except RateLimitError as e:
            # Mark provider exhausted
            self.pool_manager.mark_exhausted(prov_name, cooldown_secs=60)
            
            if attempt < len(pool) - 1:
                logger.info(f"{prov_name} rate-limited; trying next in pool")
                continue
            else:
                raise  # Last in pool, propagate error
```

---

### 6. `adapters/*.py` — Pool Awareness

**Mattermost/Discord/Telegram adapters:**

Before calling `bridge.invoke()`, pass triage result including pool info:

```python
async def _handle_message(self, message: str):
    # Classify message
    triage = await self.triage.classify(message)
    
    # Invoke with tier pool support
    response = await self.bridge.invoke(
        messages=[...],
        system="...",
        triage=triage,  # Pass full triage result
    )
```

---

## Implementation Order

1. **Phase 1: Pool Manager State**
   - [ ] Extend `pool_manager.py` with `ProviderState`
   - [ ] Add rate limit tracking methods
   - [ ] Wire into `BaseProvider` init (read cost_class from config)

2. **Phase 2: Config & Routing**
   - [ ] Update `providers.yaml` with `cost_class` and `tier_pools`
   - [ ] Create `pool_router.py` (NEW)
   - [ ] Update `router.py` to use `pool_router` or refactor entirely

3. **Phase 3: Triage**
   - [ ] Extend `TriageResult` with urgency/value/capability
   - [ ] Update `_llm_classify()` prompt
   - [ ] Update keyword fallback heuristics

4. **Phase 4: Bridge & Invoke**
   - [ ] Extend `bridge.py` with `invoke_with_failover()`
   - [ ] Wire `pool_manager` check before invoke
   - [ ] Implement rate limit error handling

5. **Phase 5: Adapter Integration**
   - [ ] Update Mattermost adapter to pass triage to bridge
   - [ ] Update Discord adapter
   - [ ] Update Telegram adapter

6. **Phase 6: Testing & Validation**
   - [ ] Unit tests for pool_manager rate tracking
   - [ ] Unit tests for pool_router selection logic
   - [ ] Integration test: message flow through full pipeline

---

## Configuration Example (Final State)

```yaml
# config/providers.yaml

providers:
  # LOCAL
  ollama_nano:
    type: ollama
    model: phi4-mini
    cost_class: local
    tier: nano
    endpoint: http://localhost:11434

  # FREE-TIER
  groq:
    type: openai
    cost_class: free_limited
    model: llama-3.1-8b-instant
    tier: nano
    rpm: 30
    tpm: 6000
    tpd: 500000

  cerebras:
    type: openai
    cost_class: free_limited
    model: qwen-3-235b-a22b-instruct-2507
    tier: standard
    rpm: 30
    tpm: 60000
    tpd: 1000000

  # PAID
  claude_sonnet:
    type: anthropic
    cost_class: paid_subscription
    model: claude-sonnet-4-6
    tier: standard

  claude_opus:
    type: anthropic
    cost_class: paid_subscription
    model: claude-opus-4
    tier: deep

# Tier pools: cost-class priority order (local → free → paid)
tier_pools:
  nano:
    providers: [ollama_nano, groq, cerebras]
    parallelism: sequential
  
  standard:
    providers: [cerebras, claude_sonnet]
    parallelism: failover
  
  deep:
    providers: [claude_opus]
    parallelism: sequential

routing:
  default_pool: standard
  code_pool: standard
  research_pool: deep
  fallback: claude_opus
```

---

## Success Criteria

- [ ] Router selects from tier pool, not single provider
- [ ] Rate limit state tracked per provider, per window
- [ ] Failover works: exhausted provider → next in pool
- [ ] Triage includes urgency + value + capability
- [ ] Pool selection respects cost-class priority
- [ ] No paid tokens burn on tasks free/local could handle
- [ ] All four existing adapters pass stress test
- [ ] Config is readable and easy to modify

---

## Risks & Mitigation

**Risk:** Breaking change to config structure
- **Mitigation:** Provide migration script; support old format with deprecation warning

**Risk:** Rate limit tracking adds memory overhead
- **Mitigation:** Store state in SQLite like triage_validator does; trim windows regularly

**Risk:** Parallel dispatch in pools increases complexity
- **Mitigation:** Start with sequential; add parallel as opt-in feature per pool

---

## Timeline

- **Planning:** 2026-06-04 ✓
- **Phase 1-3 (Core):** 3-4 hours
- **Phase 4-5 (Integration):** 2-3 hours
- **Phase 6 (Testing):** 2 hours
- **Total:** ~8-10 hours concentrated work
