# Nexus Build-Out Status — Where the Vision Meets the Code
**Date:** 2026-06-09 · **Verified/updated:** 2026-07-18
**Purpose:** Single source of truth on what's built, what's stubbed, what's missing — so the provider/tier work has full context without re-explanation.

> **2026-07-18 reality check.** The "INERT / broken in 3 ways / STANDBY since Jun 3"
> framing below is **stale**. As of this date `nexus.service` is **active and serving**
> (`/health` → ok, `/v1/models` → nexus·nano·standard·deep), 9 providers load and
> health-monitor, and most tiers are populated (`enabled: true`). The three original
> blockers are resolved (see the Blockers section). The remaining gap is the
> **swarm/graduation-ladder convergence** (below) and provider-level parallel fan-out
> — which the swarm loop subsumes. Sections not carrying this banner may still read
> from the June snapshot; trust the banner'd notes.

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
| **PARALLEL = same prompt to N providers, synthesize** | 🟡 **Subsumed by swarm (2026-07-18)** | The swarm loop (`swarm_wiring`/`swarm_loop`) is the generalized form — capability-routed parallel worker waves + review. It's ported and inert; wiring it (step 15) closes this. Fixed-role council also fans one prompt to 3 roles in parallel. |
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
tier "deep" task → fan SAME prompt to [Claude Opus, SambaNova DeepSeek-V3.2, local 70B]
  → gather all answers → judge/synthesize → return best
