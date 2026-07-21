# Changelog

All notable changes to Multi-LLM-Nexus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Canonical install directory layout, manifest-driven (2026-07-21).** New
  `config/directory_layout.json` is the single source of truth for the install layout;
  `install.sh` now reads its `class:"scaffold"` folders (stdlib JSON parse, works before the
  app venv exists, with a built-in fallback so an automated install never bricks) instead of a
  hardcoded `ROOT_FOLDERS` array. Adding a specialized directory is now a one-line manifest
  edit — the installer and docs both follow. Includes a `venv/` home for system-tool venvs
  (source stays in `Tools/`, project venvs stay in-project, manager venvs — incl. Nexus's own
  `~/nexus/.venv` — stay put), and documents the engine's self-created runtime homes
  (`~/.local/nexus/`, `~/.local/etc/`). The engine now resolves locations through
  `src/core/layout.py` (`layout.path("venv")`) instead of hardcoding `Path.home() / "..."` —
  undeclared folder names fail loud (7 tests in `tests/test_layout.py`; `wizard.py`'s docker
  path is the first migrated call site). `docs/DIRECTORY_LAYOUT.md` is the human-readable face;
  registered in the anti-drift claim-map; convergence gaps (lowercase-name alignment,
  `Memory/`/`context/` roots) flagged in `KNOWN_LIMITATIONS.md`.
- **Agent loop — the tool layer is now real (2026-07-20, live→Nexus port).** `core/agent_loop.py::
  run_agent()` gives any `supports_tools()` provider an execution engine: `BaseProvider.send()` gained
  `tools=` (OpenAI-format schemas from `ToolRegistry.schemas()`), the `openai` provider type passes
  them through and serializes agent transcripts (assistant `tool_calls` + `role:"tool"` results —
  the type covering Groq/Mistral/Cerebras/Gemini-openai, live-validated on the source system);
  the loop confirms → executes → feeds back until plain text, with max-iters / wall-clock /
  token-budget cutoffs each ending in a tool-less summarize call. `ToolDef.requires_confirmation`
  is now ENFORCED fail-closed via `confirm_fn`. Closes the gap where schemas had no channel into
  providers, nothing executed a returned tool_call, and `requires_confirmation` was decorative.
  9 new tests (`tests/test_agent_loop.py`); other providers accept-and-ignore `tools` until
  implemented.
