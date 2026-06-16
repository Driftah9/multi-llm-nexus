# Claude-Brain Multi-Provider Orchestration — System Design
**Status:** Design Phase (locked, awaiting council research)  
**Date:** 2026-06-09  
**Author:** Stryder + Claude  
**Audience:** Anyone building/extending the provider orchestration layer

This is the single source of truth for how the system routes messages to providers, tracks usage, handles failures, and benchmarks providers over time. **Do not build without alignment here.**

---

## 1. Core Philosophy

**The Company Model:** Think of Claude-Brain as a company. Each AI provider is an onboarded contractor. They each have:
- A skillset (which models/capabilities they offer)
- Rate of pay (cost per token)
- A contract (session limits, monthly caps, restrictions)
- A performance record (the council tracks what they're good at)

The orchestrator is the dispatcher. It:
1. **Understands the work** (triage classifies the task)
2. **Consults the roster** (who's available and capable?)
3. **Dispatches** (pick the cheapest/best available)
4. **Logs the work** (track usage, failures, performance)
5. **Hands off when needed** (provider hits a limit → next in queue)
6. **Reviews performance** (council scores them over time)

**Greedy execution, not conservative:** The system runs hot. It uses each provider until it hits a real limit (rate-limit 429, quota exhausted, window closed), then hands off. No preemptive reservation. No "save Claude for later." Go until you can't, then chain to the next.

---

## 2. Provider Onboarding (Dossier Schema)

When a provider is added, it gets a dossier. This schema captures what we need to know:

```yaml
provider:
  name: "Anthropic"                    # Canonical name
  type: "api_key | cli_subscription | gateway"
  
  models:
    - id: haiku-3-5                   # Canonical model ID
      name: "Haiku 3.5"               # Display name
      tier: nano                      # nano | standard | deep
      capabilities: [chat, research, summary]  # What it does well
      context_window: 8000            # Max tokens per request
      cost_per_1m_tokens: 0.80        # Input only; clarify if different for output
      restrictions: []                # ["no_vision", "no_function_calling"]
      
      limits:
        rpm: null                     # null = not applicable (CLI)
        rpd: null
        tpm: null
        tpd: null
        session_window_minutes: 300   # 5 hours = 300 min, for Anthropic
        session_window_tokens: null   # null = unknown; estimated post-hoc
        weekly_limit_all_models: true # Anthropic: yes, all models share weekly
        weekly_limit_this_model_only: false  # Anthropic Sonnet: also has Sonnet-only cap
      
      known_weaknesses: [complex_reasoning_speed]  # Council will refine this
      
    - id: sonnet-4-6
      name: "Sonnet 4.6"
      tier: standard
      capabilities: [chat, research, analysis, code, reasoning]
      context_window: 200000
      cost_per_1m_tokens: 3.00
      restrictions: []
      limits:
        session_window_minutes: 300
        weekly_limit_all_models: true
        weekly_limit_this_model_only: true  # Sonnet has its own weekly cap
      
    - id: opus-4-8
      name: "Opus 4.8"
      tier: deep
      capabilities: [complex_reasoning, research, analysis, strategic_planning]
      context_window: 200000
      cost_per_1m_tokens: 15.00
      restrictions: []
      limits:
        session_window_minutes: 300
        weekly_limit_all_models: true
        weekly_limit_this_model_only: false

  access_tier: "subscription"         # free | trial | subscription | enterprise
  subscription_plan: "Max"            # Pro | Max | Ultra
  
  authentication:
    type: "env_var | api_key | oauth"
    required_env: ["ANTHROPIC_API_KEY"]  # or for CLI: none (uses ~/.claude/config)
  
  failure_mode: "throws_error | returns_429 | silent_timeout"
  failure_signal: "exception | http_status | timeout_seconds"
  
  documentation_url: "https://docs.anthropic.com"
  last_updated: "2026-06-09"
```

**For each API provider (Groq, Cerebras, Gemini, etc.):**
- Rate limits are known (RPM, RPD, TPM, TPD from their docs)
- Monthly caps are known
- Failure signals are HTTP 429 + rate-limit headers

**For Claude (CLI subscription):**
- Session windows are known (5 hours, 3 of them per Max plan)
- Weekly limits are known but **not directly queryable**
- Failures are exceptions, not 429s
- We reconstruct the window from token usage logs + your calibration anchors

---

## 3. Roster Structure

The roster is a **provider/model registry grouped by tier**. Within each tier, models are ordered by cost (cheap first).

```
nano tier (fast triage, low token burn):
  1. Haiku 3.5 (Anthropic) — session window, free to you if on Max
  2. Gemini Flash (Google) — 250K TPM free, no auth needed
  3. Llama 8B (Groq) — 500K TPD free
  4. DeepSeek V4 Flash — cheapest paid option

standard tier (general work, reasoning):
  1. Sonnet 4.6 (Anthropic) — session window + weekly limit (different from Haiku)
  2. GPT-4o (OpenAI) — if you add it
  3. Gemini Pro (Google)
  4. Llama 70B (Groq)
  5. DeepSeek V4

deep tier (complex reasoning, strategic, architectural):
  1. Opus 4.8 (Anthropic) — session window + weekly limit
  2. GPT-4-Turbo (OpenAI)
  3. SambaNova 405B — largest free model
  4. Local 70B (if vLLM available)

fallback tier (always available, use when all tiers exhausted):
  1. Haiku (Anthropic) — always available on Max plan, last resort
  2. [Any other high-headroom provider you add]
```

**Tier assignment logic:**
- **nano:** Models <13B params, <1s latency expected, designed for classification/routing
- **standard:** Models 13–70B, <5s latency, handles reasoning and analysis
- **deep:** Models 70B+, reasoning-optimized, accepts slower latency for quality

Within a tier, cost order is the default. The **council** can re-order based on use-case performance (see §6).

---

## 4. The Roster Check Algorithm

When a message arrives:

```
Message received
  ↓
Haiku: classify(message)
  → task_type (chat, research, code, support, system)
  → urgency (immediate | normal | deferred)
  → estimated_complexity (nano | standard | deep)
  → capability_needed (general | code | search | reasoning | voice | rag)
  ↓
For tier in [estimated_complexity, standard, deep, fallback]:
  For model in tier (cost order, optionally re-ranked by council):
    Check: can_handle_task(model, task_type, capability)?
      → No: skip to next model
      → Yes: continue
    
    Check: within_rate_limits(model)?  [RPM, TPM, RPD, TPD windows]
      → No (window full): skip to next model
      → Yes: continue
    
    Check: not_exhausted_monthly(model)?  [Monthly cap]
      → No (monthly cap hit): skip to next model, model is benched for the month
      → Yes: continue
    
    Check: if multi-window provider (Anthropic), session_window_available(model)?
      → No: skip to next model
      → Yes: DISPATCH
    
    DISPATCH(model)
    Record call (timestamp, input_tokens, output_tokens, cost, success/failure)
    return success
  
  # No viable model in this tier, escalate to next tier

# Fallback tier exhausted (should be rare; means you're out of quota everywhere)
# This shouldn't happen because Haiku (Anthropic) is always available on Max plan
Log WARN: All tiers exhausted; cannot fulfill request
return error
```

**Key principle: Greedy.** Go until the provider's limit hits. No soft-bench at 80%. When a provider fails (returns 429, rate-limit, quota exhausted), that failure is **caught and logged**, then the next model in the roster is tried. The failover is **announced** in-channel (fail loud).

---

## 5. Session Ledger Tracking (Quota Management)

Each provider/model has one or more **ledgers** — rolling windows of usage.

### API Key Providers (Groq, Cerebras, Gemini, etc.)
Clean: hard limits from their APIs, sliding windows.

```python
class ProviderRateLimit:
  rpm: 30                           # Requests per minute
  rpd: 14_400                       # Requests per day
  tpm: 60_000                       # Tokens per minute
  tpd: 1_000_000                    # Tokens per day
  monthly_tokens: null              # null = no monthly cap
  monthly_requests: 1_000           # Cohere: yes; most: null
  
  # Buckets (sliding windows)
  minute_bucket: (requests: N, tokens: M, window_start: epoch)
  day_bucket: (requests: N, tokens: M, window_start: epoch)
  month_bucket: (requests: N, tokens: M, window_start: epoch)
  
  def can_use(self) -> bool:
    # Check minute window: if (now - window_start) < 60s, requests/tokens vs limits
    # Check day window: same
    # Check month window: same
    return all_windows_have_headroom
  
  def headroom(self) -> float:  # 0.0 = exhausted, 1.0 = fresh
    # Return the tightest constraint (min of all window percentages)
    return min(headroom_rpm, headroom_tpm, headroom_rpd, headroom_tpd)
```

### Anthropic (CLI subscription) — The Special Case
**Problem:** No quota introspection. You can't ask Claude "how many tokens have I used this hour?" The window is invisible.

**Solution:** Reconstruct it from logs.

```python
class AnthropicSessionLedger:
  # Three ledgers per Max plan
  
  # 1. Session window (5 hours, rolling)
  session_window: {
    model_id: "haiku-3-5" | "sonnet-4-6" | "opus-4-8",
    start_time: epoch,
    duration_minutes: 300,  # 5 hours
    tokens_used: [log of all calls in window],
    estimated_max: null,    # estimated from your calibration anchors
    percent_used: 0.50,     # You said "50% used, 2h52m left"
    last_calibration: {timestamp, percent_used},  # Your manual anchor
  }
  
  # 2. Weekly limit (all models shared)
  weekly_all_models: {
    start_epoch: (Monday midnight this week),
    tokens_used: [sum of haiku + sonnet + opus calls this week],
    estimated_max: null,    # Estimated from Anthropic docs or your calibration
  }
  
  # 3. Weekly limit (Sonnet only — Anthropic has a per-model cap)
  weekly_sonnet_only: {
    start_epoch: (Monday midnight this week),
    tokens_used: [sum of sonnet calls only],
    estimated_max: null,
  }
  
  def calibrate(percent_used: float, resets_in_minutes: int):
    """
    You provide: "I've used 50%, resets in 172 minutes"
    System fits the estimate: if 50% resets in 172 min, and the window is 300 min,
    then we're somewhere in the cycle. Store this anchor.
    """
    session_window.last_calibration = {timestamp: now(), percent_used}
    session_window.estimated_max = self._fit_estimate(percent_used, resets_in_minutes)
  
  def can_use(self, model_id: str) -> bool:
    """
    Can this model accept a request right now?
    Check: session window headroom, weekly-all headroom, weekly-model-only headroom
    """
    session_ok = self.session_window.percent_used < 0.95  # Safety margin
    weekly_all_ok = self.weekly_all_models.percent_used < 0.95
    weekly_model_ok = self.weekly_sonnet_only.percent_used < 0.95 if model_id == "sonnet" else True
    return all([session_ok, weekly_all_ok, weekly_model_ok])
  
  def should_conserve(self, model_id: str) -> bool:
    """
    Are we close to a limit? Return True if approaching 80% on any window.
    Used by orchestrator to route non-critical work elsewhere.
    """
    ...
```

**Calibration is manual but lightweight:** Every few hours, Stryder mentally notes "I've used ~60%, resets in 2 hours" and the system logs that. Over a week, the system learns the actual window sizes and usage patterns. By month 2, the estimates are tight.

---

## 6. The Council — Provider Reputation & Routing

**Mechanism (under research — see concurrent search)**

The council is a benchmarking system that tracks:
- **Who's good at what:** (provider, task_type) → success_rate, accuracy_score, speed
- **Performance trends:** Is a provider getting better or worse?
- **Reputation by use-case:** DeepSeek good at math, weak at creative writing; Gemini strong at summaries, slower on code.

**Inputs:**
- **Active:** Run the same task against multiple providers, compare outputs, score them. (Expensive, ~once/week per task category.)
- **Passive:** Score from normal traffic. When multiple providers handle similar tasks, log relative quality. (Free, but slower signal.)

**Outputs:**
- **Routing influence:** High-reputation providers get bumped up in tier order within that use-case. *But never removed* — a low-score in domain X doesn't bench them; it just demotes them from "primary" to "support."
- **Visibility:** Reports show "DeepSeek: 94% accuracy on math problems, 60% on creative writing" → operators see who's reliable where.

**Council decisions:**
1. Does reputation re-order models within a tier per use-case? (Likely yes: "For research tasks, try Gemini before Groq.")
2. Is reputation use-case-specific or global? (Use-case: research, code, summary, analysis, etc.)
3. How often do we update reputation? (Weekly? Monthly?)
4. What's the confidence threshold to re-order? (Need 30 samples? Statistical significance?)

**Research status:** Currently researching existing "LLM council / ensemble" patterns to see if there's prior art. Will update this section with mechanisms once research is in.

---

## 7. Failure Handling & Logging

**Fail loud, always log.**

When a provider fails (returns 429, times out, throws error, quota exhausted):

```python
except ProviderError as e:
  # Log the failure
  log_failure(provider=model, error=e, partial_output=result_so_far)
  
  # Announce in-channel (fail loud)
  send_to_channel(
    f"⚠️ {model.provider} hit limit (429 rate-limit) while researching.\n"
    f"Handing to {next_model.provider}. Partial progress saved."
  )
  
  # If partial output exists (mid-research), preserve it
  save_partial(result_so_far, checkpoint=True)
  
  # Failover to next model in roster
  return invoke_with_fallback(next_model)
```

Never silently drop a request or hide a failure. The operator always knows who played, why they came out, and who came in to bat.

---

## 8. Architectural Pieces (Implementation Notes)

**In `claude-brain/adapters/`:**
1. **Dossier registry** — YAML or JSON file per provider (or one consolidated registry)
2. **Roster check algorithm** — `orchestration/roster_check.py` (the core dispatcher)
3. **Session ledger tracker** — `core/session_ledger.py` (Claude) + per-provider rate buckets
4. **Council interface** — placeholder for now; updated once research is in
5. **Failure handler** — extend `core/error_handler.py` to catch, log, announce, failover
6. **Visibility layer** — heartbeat status, in-channel failure announcements

**In `multi-llm-nexus/`:**
- The same structure applies (already partially there in `provider_quota.py`, `pool_router.py`)
- Nexus becomes the OSS version of this design (no personal keys, abstract provider class)

---

## 9. Decision Points (Awaiting Input)

1. **Council mechanism** — Once research is back, decide: active benchmarking, passive, or hybrid? How often? Statistical confidence threshold?
2. **Calibration UX for Claude** — Does "Stryder, what's your current window usage?" appear as a channel command you run every few hours? Or is it manual/ad-hoc?
3. **Tier re-ordering by council** — Within a tier, does council reputation re-order the roster? Or is it advisory only?
4. **Failure announcement detail** — How much detail in the "X failed, handing to Y" message? (Brief, or full error?)
5. **Monthly cap enforcement** — When a provider hits monthly cap, is it benched silently, or does it log a "benched for the month" message?

---

## 10. Example Flow (End-to-End)

```
Stryder: "@brain research how different LLM providers handle prompt injection detection"

Haiku classifies:
  task_type: research
  urgency: normal
  estimated_complexity: standard
  capability_needed: reasoning

Roster check for standard tier:
  1. Sonnet 4.6 — session window 60% used, weekly 40%, within RPM. ✓ CAN USE
     → Dispatch Sonnet

Sonnet researches for ~8 min, returns analysis
  Logs: 5200 input tokens + 3100 output tokens = 8300 total
  Updates ledgers: session_window now 62%, weekly now 41%

Brain replies with Sonnet's analysis + cost breakdown

Later, another task comes in (deep reasoning needed). Opus tier:
  Opus — session window 62%, weekly 41%, within RPM. ✓ CAN USE
  → Dispatch Opus

Opus reasons for ~5 min, returns strategic analysis
  Logs: 4100 input + 6200 output = 10300 total
  Updates ledgers: session_window now 65%, weekly now 42%

... hours pass, Haiku for triage keeps running (separate session windows for each model) ...

Late in the day, another message:
  Sonnet's session window is now 93% (about to reset in 8 min)
  Stryder sends a complex task that would be ~5K tokens
  
Roster check for standard:
  Sonnet — 93% used, estimated to reset in 8 min. ✗ SKIP (close to limit)
  GPT-4o (if added) — within limit. ✓ CAN USE
  → Dispatch GPT-4o

GPT-4o handles the work. 8 min later, Sonnet's window resets.

Council observes:
  "Sonnet: 95% accuracy on reasoning, 88% on research"
  "GPT-4o: 92% accuracy on reasoning, 90% on research"
  Council notes: "Sonnet slightly better on reasoning, GPT-4o slightly better on research"
```

---

## Glossary

| Term | Definition |
|---|---|
| **Provider** | An AI company (Anthropic, OpenAI, Google, Groq, DeepSeek, etc.) |
| **Model** | A specific LLM (Haiku, Sonnet, Opus, GPT-4o, Gemini Flash, etc.) |
| **Tier** | A capability class (nano, standard, deep, fallback) |
| **Roster** | The list of available providers/models, ordered by cost within each tier |
| **Session window** | A time-bounded quota window (e.g., 5 hours for Anthropic) |
| **Ledger** | A record of usage within a window (tokens, requests) |
| **Headroom** | Remaining capacity as a percentage (0.0 = exhausted, 1.0 = fresh) |
| **Failover** | Automatic switch to the next provider when the current one hits a limit |
| **Fail loud** | Always announce failures in-channel instead of silently retrying |
| **Council** | Benchmarking system that tracks provider reputation per use-case |
| **Dossier** | Provider metadata (models, limits, costs, capabilities) |

