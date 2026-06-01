# Nexus Mesh Documentation

Nexus Mesh is an optional extension that connects independent Nexus deployments into a collective inference network. Each node remains sovereign — private data never leaves your machine. The mesh moves tasks and results only.

## Documents

| File | Contents |
|---|---|
| [01-overview.md](01-overview.md) | What it is, cost model, precedent systems (BitTorrent, Folding@Home, BOINC, Tor), two modes |
| [02-architecture.md](02-architecture.md) | Full stack diagram, resource governance, ONS transport, specialization, aggregate capability math |
| [03-security.md](03-security.md) | Threat model, isolation architecture, sandbox design, result validation, open research areas |
| [04-protocol.md](04-protocol.md) | Peer discovery, task descriptors, capability tags, ratio enforcement, reputation, trust revocation |
| [05-implementation.md](05-implementation.md) | Build-out roadmap (5 phases), security validation practices, stress testing requirements |
| [06-scaffold.md](06-scaffold.md) | Phase 1 concrete starting point: file structure, core modules, unit tests, checklist |

## Quick Orientation

**Two mesh modes exist and are distinct:**
- **Mode A (Public Mesh)**: Anonymous compute donation. Maximum isolation. Folding@Home model.
- **Mode B (Trusted Sandbox)**: Explicit peer access. Named permissions. Provider sharing between known operators.

**Core principle**: Local inference always runs first. Mesh is supplemental. Cloud APIs are fallback only.

**Cost model**: Electricity. Not per-token. Every idle GPU cycle contributed to the mesh is free inference for the collective.

## Status

Design phase — 2026-05-31. Concepts documented from first-principles conversation.

**Build estimate**: 18-30 weeks (4.5-7.5 months) solo developer, 5 phases.
See [05-implementation.md](05-implementation.md) for full roadmap, security validation, and stress testing.
