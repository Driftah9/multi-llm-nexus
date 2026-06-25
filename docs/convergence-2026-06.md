# Convergence Port — June 2026

This release ports a set of provider-neutral mechanisms that were built and hardened in
the upstream live system, generalized into agnostic platform features. It is a behavioral
convergence, not a feature dump: the goal is that **the same Nexus codebase runs on the
floor (one cloud provider + the smallest local LLM) and grows to a fully-local rig with no
code change** — capability is purely a function of which resources are registered.

**Ground rule held throughout:** only *mechanism and behavioral shape* were ported. **No
provider APIs, keys, or rosters** travel into Nexus — operators wire their own providers via
`config/providers.yaml`. Operator-specific data ships only as `*.example` templates.

---

## The two gates

The platform decides what a feature does in two stages:

1. **Activation / deferral gate** (`core/capability_gate`) — *can anything available do this?*
   A feature declares a `CapabilityRequirement` (min capable executors, structured-output,
   shared-state, RAM, GPU). `evaluate()` against a `SystemCapabilities` snapshot returns
   **active**, or **deferred** with the reason. A feature goes dark only if *nothing
   available* meets its bar; a strong primary provider makes most features active. As a
   deployment grows (more providers, bigger local model, a coordination store), deferred
   features light up automatically — no reconfiguration.

2. **Offload / routing** (`core/provider_chain` + the existing pools) — *among the things
   that can, which is cheapest and healthiest?* This is where the failover intelligence
   below lives. (Routing by *learned* quality scores is collected but not yet wired — a
   follow-on.)

---

## Failover intelligence (`core/error_classifier`, `core/provider_chain`)

A raw provider failure is classified into `transient | quota | auth | bad_request | unknown`,
which drives the failover loop:

- **transient** (529/503/timeout/overload) → retry the *same* provider once, then advance.
- **bad_request** (malformed / filtered / over-length) → **stop the chain** — it fails
  identically on every provider, so surfacing it beats burning N providers on one error.
- **auth / quota** → advance (a different key/budget may work), and the failed provider is
  **benched on a classification-aware cooldown** — ~1h for billing/auth (won't recover in
  30s) vs the short transient window. A dead-key/dead-balance provider stops costing a
  failed round-trip every request and auto-recovers when the window elapses.

Provider health (failures + cooldown) is **optionally persisted** (`ChainConfig.health_path`)
so a benched provider stays benched across a restart instead of being re-probed immediately.

---

## Provider-neutral memory (`core/memory_injector`)

The platform's answer to *"how does an AI read system memory?"* — the **same way regardless
of which model is wired in.** A single agnostic contract — `assemble_context` / `recall` /
`remember`, plus value types and `TOOL_SPECS` for function-calling models — sits between the
stores (`RagStore`, `MemoryLoader`) and the harness. It is wired into `Bridge.invoke` so
memory is injected **once, before any provider is chosen** (recall → prompt, standing →
system prompt). Opt-in and behavior-preserving: with no stores wired it is a clean no-op;
`enable_memory(rag, mem)` turns it on. A different backend swaps in via `set_injector()`.

---

## Structured-output robustness (`core/schema_gate`)

A fail-open structural conformance gate. When a turn requests structured output and a weak
provider returns valid-JSON-but-wrong-shape (missing required field, array-of-objects as
scalars), the gate flags it so the caller can fail over to a provider that gets the shape
right — instead of passing garbage downstream. Not a full JSON-Schema validator; it never
rejects a valid response.

---

## Multi-orchestrator failover (`core/council_lease`, `council_checkpoint`, `council_resumer`)

For deployments with several capable models acting as orchestrators: a single-leader **lease**
(auto-expires if the holder dies) + a monotonic **fencing token** (rejects a stale,
resurrected orchestrator's writes) + a cooperative **knock** handoff, rich fencing-stamped
**checkpoints**, and a decoupled **resumer** that continues an interrupted task under a
recovered provider (built on injected callables — no harness coupling).

This feature **declares itself dark at the floor**: its `CapabilityRequirement` needs ≥2
capable executors *and* a shared coordination store, so a one-provider / no-store deployment
gates it off and never starts it. The store is an optional Redis-compatible backend
(`NEXUS_COORD_REDIS_*`); `redis` is an optional dependency.

---

## Identity resolution (`core/identity`)

`resolve((platform, native_id)) → person_id` with an **owner floor** (the operator always
resolves, even with no registry) + a people registry, and graceful behavior when no identity
config exists (everyone → shared tier). Fully agnostic: no hardcoded platform names — every
handle is a uniform string `ids` list, so any adapter works unchanged. It **composes with**
`core/security`: identity answers *who*, security authorizes the *action*.

---

## Local-first web research (`research/research_worker`)

Page fetch + content extraction moved **on-box** (`httpx` + `trafilatura`), replacing the
remote Jina Reader. Nothing about a fetched page leaves the operator's machine before the
(locally-routed) synthesis step — local-first by construction, which the floor requires.

---

## What this does *not* change

- No providers, keys, or rosters were added — provider config stays the operator's.
- Default runtime behavior is preserved: memory injection, health persistence, and council
  failover are all **opt-in / capability-gated**, so an existing deployment behaves the same
  until it opts in or grows into them.
