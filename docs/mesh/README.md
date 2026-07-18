# Nexus Mesh Documentation

> **This is a design concept — not in active development.** Nothing in these documents is
> built, and the mesh is not on the near-term roadmap. They capture a complete
> first-principles design so the thinking is ready if the idea is ever pursued. For what
> Nexus actually runs today, see the main [README](../../README.md) and
> [KNOWN_LIMITATIONS.md](../../KNOWN_LIMITATIONS.md).

Nexus Mesh is a *proposed* optional extension that would connect independent Nexus deployments into a collective inference network. Each node would remain sovereign — private data never leaves your machine. The mesh moves tasks and results only.

## Documents

| File | Contents |
|---|---|
| [01-overview.md](01-overview.md) | What it is, cost model, precedent systems (BitTorrent, Folding@Home, BOINC, exo), four modes (0/A/B/R) |
| [02-architecture.md](02-architecture.md) | Full stack diagram, resource governance, IPFS/libp2p transport, specialization, aggregate capability math |
| [03-security.md](03-security.md) | Threat model, isolation architecture, sandbox design, result validation, open research areas |
| [04-protocol.md](04-protocol.md) | Peer discovery, task descriptors, capability tags, ratio enforcement, reputation, trust revocation |
| [05-implementation.md](05-implementation.md) | Build-out roadmap (5 phases + optional Phase 6), security validation, stress testing |
| [06-scaffold.md](06-scaffold.md) | Phase 1 concrete starting point: file structure, core modules, unit tests, checklist |
| [07-evolution.md](07-evolution.md) | Optional extensions: Mode 0 local pool/E1 (exo-style, research deployments), provider delegation (E2), collective fine-tuning (E3), compute bartering (E4), phone-cluster (E5) |

## Quick Orientation

**Four deployment modes — each distinct:**

| Mode | Name | What It Does | Network | Latency |
|---|---|---|---|---|
| **Mode 0** | Local Pool | Shard one model across your LAN machines (exo-style). Your hardware, your hub. | LAN only | ~1-4s overhead |
| **Mode A** | Idle Mesh | Anonymous compute donation — your idle cycles serve others, theirs serve you | WAN | Seconds |
| **Mode B** | Trusted Sandbox | Named peers, explicit scoped grants, provider sharing | LAN or WAN | Seconds–minutes |
| **Mode R** | Research | Async batch research jobs. Deferred execution. Multi-model consensus chains. Set and return to results. | WAN | Hours (irrelevant) |

**Core principle**: Local inference always runs first. Mesh is supplemental. Cloud APIs are fallback only.

**Cost model**: Electricity. Not per-token. Every idle GPU cycle contributed to the mesh is free inference for the collective.

## Status

**Concept — not in active development.** This is a fully-documented *design*, not a work item
on the current roadmap. Nothing here is built. The documents capture the thinking from a
first-principles design pass (2026-06-05) so it's ready if the concept is ever picked up.

**If it were pursued**, the rough estimate was 18-30 weeks (solo) for Phases 1-5 (Modes A/B/R);
Mode 0 (local pool) would be a later, optional phase. See [05-implementation.md](05-implementation.md)
for the full design roadmap, security validation, and stress-testing plan.