- **Swarm + graduation-ladder + fixed-role council convergence — 15/15 (2026-07-18).** The live→Nexus
  port of the swarm orchestration loop, the capability graduation ladder, and the fixed-role council.
  All 15 steps ported and tested (194 green), **deployed to `nexus.service` inert behind
  `SWARM_LOOP_ENABLED=0`** (default). Step 15 (the `core/bridge.py` delegation/swarm gate) is wired
  inert; the ON-path was validated once end-to-end against real providers (planner → parallel workers →
  deep-tier synthesis, with the safe `[]`→single-shot fallback confirmed). Enabling the flag remains an
  operator decision; it is **not yet battle-tested**. See `docs/BUILDOUT_STATUS.md`. New/changed
  modules, all under `src/orchestration/` unless noted:
  - `provider_status.py` — trust-ladder lifecycle (unknown→known→benched + shadow promotion);
    `KNOWN_PROVIDERS`/`FRONTIER_COUNCIL` mapped to `providers.yaml` ids, env-overridable.
  - `worker_pool.py` — the provider-config-model shim: `tier_roster()`/`worker_candidates()`/
    `is_nano_tier()`/`communicator_providers()` derived from `providers.yaml` `tier:`/`priority:`;
    deep tier reserved as the synthesis voice (the Nexus form of live's Claude reservation).
  - `scribe.py` — house-model lane for internal chores; agnostic default backend is Nexus's own
    `nexus-nano` endpoint (replaces live's `claude-cli` backstop), ollama lane opt-in.
  - `staging.py` / `council_session.py` — worker-finding staging + per-council-run JSON audit record.
  - `council_roles.py` / `council_judge.py` — fixed-role prompts + read-only grounding brokers
    (`[CHECK:]` local grep, `[WEB:]` cited lookup) + the deferred multi-candidate judge.
  - `council_executor.py` — **replaced** the retired tournament model (anonymize + Borda peer-rank,
    ~30 calls) with the fixed-role Skeptic/Advocate/Verifier model (~4 calls). `CouncilResult` is a
    superset of the old shape, so no caller broke.
  - `delegator.py` / `swarm_loop.py` / `swarm_grading.py` / `swarm_wiring.py` — task→worker fan-out,
    dependency-wave execution, sampled worker grading (climb/sink), and the Nexus wiring/entry point.
  - `orchestration/providers.py` — `fan_out()` now forwards `system=` (council/delegator rely on it).
  - Boot: `capability_map` ladder foundation (below) plus these are all additive; 185 tests green.
- **Capability graduation ladder — foundation** (`orchestration/capability_map.py`) — first
  slice of the live→Nexus convergence for the swarm/graduation work (2026-07-18). Added the
  per-(domain, provider) observation counter (`data["grades"]`, incremented in `update()`,
  persisted via `load`/`save`) plus the qualification layer: `grade_count()`, `is_qualified()`,
  `qualified()`, `probe_candidates()` and the constants `QUALIFY_BAR` (0.6, `CAP_QUALIFY_BAR`),
  `QUALIFY_MIN_SAMPLES` (5), `DEMANDING_DOMAINS`, `PROBE_BAND`, `PROBE_RATE`. A model qualifies
  for a demanding role (planner/reviewer/builder) in a domain only when PROVEN — enough graded
  samples AND score ≥ the bar; unproven (no samples) never qualifies (cold-start-low). Pure
  additions, no new deps; 53/53 tests green. The rest of the ladder (swarm loop, worker grading,
  council-roles/judge/session, scribe, provider_status) is a sequenced multi-step convergence —
  see `docs/BUILDOUT_STATUS.md` → "Swarm + Graduation Ladder convergence".
- **Config schema validation at boot** (`core/config_schema`, PW-5) — `providers.yaml`
  and `adapters.yaml` are validated against Pydantic models in `main.run()` before any
  provider or adapter is built. Catches missing/typo'd fields, wrong types (e.g. `rpm`
  as a string), invalid tiers (must be `nano`/`standard`/`deep`/`apex`), bad `access_tier`
  values, and malformed routing patterns (must have exactly one of `provider`/`providers`).
  On failure the process exits with a clear error instead of starting half-configured.
- **Installer resilience** (PW-6):
  - `scripts/preflight_check.sh` — read-only pre-install validator (Linux, root, ≥2 GB
    disk, memory, DNS/GitHub reachability, Python 3.11+, `git`/`curl`/`whiptail`, ports,
    sudoers). Exit codes: `0` pass, `1` fail, `2` warnings.
  - `scripts/install_state.py` — install checkpoint tracker (`~/.nexus-install-state.json`)
    with `mark-phase` / `is-completed` / `show` / `reset`, enabling resume-after-failure
    for idempotent install phases.
  - `docs/INSTALLER_RESILIENCE.md` — two-layer (preflight + checkpoint) design. Installer
    testing is **local-only** (interactive TTY/whiptail/sudo can't run headless); this repo
    has no CI.
- **Triage validator extensions** (`core/triage_validator`, PW-8) — decision metadata
  columns (`domain`, `stakes`, `complexity`, `platform`) and turn facts (`provider`,
  `model`, `failover_hops`, `council`, `error`); re-ask detection (`note_incoming` flags the
  previous decision dissatisfied on a negation opener or >60% token overlap within 5 min);
  `apex` added to the tier rank; and legacy-DB migration that archives pre-2026-06
  model-name-schema databases instead of failing.
- **`core/diag_report`** — a self-diagnostic report generator. `nexus doctor` (or any
  surface) renders a single Markdown document of the deployment's own state: versions, a
  hardware/capability snapshot, **which features are active vs deferred** (via the real
  `capability_gate` + each feature's `CapabilityRequirement`), configured providers **by
  type/tier/role** (keys reported only as present/absent — never the value), provider
  health, and local-service reachability. For attaching to a GitHub issue, email, etc.
  - **No phone-home.** The module only produces a string and a file; it never transmits.
    The operator is the sole transmitter (download / copy / paste).
  - **Fail-closed redaction** (`diag_report.scrub`): secret-shaped strings are always
    masked; home paths and LAN IPs are redacted by default. Other users' identities are
    never included (people are reported as a count only).
  - Everything is read from the operator's own `config/providers.yaml` + a live hardware
    scan — no baked-in roster, no operator data in the module.
- **`nexus doctor`** CLI subcommand (`--stdout`, `--output PATH`, `--no-redact-paths`).
- **`tools/ops_board`** — a local admin console (`nexus ops-board`, default `127.0.0.1:8137`)
  built on Nexus's own FastAPI stack with HTML/CSS/JS inlined (no template engine or static
  assets). Agnostic, config-driven tabs:
  - **Providers** — from `config/providers.yaml`, by type/tier/role; keys present/absent only.
  - **Diag** — the diagnostic report (rendered Markdown + Raw toggle, Download / Copy /
    Regenerate, redact toggle).
  Both tabs reuse `core.diag_report`'s collectors, so the console and `nexus doctor` can
  never disagree. Local only; it never transmits.
- Test suite `tests/test_diag_report.py` (structure, fail-closed redaction, no-config safety).

- **Model-lifecycle hardening** (agnostic port from the live upstream system):
  - `core/error_classifier` gains a `MODEL_GONE` class — provider errors like
    `model_not_found` / "has been decommissioned" / Ollama "try pulling it first"
    are the MODEL dying, not the provider or the request. Tested before
    `bad_request` so a retired model advances failover instead of stopping it.
  - `core/provider_chain.record_failure`: `MODEL_GONE` skips the failure
    threshold — immediate FAILED with a 30-day cooldown persisted across
    restarts. A dead model is never hammered again; the log names the fix
    (update `config/providers.yaml`).
  - OpenAI-compatible `health_check()` now verifies the **configured model is
    present in the provider’s live listing** (normalized for Gemini
    `models/` prefixes and azureml registry URIs; fail-open on odd listings) —
    retirements are caught by the 60s health monitor before any real traffic
    hits them, not after.

### Changed
- **Retired provider models swapped in `config/providers.yaml`** (PW-3, confirmed via
  `discover_models.py` 2026-07-06): Cerebras `qwen-3-235b-a22b-instruct-2507` →
  `gpt-oss-120b`; SambaNova `llama-3.1-405b` → `DeepSeek-V3.2`. Both former models were
  retired at their providers. `docs/AI_PROVIDER_REFERENCE.md` updated to match.

### Removed
- **`config/sessions.json` untracked** (PW-4) — it is runtime session state and was
  committed by mistake. Removed via `git rm --cached` and added to `.gitignore`.

### Fixed
- **Provider loader honors `enabled: false`** (`main._build_providers`). Previously every
  provider in `providers.yaml` was instantiated regardless of its `enabled` flag, so
  "disabled" providers still joined the failover chain and were health-probed every cycle
  (surfaced as an endless `/models` retry loop against a dead endpoint). Adapters already
  honored the flag; providers now match. Operators relying on the old behavior must flip
  `enabled: true` on providers they actually use.
- HuggingFace template `base_url` updated `api-inference.huggingface.co/v1` →
  `router.huggingface.co/v1` (the old serverless domain was sunset upstream and no longer
  resolves).

## [0.9.0] — 2026-06-25

Convergence pass: provider-neutral mechanisms hardened in the upstream live system were
ported down as agnostic platform features. **No provider APIs, keys, or rosters are
included** — only the mechanisms; operators wire their own providers via config. See
[docs/convergence-2026-06.md](docs/convergence-2026-06.md) for the architecture.

> First release tagged from a clean CHANGELOG. Earlier `v0.7.0` / `v0.8.0` tags were cut
> mid-development and `main` ran ~54 commits past them; this release (0.9.0) is the next
> free version and rolls up that accumulated post-v0.8.0 work (vLLM provider, model
> lifecycle manager, mesh docs) on top of the convergence below. See `docs/project_state.md`
> and git history for the rolled-up detail.

### Added
- **`core/error_classifier`** — classifies a provider failure into `transient | quota |
  auth | bad_request | unknown`, driving retry-vs-advance-vs-stop in failover. Pure,
  provider-agnostic.
- **`core/memory_injector`** — the provider-neutral memory contract
  (`assemble_context` / `recall` / `remember` + value types + `TOOL_SPECS` + `dispatch_tool`
  + swappable injector). "Any model reads system memory the same way." Bound to the existing
  `RagStore` + `MemoryLoader`; activate with `enable_memory(rag, mem)`.
- **`core/schema_gate`** — structural schema-conformance gate (fail-open) so a structured-
  output caller can fail over when a backend returns valid-JSON-but-wrong-shape.
- **`core/capability_gate`** — feature activation/deferral gate. A feature declares a
  `CapabilityRequirement`; `evaluate()` against a `SystemCapabilities` snapshot returns
  active/deferred-with-reason. Features auto-light-up as a deployment grows.
- **`core/council_lease` · `council_checkpoint` · `council_resumer`** — multi-orchestrator
  failover: single-leader lease + monotonic fencing + cooperative knock, rich fencing-
  stamped checkpoints, and a decoupled resumer (injected callables — no adapter coupling).
  **Capability-gated: dark on a single-provider floor.** Optional Redis-compatible store
  (`NEXUS_COORD_REDIS_*`).
- **`core/identity`** — cross-adapter identity resolution: `resolve((platform, native_id))
  → person_id` with an owner floor + people registry, graceful on missing config. Composes
  with `core/security` (identity resolves *who*, security authorizes the action). Generic
  `config/identity.json.example` template.
- Test suites for all of the above (`tests/test_{capability_gate,council,identity,claude_code}.py`).

### Changed
- **`core/provider_chain`** — failover now uses `error_classifier`: retry-the-same-provider
  on transient, **stop the chain on `bad_request`** (was: every error treated identically);
  **classification-aware cooldown** (auth/quota benched ~1h vs 30s transient); **opt-in
  persistent health** (`ChainConfig.health_path`) so a benched provider survives a restart.
- **`core/bridge`** — `invoke()` runs the MemoryInjector seam once before any provider is
  chosen (recall→prompt, standing→system). Opt-in and behavior-preserving (no-op by default).
- **`providers/claude_code`** — backported `stream-json` + `--resume` + incremental
  `on_output` (was buffered `--output-format json` and silently dropped session resume).
- **`research/research_worker`** — page fetch/extract moved on-box (`httpx` + `trafilatura`),
  replacing the remote Jina Reader; nothing egresses before the synthesis step.
- **`requirements.txt`** — added `trafilatura` (local web extraction); `redis` listed as an
  optional dependency (council failover only).

### Removed
- Nothing. All changes are additive or enhance existing modules.

## [0.6.1] — 2026-06-09
- Documentation audit; provider count and model-id corrections; `.env.example` fixes.
- (See `docs/project_state.md` for the full pre-changelog history.)
