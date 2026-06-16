# Nexus Build-Out Status — Where the Vision Meets the Code
**Date:** 2026-06-09
**Purpose:** Single source of truth on what's built, what's stubbed, what's missing — so the provider/tier work has full context without re-explanation.

---

## The Vision (Operator's Model)

> Primary AI / Orchestrator → triage classifies **nano | standard | deep** → route to a tier → execute either in **PARALLEL** (multiple providers at once) or as **ROLLOVER** (fail to next provider). Harness ALL providers — free, paid, subscription, API-only, OpenRouter, local. Run with 1 or dozens. Free tiers are usable even if slow/rate-limited, as long as we monitor them within their restrictions. Goal: offload Claude token burn (Operator's personal usage).

---

## The Reality: Two Parallel Stacks on 10.0.0.7

```
┌─────────────────────────────────────────────────────────────────────┐
│  LIVE PRODUCTION BRAIN          /home/claude/adapters/                │
│  Services: adapter-mattermost / -discord / -telegram                  │
│                                                                       │
│  Mattermost WS → owner gate → channel→project map →                   │
│    triage (Claude CLI Haiku) → ClaudeBridge subprocess                │
│    (npx claude -p … --model … --effort …) → reply                     │
│                                                                       │
│  ► CLAUDE ONLY. Max subscription CLI. No API providers in live path.  │
│  ► orchestration/ (council_router) = scaffolding, NOT wired.          │
│  ► This is what answers Stryder today.                                │
└─────────────────────────────────────────────────────────────────────┘
                              ▲  not integrated  ▲
┌─────────────────────────────────────────────────────────────────────┐
│  MULTI-LLM NEXUS               /home/claude/projects/multi-llm-nexus/ │
│  Service: nexus.service  (RUNNING but INERT)                          │
│                                                                       │
│  AdapterBase → Triage.classify → bridge.invoke(triage=…) →            │
│    PoolRouter → PoolManager (cost-class order) → failover loop        │
│    [optional] Orchestrator → specialist fan-out → synthesize          │
│                                                                       │
│  ► The multi-provider future. 12 provider classes implemented.        │
│  ► Currently broken in 3 ways (see "Blockers"). Offloads nothing yet. │
└─────────────────────────────────────────────────────────────────────┘

  llm-watcher.service → /home/claude/projects/llm-watcher/
    Liveness only (30s state machine, town-square announcements). Not quota.
```

**Key point:** Provider/tier work belongs to **Nexus**. The live brain is Claude-only by design ("no Nexus dependencies"). Neither offloads Claude token burn today.

---

## Vision → Code Mapping

| Vision element | Status | Where in code |
|---|---|---|
| Triage classifies nano/standard/deep | ✅ **Built** | `src/core/triage.py` — `estimated_complexity`, LLM + keyword fallback, 5 dimensions |
| Route to a tier | ✅ **Built** | `src/core/pool_router.py` — `_pool_for_triage` (nano→nano, standard→default, deep→deep_pool) |
| ROLLOVER (fail to next provider) | ✅ **Built ×2** | `src/core/bridge.py` `_invoke_with_pool` (cost-class: local→free→paid) + `src/core/provider_chain.py` `try_with_fallback` (tier-filtered, circuit breaker) |
| Monitor providers within rate limits | ✅ **Built** | `src/core/provider_quota.py` (`can_use`/`headroom`/`should_conserve`, RPM/RPD/TPM/TPD) + `pool_manager.py` sliding-window rate states |
| Run with 1 or dozens of providers | ✅ **Built** | `chain_builder.py` builds from `providers.yaml`; any provider with a `priority:` joins the chain |
| Harness all access models (free/paid/sub/local) | ✅ **Built** | `provider_quota.py` `AccessTier` (FREE/TRIAL/PAID/UNLIMITED); 12 provider classes in `src/providers/` |
| **PARALLEL = same prompt to N providers, synthesize** | ❌ **MISSING** | Only *specialist-role* fan-out exists (`orchestrator.py` `_invoke_specialists` + `_synthesize`). No same-prompt-to-many-providers path. |
| Tier pool `parallelism: parallel` | ⚠️ **Stubbed** | `pool_manager.py:211,306` — field parsed, never branched on. Every pool treated as failover. |
| Engine ACTIVE/STANDBY tick loop | ⚠️ **Dormant** | `engine.py` `_process_standard` — legacy single-provider path; live adapters bypass it via `bridge.invoke` directly |

**Bottom line: ~85% of the vision is already coded.** Rollover, tier routing, and quota monitoring are production-quality. The gap is **provider-level parallel fan-out** and the fact that **Nexus isn't actually running**.

---

## The Critical Gap: Provider-Level Parallel

The vision says "execute in parallel." The code has parallel — but only across **specialist roles** (different system prompts), not across **providers answering the same prompt**.

**What exists:**
```
orchestrator.dispatch() → route to specialists (developer, researcher, …)
  → _invoke_specialists()  [asyncio.gather — TRUE parallel]
      each specialist → bridge.invoke(tier=…)  → ONE provider (failover underneath)
  → _synthesize()  [Chief-of-Staff merges, surfaces conflicts]
```
Each specialist resolves to a **single** provider. Fan-out is by *role*, not by *model*.

**What's missing (the build target):**
```
tier "deep" task → fan SAME prompt to [Claude Opus, SambaNova 405B, local 70B]
  → gather all answers → judge/synthesize → return best
```
The hook is already there: `TierPoolConfig.parallelism: sequential | failover | parallel` in `pool_manager.py`. It's parsed and stored but **never read**. Wiring it is the highest-leverage feature for the Operator's vision.

---

## Blockers: Why Nexus Is Inert (must fix to run at all)

1. **Primary provider 404** — `config/providers.yaml` primary is `cerebras` model `qwen-3-235b-a22b-instruct-2507`, returns HTTP 404 "model does not exist or you do not have access" on every call. → Verify the exact Cerebras model slug, or switch primary to a known-good model.
2. **Mattermost adapter can't attach** — `config/adapters.yaml` `mattermost.token` is an unresolved env placeholder, and `team: main` doesn't exist (live team is `claude-brain`). → Set a real bot token + correct team.
3. **Engine never goes ACTIVE** — stuck in STANDBY since Jun 3. → Likely downstream of #1/#2; re-check after fixing.

Until these are fixed, Nexus listens on `:8080` (OpenAI-compatible API) but answers no one.

---

## Recommended Build-Out Sequence

### Phase 1 — Make Nexus Live (unblock)
- [ ] Fix Cerebras model slug (or repoint primary) — kill the 404
- [ ] Set real Mattermost bot token + `team: claude-brain` (use a TEST channel, not town-square, to avoid colliding with the live brain)
- [ ] Confirm engine transitions STANDBY → ACTIVE and answers one test message
- [ ] Verify `llm-watcher` sees it healthy

### Phase 2 — Populate the Tiers (the provider work from #questions)
- [ ] Enable staged providers in `config/providers.yaml` (currently `enabled: false`):
  - **nano:** Groq (triage), + GitHub Models low-tier as backup
  - **standard:** Cerebras, Google Gemini (250K TPM), Mistral, GitHub Models (GPT-4o)
  - **deep:** SambaNova 405B, OpenRouter (frontier), Claude (reserved)
- [ ] Each new key → flip `enabled: true`, assign `tier:` + `priority:`, set `access_tier` + RPM/RPD/TPM/TPD so quota manager governs it
- [ ] Confirm rollover: saturate a free tier, watch it fail over to the next in cost-class order
- Detail: `docs/provider-integration-roadmap.md`, `docs/api-key-setup-guide.md`

### Phase 3 — Build Provider-Level Parallel (close the gap)
- [ ] Un-stub `TierPoolConfig.parallelism: parallel` in `pool_manager.py`
- [ ] Add a parallel execution path in `bridge.py`: when a tier pool is `parallel`, fan the same prompt to N providers via `asyncio.gather`, then route results through a judge/synthesis step (reuse `orchestrator._synthesize` patterns)
- [ ] Gate parallel fan-out on `task_value` (critical/important) + `headroom()` so it only spends multiple providers when the task justifies it
- [ ] Heartbeat: show "3 providers racing" like the existing specialist display

### Phase 4 — Decide Integration
- [ ] Option A: Nexus takes over the adapters (replaces Claude-only brain) — full offload, higher risk
- [ ] Option B: Nexus runs parallel on non-critical channels only — Claude brain stays primary, Nexus proves out
- [ ] Option C: Wire the live brain's `orchestration/council_router.py` to call Nexus as a backend for nano/standard tasks — incremental offload
- Recommend **B** first (prove reliability), then **C** (incremental offload), reserve **A** for when parity is proven

---

## Quota Reality (the "monitor within restrictions" piece)

`provider_quota.py` already does what the Operator described — work *within* limits instead of slamming into them:
- `can_use(provider)` → False when RPM/RPD/TPM/TPD window is full
- `headroom(provider)` → 0.0–1.0 remaining capacity (tightest constraint wins)
- `should_conserve(provider)` → True below 20% — orchestrator skips optional calls
- `AccessTier` drives it: FREE/TRIAL get hard limits; PAID/UNLIMITED always pass

There are currently **two quota/health layers** that should eventually unify:
- `provider_quota.py` (token/request budgets, in Nexus)
- `pool_manager.py` `ProviderRateState` (sliding-window rate, in Nexus)
- `llm-watcher` (liveness only, separate project)

Phase 2+ should converge on one so a provider's health + headroom is read from a single source.

---

## File Reference (for whoever builds this)

| Concern | File |
|---|---|
| Tier classification | `src/core/triage.py` |
| Tier → pool routing | `src/core/pool_router.py` (`_pool_for_triage`) |
| Provider selection hub | `src/core/bridge.py` (`invoke`, `_invoke_with_pool`, `_invoke_with_chain`) |
| Rollover (chain) | `src/core/provider_chain.py` (`try_with_fallback`, `select_provider`) |
| Rate limit gate | `src/core/pool_manager.py` (`is_available`, `ordered_pool`); `src/core/provider_quota.py` |
| Parallel specialists + synthesis | `src/core/orchestrator.py` (`_invoke_specialists`, `_synthesize`) |
| Parallel provider stub | `src/core/pool_manager.py` (`TierPoolConfig.parallelism`) |
| Chain construction | `src/core/chain_builder.py` |
| Provider registry (22 defs) | `src/providers/registry.py` |
| Startup wiring | `src/main.py` |
| Provider configs (staged) | `config/providers.yaml` |