```
The hook is already there: `TierPoolConfig.parallelism: sequential | failover | parallel` in `pool_manager.py`. It's parsed and stored but **never read**. Wiring it is the highest-leverage feature for the Operator's vision.

---

## Blockers: Why Nexus Is Inert (must fix to run at all)

> **2026-07-18: all three ORIGINAL blockers are RESOLVED — Nexus is live and serving.**
> Verified: `nexus.service` active; `/health`→ok; `/v1/models`→nexus·nano·standard·deep;
> 9 providers load and health-monitor. #1 fixed at PW-3. #2 uses the `${MATTERMOST_BOT_TOKEN}`
> env pattern (operator sets team via override). #3 the engine serves via the API/adapter
> path — it is no longer stuck in STANDBY. The only residual is a benign INFO-level
> `Retrying request to /models` backoff on one provider's periodic health check (likely
> `github_models` — slow to list); it self-recovers and does not block traffic. The list
> below is retained as history.

1. **Primary provider 404** — *(resolved, PW-3)* the primary was `cerebras` model `qwen-3-235b-a22b-instruct-2507`, which the provider retired (HTTP 404 on every call). `config/providers.yaml` now points Cerebras at `gpt-oss-120b` and SambaNova at `DeepSeek-V3.2` (both former models confirmed retired via `discover_models.py`). Boot-time schema validation (PW-5) plus the model-lifecycle health check now catch a retired model before traffic hits it.
2. **Mattermost adapter can't attach** — `config/adapters.yaml` `mattermost.token` is an unresolved env placeholder, and `team: main` doesn't exist (live team is `claude-brain`). → Set a real bot token + correct team.
3. **Engine never goes ACTIVE** — stuck in STANDBY since Jun 3. → Likely downstream of #1/#2; re-check after fixing.

Until these are fixed, Nexus listens on `:8080` (OpenAI-compatible API) but answers no one.

---

## Recommended Build-Out Sequence

### Phase 1 — Make Nexus Live (unblock)
- [x] Fix Cerebras model slug (or repoint primary) — kill the 404 *(done, PW-3: `gpt-oss-120b`)*
- [ ] Set real Mattermost bot token + `team: claude-brain` (use a TEST channel, not town-square, to avoid colliding with the live brain)
- [ ] Confirm engine transitions STANDBY → ACTIVE and answers one test message
- [ ] Verify `llm-watcher` sees it healthy

### Phase 2 — Populate the Tiers (the provider work from #questions)
- [ ] Enable staged providers in `config/providers.yaml` (currently `enabled: false`):
  - **nano:** Groq (triage), + GitHub Models low-tier as backup
  - **standard:** Cerebras, Google Gemini (250K TPM), Mistral, GitHub Models (GPT-4o)
  - **deep:** SambaNova DeepSeek-V3.2, OpenRouter (frontier), Claude (reserved)
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

## Swarm + Graduation Ladder convergence (live→Nexus, started 2026-07-18)

The live brain shipped a **swarm orchestration loop** (plan → capability-route → parallel DAG
waves → review → loop) and a **capability graduation ladder** (unknown models start low, are graded
every call, climb via evidence, earn demanding roles benchmarked to a trusted baseline). Per the
live→Nexus convergence rule, these port DOWN into Nexus. A gap audit (2026-07-18) found Nexus is
several steps behind on the underlying arc, so this is a **sequenced multi-step convergence, not a
copy**. Ordered plan (each step is its own PR, verify-before-next):

> **Progress 2026-07-18:** ALL 15 steps DONE and green (193 tests), and DEPLOYED to the running
> `nexus.service` — inert behind `SWARM_LOOP_ENABLED=0`. Foundation, keystone shim, full council
> refactor (tournament→fixed-role), swarm mechanics + wiring, and the `bridge.invoke` integration
> gate are all in. Remaining is validation (exercise the ON-path against real providers on a test
> channel) + the operator's decision to flip the flag — not porting.

1. ✅ **capability_map ladder foundation** — DONE 2026-07-18 (grades counter + qualification/probe
   methods + constants). Pure additions, tests green.
2. ✅ `orchestration/provider_status.py` — DONE 2026-07-18. Ported; `KNOWN_PROVIDERS` = all 11
   `providers.yaml` ids, `FRONTIER_COUNCIL` = {sambanova, openrouter, cerebras} (no claude in the
   OpenAI-compatible registry), both env-overridable. 15 tests.
3. ✅ `orchestration/providers.py` `system=` kwarg — DONE 2026-07-18 (threaded through `fan_out`;
   `complete` already had it).
4. ✅ `orchestration/staging.py` — DONE 2026-07-18. `$NEXUS_DATA_DIR/staging`. 7 tests.
5. ✅ **Worker-pool shim** (`orchestration/worker_pool.py`) — DONE 2026-07-18. `tier_roster()`,
   `worker_candidates()`, `is_nano_tier()`, `communicator_providers()` derived from `providers.yaml`
   `tier:`/`priority:`; deep tier reserved as the voice (was: claude reservation), env-overridable.
   8 tests. This was the provider-config-model adaptation — the biggest non-council rewrite.
6. ✅ `orchestration/scribe.py` — DONE 2026-07-18. Agnostic default backend = Nexus's own
   `nexus-nano` endpoint (replaces live's `claude-cli` backstop); ollama lane opt-in. 6 tests.
7. ✅ `orchestration/council_roles.py` — DONE 2026-07-18. Fixed-role prompts + read-only grounding
   broker; `_is_nano_tier`→`worker_pool.is_nano_tier`; research roots default to the Nexus repo tree
   (`COUNCIL_RESEARCH_ROOTS` override); web lookup via `research.research_worker.research`. 28 tests.
8. ✅ `orchestration/council_session.py` — DONE 2026-07-18. `$NEXUS_DATA_DIR/council_sessions/`,
   atomic writes, 200-file retention. 5 tests. Now wired into `council_executor.run()` (step 10).
9. ✅ `orchestration/council_judge.py` — DONE 2026-07-18. Deferred + multi-candidate failover; judge
   roster remapped to Nexus ids (`COUNCIL_JUDGES_HIGH/STANDARD` env override), fable lane inert. 17 tests.
10. ✅ **Replaced** `orchestration/council_executor.py` — DONE 2026-07-18. Tournament (anonymize+Borda)
    → fixed-role (Skeptic/Advocate/Verifier). `CouncilResult` is now a SUPERSET of the old shape
    (kept `rankings`/`label_map`/`peer_review_skipped` for back-compat), so no caller broke — and none
    were wired anyway (council path was scaffolding). Grounding on every role, session record, deferred
    judge. 4 tests.
11. ✅ `orchestration/delegator.py` — DONE 2026-07-18. Uses `worker_pool.worker_candidates`; stages
    via `staging.py`. 5 tests.
12. ✅ `orchestration/swarm_loop.py` — DONE 2026-07-18. Byte-identical to live (pure DI). 15 tests.
13. ✅ `orchestration/swarm_grading.py` — DONE 2026-07-18. Judge roster→Nexus ids; provider_status
    lifecycle wired. 9 tests.
14. ✅ `orchestration/swarm_wiring.py` — DONE 2026-07-18. Planner floor = scribe "nexus" nano lane
    (`SWARM_PLANNER_BACKENDS` override); worker pool + probe admission; heartbeat callback guarded
    with getattr for the step-15 seam. 13 tests. **Inert until `SWARM_LOOP_ENABLED=1`.**
15. ✅ **Orchestrator delegation/swarm gate** — DONE 2026-07-18 (wired INERT). Resolved the seam
    question: Nexus's `core/orchestrator.py` is a *workspace→specialist* dispatcher (a different axis
    than swarm delegation), so the gate went into the STANDARD path — `core/bridge.py` `invoke()`,
    right after memory injection, before routing. Logic: *if `swarm_loop.enabled()` AND triage says
    the task is `estimated_complexity=="deep"` and `task_value` in (important, critical), call
    `run_swarm_delegation()`; synthesize the worker findings via one ephemeral deep-tier call
    (`_synthesize_swarm_findings`); on `[]`/any issue, fall through to the normal single-shot path.*
    The `swarm_loop.enabled()` check is FIRST, so at `SWARM_LOOP_ENABLED=0` (default) the block is
    skipped and behavior is byte-identical — proven by `tests/test_bridge_swarm_gate.py`
    (`test_gate_inert_when_flag_off`). **ON-path (flag=1) is first-cut — validate end-to-end against
    real providers before enabling.** 8 gate tests.

**Convergence status: 15/15 ported, green (194 tests), DEPLOYED to the running `nexus.service`
(inert behind `SWARM_LOOP_ENABLED=0`), and the ON-path is END-TO-END VALIDATED against real providers
(2026-07-18).** A bounded out-of-process run (flag on for that process only) exercised the full loop —
planner decomposed a task into 4 sub-steps, workers answered them in parallel, and a deep-tier provider
synthesized the findings into one answer. The safety fallback was also confirmed: when the planner
failed, the swarm returned `[]` and the caller fell back to a single-shot call.

**Bug found + fixed by that validation:** the scribe planner lane posts to the local Nexus `:8080` API,
which requires a bearer token (`adapters.yaml` `openai_api.api_key`, default `"nexus"`). scribe wasn't
sending `Authorization`, so the planner 401'd and the ON-path was silently dead (safe — it just fell
back). Fixed: `scribe._post_openai` now sends the token on the `nexus` lane (`NEXUS_API_KEY` env,
default `"nexus"`); regression-tested. Only remaining item: the operator's decision to flip the flag
(and, when scaled up, watch worker diversity as the capability ladder accumulates grades — the cold-start
run routed most sub-steps to one nano worker until graded history exists).

Live source of truth for the design: `~/.claude/.../memory/project_swarm_orchestration_loop.md` +
`project_capability_graduation_ladder.md`.

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
