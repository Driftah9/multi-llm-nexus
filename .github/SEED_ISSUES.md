# Seed issues — file these to bootstrap the tracker

These are the known gaps from the 2026-07-18 doc/status audit, pre-written so the tracker
starts populated with honest, actionable work instead of an empty page. File each with
`gh issue create` (repo already has the templates + labels). This file is a checklist —
delete rows as they become real issues, or delete the file once seeded.

> Requires `gh auth login` and push access. Nothing here is filed automatically.

| # | Title | Labels | One-liner |
|---|---|---|---|
| 1 | Provider-level parallel fan-out (same prompt → N providers → synthesize) | `roadmap`, `good-first-hardening` | Un-stub `TierPoolConfig.parallelism: parallel`; add gathered fan-out + judge/synthesis in `bridge.py`. The #1 vision gap. |
| 2 | Self-improvement loop is documented but not implemented | `docs`, `overclaim`, `roadmap` | Either build the eval→candidate→approval loop or clearly mark it design-only everywhere it's described. |
| 3 | Citadel "routes around busy pools out of the box" overclaims | `docs`, `overclaim` | Pool config is read but auto-around-busy routing isn't wired; soften README §Citadel (done in docs) and track the real wiring. |
| 4 | Fresh-install adapter attach is unproven | `edge-case`, `blocker` | A clean install needs a real bot token + correct team before the engine goes ACTIVE; document and smoke-test the first-answer path end to end. |
| 5 | Validate swarm ON-path beyond the single test run | `flag-gated`, `edge-case` | Exercise `SWARM_LOOP_ENABLED=1` across varied tasks/providers; watch worker diversity as the ladder accumulates grades. |
| 6 | Slack + Matrix adapters | `adapter`, `roadmap` | Config reserved; no code. |
| 7 | Unify provider count language across docs | `docs` | Canonicalize to "8 provider-type adapters fronting 22 selectable providers (100+ models)". |
| 8 | Unify the two quota/health layers | `roadmap` | `provider_quota.py` + `pool_manager` rate state + `llm-watcher` should converge on one source of truth. |

Suggested one-liner for filing #1 (adapt per row):

```bash
gh issue create \
  --title "Provider-level parallel fan-out (same prompt → N providers → synthesize)" \
  --label roadmap --label good-first-hardening \
  --body "Un-stub TierPoolConfig.parallelism: parallel (pool_manager.py:211,306). Add a parallel path in bridge.py: fan the same prompt to N providers via asyncio.gather, route results through a judge/synthesis step, gate on task_value + headroom. See docs/BUILDOUT_STATUS.md Phase 3."
```
