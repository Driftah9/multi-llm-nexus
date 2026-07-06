# Changelog

All notable changes to Multi-LLM-Nexus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

### Added
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
