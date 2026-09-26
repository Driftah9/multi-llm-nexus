# Worker Health Gate — Implementation & Integration

**Date:** 2026-09-25 · **Status:** Complete and tested (25 tests, 267 total suite)

## Overview

The worker health gate solves a measured swarm failure: when top-ranked workers are unreachable
(auth failure, model decommissioned, quota exhausted), they consume the entire retry budget
before healthy workers get a chance. This was measured on the live install 2026-09-04:
10-provider pool, 1 reachable; swarm step completion decayed 100% → 0/11 with no error.

The gate keeps dead providers out of the swarm's worker selection loop by tracking transport
failures (404, 403, 503, etc.) and benching workers with appropriate cooldowns.

## Implementation

### Module: `src/orchestration/worker_health.py` (275 lines)

**Core functions:**

| Function | Purpose |
|----------|---------|
| `record_failure(worker, err)` | Classify an error and bench the worker if needed |
| `record_success(worker)` | Clear any bench (traffic-driven recovery) |
| `is_benched(worker)` | Check if a worker should be skipped right now |
| `filter_candidates(candidates)` | Drop benched workers from a candidate list |
| `benched()` | List all currently-benched workers (for monitoring) |

**Key design:**

- **Separate from quality ranking** — `capability_map` scores how GOOD providers are;
  `worker_health` scores whether they ANSWER. Conflating them corrupts both.
- **Class-based cooldowns** — bench duration fits the failure type:
  - `MODEL_GONE` (404/410): 24h (only config edit fixes it)
  - `AUTH` (403): 6h (only operator with new key fixes it)
  - `QUOTA` (402): 1h (resets on its own clock)
  - `TRANSIENT` (5xx, timeout): 5min (blip recovers fast)
  - `NOT_CONFIGURED`: 24h (only operator adding credentials fixes it)
- **Two-strike rule** — deterministic failures (auth, model gone) bench on first; transients
  need two consecutive before benching (single blip never costs a place in rotation).
- **Backoff after 3** — permanently-dead providers have cooldown double (capped at 24h) to
  avoid re-probing on a fixed short cycle.
- **Fail-open** — if all candidates are benched, returns them anyway. A health gate must
  never be the reason a task has no workers.
- **State on disk** — JSON registry (`data/worker_health.json`, env-overridable) persists
  across runs.

### Error Classification

Refines `src/core/error_classifier.py` for HTTP status codes the base classifier misses:
- 404/410 → `MODEL_GONE` (endpoint retired)
- 402 → `QUOTA` (payment required)
- 5xx → `TRANSIENT` (server blip)

Also unwraps "all credentials exhausted" wrapper to classify the *real* underlying error.

### Integration: `src/orchestration/swarm_loop.py`

**Changes to `_execute_step()`:**

```python
# 1. Get candidates ranked by capability for the domain
raw_candidates = worker_candidates_fn(step.domain)

# 2. Filter out already-tried ones (existing logic)
candidates = [c for c in raw_candidates if c not in tried]

# 3. Apply health gate — skip benched workers (NEW)
candidates = worker_health.filter_candidates(candidates)

# ... route to best candidate ...

# 4. On success — clear any bench (NEW)
worker_health.record_success(provider)

# 5. On failure — record for future filtering (NEW)
worker_health.record_failure(provider, e)
```

This integrates with existing routing without changing the core loop logic. The health gate
is transparent: if no workers are benched, filtering is a no-op.

## Testing

**File:** `tests/test_worker_health.py` (25 tests, 380 lines)

### Test categories

| Category | Tests | Covers |
|----------|-------|--------|
| Classification | 9 | Real error texts from production (404, 410, 403, 503, 429, 402) |
| Benching policy | 8 | Deterministic vs transient; two-strike rule; backoff; cooldown by class |
| Candidate filtering | 7 | Dropping benched workers; fail-open; order preservation |
| Regression | 1 | The measured failure mode: dead top-ranked workers starving healthy ones |

**Example test:**

```python
def test_regression_dead_top_ranked_workers_dont_starve_healthy_ones():
    """Live measurement 2026-09-04: without the gate, swarm step completion
    decayed 100% → 0/11 because two dead top-ranked providers consumed the
    entire MAX_STEP_RETRIES budget.
    """
    for _ in range(5):
        record_failure("cerebras-qwen3", "404 Not Found")
    for _ in range(5):
        record_failure("groq-70b", "403 Forbidden")

    pool = ["cerebras-qwen3", "groq-70b", "gemini-flash", "openai-gpt4o", "ollama-local"]
    filtered = filter_candidates(pool)
    
    assert "cerebras-qwen3" not in filtered
    assert "groq-70b" not in filtered
    assert len(filtered) == 3
```

### Test suite status

- **267 total tests**: all passing
- **25 worker_health-specific**: all passing
- **15 swarm_loop tests**: all passing (now with health gate integrated)

## Configuration

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKER_HEALTH_PATH` | `data/worker_health.json` | Override disk location |
| `NEXUS_DATA_DIR` | `./data/` | Data directory root |

### Enabling in the swarm

The health gate is **always active** in `_execute_step()`. No flag needed — it's passive when
no workers are benched.

To enable swarm loop itself:
```bash
export SWARM_LOOP_ENABLED=1
```

## Monitoring

The `benched()` function provides current state for the ops board:

```python
from src.orchestration import worker_health

recs = worker_health.benched()
for r in recs:
    print(f"{r['worker']}: {r['klass']} until {r['benched_until']}")
```

Output:
```
cerebras-qwen3: model_gone until 1726704000.5
groq-70b: auth until 1726689600.2
gemini-flash: transient until 1726604400.1
```

## Key differences from live

Live's `worker_health.py` also reads a shared vendor registry (`core/provider_health.py`)
maintained by the primary seat's failover logic. Nexus doesn't have that cross-check yet
(no primary-seat concept), so:

- Benches are per-worker, local to the swarm pool (simpler, still solves the measured bug)
- Future enhancement: integrate with `core/provider_health.py` if that module is ported

## Deployment notes

1. **No service restart needed** — the gate is integrated into the existing swarm loop
2. **Backward compatible** — if no workers are benched, behavior is unchanged
3. **State is ephemeral** — `data/worker_health.json` can be deleted anytime; benches rebuild
   from transport failures in the next run
4. **Scaling** — with the gate, swarms using 100+ providers (OpenRouter, multi-cloud setups)
   stay efficient even when top-ranked ones are temporarily down
