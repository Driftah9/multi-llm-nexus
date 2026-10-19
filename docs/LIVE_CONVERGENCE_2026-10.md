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

## Stage 2d — Routing weights (telemetry → routing)

- **Gap found:** Nexus had `routing_telemetry.record()` (Phase 1) but nothing called it. The
  chain now records one row per attempt in `try_with_fallback` (success, latency, reasoning-leak
  proxy, tier as task class), including the same-provider retry.
- `src/core/routing_weights.py` — port of live Phase 2: per-provider weight = reliability
  (Beta-prior over telemetry + council seats/failures) × quality × latency factor over a 21-day
  window. **Demote-only and stable:** a provider is demoted only with ≥ 8 recent samples AND
  weight < 0.35 or median success latency > 12 s; it then sorts after its peers in the same
  health class — never dropped. No evidence → no change. Kill switch `ROUTING_WEIGHTS_ENABLED=0`.
- Wired into `ProviderChain.select_provider` (sort key: health, demoted, priority; computed
  before the chain lock) and `council_executor.run` (`order(members)` after the worker-health gate).
- **Adaptations:** live pins Claude entries as fixed anchors — Nexus core has no special
  provider, so `is_anchor` is caller-supplied and unused by default. Live filters evidence by its
  provider catalog; Nexus does not (chain names and orchestration ids are separate namespaces).
  Ledger paths follow the writer modules (`TELEMETRY_PATH`/`SESSIONS_DIR` = None → read the
  telemetry/session modules' own paths), so one env var moves writer and reader together.
- `tests/conftest.py` gains an autouse fixture isolating the routing ledgers — live leaked test
  `fake` rows into its real ledger (2026-10-07); with the chain now recording, every chain test
  would otherwise write to `data/`.
Tests: 13 (8 ported, 5 Nexus seams incl. chain-evidence → demotion end-to-end).

## Stage 2f — Turn-failure surfacing (+ a reply-path bug found on the way)

- `src/core/turn_failure.py` — port of live's turn-crash surfacing. `AdapterBase.handle_incoming`
  now covers three stages after the placeholder is posted: **pre-dispatch** (triage, routing,
  context), **dispatch** (bridge/orchestrator), **output** (review gate, formatting, send). A crash
  in any of them edits the placeholder into a visible error (pre-dispatch says no usage was spent)
  and records it in `$NEXUS_DATA_DIR/error-inbox/open/turn-crash__<platform>.json` (de-duplicated
  per platform, `occurrences` counter, traceback tail) — the inbox the Stage 3 nightly sweeper
  reports from. Previously a pre-dispatch crash left "…" up
  forever and a dispatch crash froze the last "thinking…" heartbeat text.
- **Adaptation:** live tells the owner to pick the turn up from the CLI on its VM; the Nexus
  user message is install-neutral. The record format is live's error-inbox entry, so Stage 3's
  sweeper DMs turn crashes alongside failed jobs.
- **Bug fixed:** `AdapterBase._check_review_gate` compared against `ReviewTrigger.NONE`, which
  has never existed (members: SKIP/SUGGEST/AUTO; reference since v0.6.0). Every successful turn
  through the Mattermost/Discord/Telegram adapters raised `AttributeError` after the answer was
  generated and before it was posted. Now `ReviewTrigger.SKIP`. The regression test was verified
  to fail with the old line restored.
Tests: 7 (real `NexusBehavior` + heuristic triage; only platform I/O and the bridge are faked).

## Stage 3 — Linux ops layer

**3a. Error inbox — did everything scheduled actually run?**
- `scripts/run_tracked.py` — wraps a cron job transparently (output passes through line by
  line, exit code preserved), records each run to `$NEXUS_DATA_DIR/error-inbox/state/`, flags
  crash signatures even on exit 0, extends cron's PATH with the user bin dirs.
- `scripts/nightly_sweeper.py` — reads the REAL schedule (crontab + custom systemd timers +
  enabled services under /etc/systemd/system) and classifies each job: FAILED / DID_NOT_FIRE /
  HUNG / SILENT / STALE / OK; hung turns from the task journal; per-job expectations
  (`config/error-inbox-expectations.json`, example shipped). One de-duplicated inbox entry per
  job (`error-inbox/open/`), RECOVERED not deleted, DM via the `Notifier` seam. Accepts both
  `run_tracked.py` and the live `run-tracked` spelling in crontab lines.
- `core/turn_failure` (Stage 2f) writes into the same inbox, so turn crashes reach the nightly DM.
- **Validated against the real host:** run read-only (`--dry-run`) on the reference VM's live
  crontab/systemd/error-inbox state, the port's classification output was **byte-identical** to
  live's own sweeper (80 OK / 7 not-yet-due / 3 failed, line for line).

**3b. Notifier failover + circuit breaker.** `src/core/comms_breaker.py` (persisted, flock-guarded;
3 consecutive failures → open, 5 min cooldown → half-open trial) and failover inside
`Notifier.send`: primary, then `notify.fallback_order` (default: the other configured protocols
in config order), only configured protocols. Rerouted messages carry a banner; the first message
through the recovered primary reports the outage length and how many messages went via fallback.

**3c. Secrets lint.** `scripts/secrets_lint.py` — design rule #4 ("public-safe") made checkable:
scans every git-TRACKED file (what a push publishes) for key-shaped literals, prints
`file:line:kind` only. Live scans its home-directory layout; Nexus scans the repo.
`config/secrets-lint-allow.txt` records two pre-existing synthetic fixtures (with reasons).
`tests/test_secrets_lint.py::test_repo_is_clean` keeps the repo clean going forward.
Tests: 14 error inbox (incl. real-subprocess run_tracked) + 10 notify failover + 5 secrets lint.

## Validation against the real system (2026-10-19)

- Full suite: 432 green (baseline 275).
- **Boot:** `python3 -m src.main` with the deployment's real `providers.yaml` + `.env`, no
  platform adapters, scratch data dir — engine up, chain health monitoring started, clean SIGTERM
  shutdown, no tracebacks.
- **Live provider round-trip:** one prompt through the real chain recorded real attempts to
  telemetry (latencies, success flags) and `routing_weights` derived weights from them (thin
  evidence → no demotion, as designed).
- **Bug found by that run (fixed in live first, then here):** an invalid key that a provider
  returns as HTTP 400 (Gemini: "Please pass a valid API key") classified as BAD_REQUEST, which
  stops the chain — one misconfigured key ended the turn before healthy providers were tried.
  `error_classifier` AUTH now matches the invalid-key phrasings ahead of BAD_REQUEST's "400".
- **Sweeper:** byte-identical to live on real host state (see Stage 3a).
- **Deployment config findings (operator action, not code):** on the reference deployment the
  `cerebras` key is rejected (401) and the `google_gemini` key is invalid; two configured models
  (`meta/llama-3.1-70b-instruct`, `llama-3.1-8b-instant`) are no longer listed by their providers.
- **Deploy (`nexus.service` restart):** the first prompt after boot failed — cerebras 402,
  groq model gone, then "No available providers" with seven entries untried. Pre-existing
  selection bug, fixed (see CHANGELOG → Fixed); 436 green, redeployed, re-verified.

## Not ported (deliberately), with reasons

| Live module | Why not now |
|---|---|
| `core/connection_roster.py` (+ `scripts/connection_roster_check.py`) | Built on live's multi-key credential model (per-provider credential sidecars, N keys per vendor, rotation). Nexus has no equivalent; porting it means porting that subsystem first. Its time-based down/recovery half is covered by the Stage 2a HealthMonitor. |
| `core/prompt_assembly.py` + `core/conversation_ledger.py` | Live runs it on by default, but its brain-queue item is **failed-review** — not validated. Live-first rule: prove it, then port. |
| `core/council_resumer.py` outage parking (`OUTAGE_STEP`) | Parks a mid-turn drop of live's LOCKED primary and resumes it on the same CLI session. Nexus has no primary lock — the chain fails over on health and the fallback answers, so there is no parked turn to resume. |
| `core/session_window.py` hold/seed, `memory_injector.recall_for_turn` | Support code for `prompt_assembly` (above); ports with it. |
| `memory_injector` default RAG namespaces (`memory` dropped) | Follows live's engram/RAG scope split; Nexus has no engram, so `memory` stays a RAG namespace. |
| `scripts/bulletins.py` | Its only reader is the reference install's ops-board UI. Nexus has no renderer. |
| `scripts/consolidate-secrets.py` | Migrates secrets into live's `.local/etc/<service>/` layout; Nexus keeps secrets in `.env`. |
| `core/claude_roster_watch.py`, `scripts/claude-window-cycler.py`, `scripts/claude-auth-watch.py`, `scripts/cloud_credit_ledger.py`, `scripts/nvidia_nim_roster_check.py` | Vendor-specific to the reference install's subscriptions/keys. |

Last verified against code: 2026-10-19
