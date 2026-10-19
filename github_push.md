# Push: Live → Nexus convergence, October 2026

## Commit range
`origin/main..main` — 7 previously-unpushed commits from 2026-09-25/26 (domain classifier
integration, self-improvement loop) + 10 convergence commits from 2026-10-19.

## Summary
Ports the live claude-brain changes since the last sync (2026-09-26). Full record, including
what was deliberately NOT ported and why: `docs/LIVE_CONVERGENCE_2026-10.md`.

- feat(guard): workspace guard — the office model (`core/workspace_guard.py`, agent-loop
  `guard_fn`, `scripts/workspace_guard_hook.py`)
- feat(recovery): mechanical provider recovery + operator announcements in the chain
  HealthMonitor; `core/outage_reason.py`
- feat(council): contradiction scan; worker-health gate on council members
- feat(capability): operator baseline priors + qualification flap guard
- feat(routing): chain telemetry wired; demote-only routing weights
- fix(adapters): every adapter reply crashed before posting (`ReviewTrigger.NONE`, since
  v0.6.0); turn-failure surfacing
- feat(ops): error inbox — `run_tracked.py` + `nightly_sweeper.py`
- feat(notify): protocol failover + persisted circuit breaker
- feat(security): `secrets_lint.py` over tracked files; repo-clean test
- fix(error_classifier): invalid key returned as HTTP 400 is AUTH, not BAD_REQUEST

## Validation
- Suite: 432 passed (baseline 275).
- `scripts/secrets_lint.py`: 0 findings in tracked files.
- Real-system: boot smoke on the deployment's real provider config; one live provider
  round-trip through the chain (telemetry → weights); sweeper output byte-identical to live on
  real host state.

## Branch
main (fast-forward from `port/live-convergence-2026-10`)

## Version
0.9.0-unreleased
