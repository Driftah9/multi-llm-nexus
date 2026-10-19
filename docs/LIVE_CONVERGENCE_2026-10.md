# Live → Nexus convergence, October 2026

**Started:** 2026-10-19 · **Branch:** `port/live-convergence-2026-10` · **Baseline:** 275 tests green
**Source:** live claude-brain changes since the last Nexus sync (2026-09-26 — 322 live commits).
**Method:** per stage — port, adapt to Nexus seams (layout manifest, `NEXUS_DATA_DIR`, injected
notify, no hardcoded provider), port or write tests, full suite green, docs in the same commit.

The governing rule is unchanged: live is built and validated first; Nexus receives the validated
design, never the operator's constraints (paths, provider names, Claude-specific wording).

## Stage 1 — Workspace guard (the office model)

Ported live `node_map_guard.py` (fixed + validated 2026-10-19) → `src/core/workspace_guard.py`,
`run_agent(guard_fn=…)`, `scripts/workspace_guard_hook.py`. Full design: `docs/WORKSPACE_GUARD.md`.
Adaptation: live keys ownership off WTI manifests under a fixed projects root; Nexus resolves the
root from `layout.path("workspace")` and owns by location, honouring a project's optional
`.workspace-tools.yml` `protected_paths`. Live's per-turn cwd does not exist in Nexus, so the
linked project is passed explicitly (`home=`), or taken from the hook payload's `cwd`.
Tests: 42.

## Stage 2a — Outage reasons + mechanical recovery

| Live | Nexus |
|---|---|
| `core/outage_reason.py` | `src/core/outage_reason.py` — same table, labels made provider-neutral (no "Claude CLI") |
| `core/provider_health.py` (JSON down-registry) | **not ported as a second store** — `ProviderChain` already persists per-entry health/cooldown. Added `ProviderChainEntry.down_since` (persisted) instead |
| `core/recovery_watcher.py` (separate loop) | folded into the existing `ProviderChain.HealthMonitor` |

`HealthMonitor` previously probed every entry blindly — including auth-benched ones that need a
human — recovered silently, and left `cooldown_until` set so a recovered provider stayed skipped
until the window expired. It now applies live's decision branches: **quota** waits for its window
then probes; **transient/unknown** probe now; **auth / bad_request / model_gone** are never probed or
auto-cleared. Recovery clears the entry like a live success and calls the chain's injected
`on_recovery(text)` with the cause (`outage_reason.summary`) and how long it was down; `main.py`
wires that to the operator's `Notifier` seam (off-loop). Local-floor providers recover silently.
Not ported: live's local-LLM narration of the notice (operator nicety, deterministic text is
sufficient) and Claude-specific probe/quota branches. Tests: 18.

## Stage 2b — Council contradiction scan + worker-health gate on the council

- `src/orchestration/council_contradictions.py` — verbatim port (pure, zero-LLM): verdict /
  polarity / numeric contradictions between Skeptic, Advocate and Verifier, injected into the
  chairman's synthesis prompt as items to resolve explicitly, and persisted in the session record
  (`council_session` schema v2, `contradictions`). Fail-open.
- **Gap closed in passing:** Nexus's council executor never consulted `worker_health` (ported to
  the swarm loop only, 2026-09-25), so benched providers could still be handed council roles. It
  now filters members through `worker_health.filter_candidates` and records each role's
  success/failure — live parity since 2026-09-14.
Tests: 18 ported from live (fixtures adapted).

## Stage 2c — Capability baseline + qualification flap guard

- `src/orchestration/capability_baseline.py` — the seeded-prior MECHANISM. `is_qualified` is now
  `earned OR seeded`; `qualified()` and `choose()` rank ungraded providers by the operator's prior
  (`_effective_score`) without ever writing it into the earned table.
- **Adaptation:** live ships a curated table keyed by its own provider ids (`claude-opus`,
  `groq-70b`, …). Nexus provider ids are operator-defined, so shipping those scores would silently
  mis-seed anyone whose names differ. Nexus ships `config/capability_baseline.json.example` only;
  the real file is operator data (gitignored). No file → earned-only, exactly as before. Live's
  transitional two-file split (baseline + seed) collapses to one file accepting both shapes.
- `CapabilityMap` flap guard (`QUALIFY_CONFIRM`, default 3): the earned bar crossing flips only
  after N consecutive grades on the other side; a collapse below `bar - PROBE_BAND` demotes at
  once. `qualification_state()` exposes the latch; `probe_candidates()` includes over-bar models
  whose promotion is still unconfirmed.
Tests: 12 baseline (adapted from live) + 13 hysteresis (ported verbatim).

Last verified against code: 2026-10-19
