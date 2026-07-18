# Known Limitations & Honest Status

> **Read this before filing an issue** — and before trusting a feature in production.
> Multi-LLM-Nexus is **Active Beta**. This document is the honest line between what the
> code *does today* and what the project is *designed to become*. If something below bites
> you, it's a known gap, not a surprise — but a good [edge-case report](.github/ISSUE_TEMPLATE/edge_case.yml)
> on it still helps us harden faster.
>
> Sources of truth this summarizes: [`docs/BUILDOUT_STATUS.md`](docs/BUILDOUT_STATUS.md)
> (verified status) and [`CHANGELOG.md`](CHANGELOG.md).

_Last reviewed: 2026-07-18 · against version 0.9.0 + convergence._

---

## The honest one-paragraph version

The **provider layer, tier routing, rollover/failover, quota governance, session/memory,
and the heartbeat status display are real and work.** The engine serves an
OpenAI-compatible API and health-monitors its providers. What is **not** proven or is
**inert**: guaranteed one-command "it answers on your chat platform" out of the box;
provider-level *parallel* fan-out; the self-improvement loop; and the swarm / capability
ladder / council system (present but flag-gated off). It has been run and hardened by
**one operator** — you may be the first to hit your particular corner.

---

## Not built yet (designed, documented, absent from code)

| Feature | Doc says | Reality |
|---|---|---|
| **Self-improvement loop** (bi-weekly eval → candidate queue → approval) | Described as a feature | Design only. No module exists. Listed in AGENTS "What's Next". |
| **Provider-level parallel** (same prompt → N providers → synthesize) | "execute in parallel" | Parallel exists across *specialist roles*, not across *providers*. `TierPoolConfig.parallelism: parallel` is parsed but never branched on (`pool_manager.py`). Highest-leverage open feature. |
| **Citadel auto-routing "out of the box"** | README §Citadel | Pool config is read; automatic routing *around busy pools* is not wired (same stub as above). |
| **Nexus Mesh** (federated inference network) | 6 design docs | **Concept — not in active development.** Fully documented design, nothing built, not on the near-term roadmap. See [docs/mesh/](docs/mesh/README.md). |
| **Slack / Matrix adapters** | "Planned" | Config keys reserved; no adapter code. |

## Present but INERT (flag-gated off by default)

| Feature | Flag | Notes |
|---|---|---|
| **Swarm orchestration loop** (plan → capability-route → parallel waves → review) | `SWARM_LOOP_ENABLED=0` | Ported, tested (194 tests), deployed inert. ON-path validated once against real providers; **not battle-tested**. Enabling it is an operator decision. |
| **Capability graduation ladder** (models earn trust per-domain via graded evidence) | tied to swarm/grading path | Foundation live; accrues data only as grading fires. Cold-start routes narrowly until graded history exists. |
| **Fixed-role council** (Skeptic / Advocate / Verifier) | not on the standard reply path | Replaced the old tournament model. Wired but not invoked in normal single-shot replies. |

## Works, but only proven by ONE operator

Everything has been exercised on a single operator's hardware and provider mix. Untested
in the wild:
- **Adapter attach on a fresh install** — the reference deployment reuses an existing bot
  token/team. A clean install must still supply a real Mattermost bot token and correct
  team before the engine goes ACTIVE and answers. CPU-only triage is slow (5–10s/message)
  without a GPU.
- **Provider combinations** other than the reference set. Retired cloud models are a moving
  target; schema validation + a health check catch many at boot, but not all.
- **Multi-user / multi-tenant** — the "users" (plural) story is designed, not proven. Treat
  it as single-operator today.
- **Hardware tiers** beyond the tested one (phone clusters, dual-V100 "Dreadnought",
  server-grade pools) are documented *designs*, not validated deployments.

## Safety posture (why the above is OK to ship)

Nexus is built to **fail closed and loud**: no destructive action without confirmation,
provider failures fall back rather than crash the reply, and the self-diagnostic
(`nexus doctor`) **never phones home** — it prints a string you choose to share. An edge
case should cut the *input*, not the operator's data. If you find one that doesn't,
that's a `data-safety` issue and the highest priority we have.

---

*This file is maintained by hand alongside `docs/BUILDOUT_STATUS.md`. If you find a claim
elsewhere in the docs that this contradicts, the more conservative statement wins — please
open a `docs` + `overclaim` issue.*
