# Changelog

All notable changes to Multi-LLM-Nexus are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [0.7.0] — Unreleased

Convergence pass: provider-neutral mechanisms hardened in the upstream live system were
ported down as agnostic platform features. **No provider APIs, keys, or rosters are
included** — only the mechanisms; operators wire their own providers via config. See
[docs/convergence-2026-06.md](docs/convergence-2026-06.md) for the architecture.

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
