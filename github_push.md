# Push: Documentation sync for PW-3→PW-8

## Commit Message
```
docs: sync documentation with PW-3→PW-8 changes

- INSTALLER_RESILIENCE.md: recast as two-layer (preflight + checkpoint);
  removed the deleted CI-matrix/GitHub-Actions layer; testing is local-only
- README.md: add optional preflight step + note boot-time config schema validation
- CHANGELOG.md: add Unreleased entries for PW-5 (config_schema), PW-6 (installer
  resilience), PW-8 (triage_validator ext), PW-3 (model swap), PW-4 (sessions.json)
- BUILDOUT_STATUS.md: mark Cerebras-404 blocker resolved; swap retired models
- convergence-2026-06.md: add PW-3→PW-8 hardening-pass section
- AGENTS.md: update status header to 0.9.0 + convergence hardening
- CONTRIBUTING.md: note config/*.yaml is schema-validated at boot
- Retired-model swaps in SYSTEM_DESIGN, POOL_ROUTING_REFACTOR,
  provider-integration-roadmap, api-key-setup-guide (405b→DeepSeek-V3.2,
  qwen-3-235b→gpt-oss-120b)

All documentation now reflects the hardening and feature-port work completed
on convergence-port-2026-06 branch. No retired models referenced in docs.
No CI-workflow references for installer testing (interactive installers can't
run headless).
```

## Branch
convergence-port-2026-06 (or main if merging)

## Version
0.9.0-unreleased
