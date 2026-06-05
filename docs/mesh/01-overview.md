# Nexus Mesh — Overview

**The point where individual Nexus deployments stop being isolated and become a collective intelligence.**

---

## What Is Nexus Mesh?

Nexus Mesh is an optional extension to any Nexus deployment that allows operators to:

1. **Contribute** idle inference compute to a shared pool
2. **Consume** inference from the pool when their local resources are saturated or insufficient
3. **Collaborate** with trusted peers in explicitly-scoped shared sandbox environments

Each Nexus node remains sovereign. Your private memory, sessions, adapter connections, and operator config never leave your machine. The mesh moves **tasks** and **inference results** — never personal data.

---

## The Cost Model Inversion

Every commercial AI platform charges per-token. Every token you generate costs money. The meter runs whether you're doing deep research or asking a simple question.

Nexus Mesh operates on a different model entirely:

```
Commercial AI:   You pay per-token. Inference has marginal cost forever.
Nexus Mesh:      You pay electricity. Inference is free at the margin.
```

A V100 SXM2 running 24/7 costs approximately **$0.86/day** at average residential electricity rates. That's the total inference cost for everything that GPU can produce. Once you own the hardware, every additional token is free. Idle cycles are wasted value — mesh participation converts that waste into collective capability.

**Local inference is always the default.** Cloud APIs exist as fallback for tasks that require live data, real-time search, or models the local stack can't serve. The mesh extends local-first to a collective pool of locally-owned hardware.

---

## Precedent Systems

Nexus Mesh is not a new idea at the component level — it synthesizes proven models:

### BitTorrent
Distributed file sharing with ratio enforcement, peer discovery, and swarm health. The key insight: cost to participate is bandwidth + electricity, not per-download fees. A large healthy swarm outperforms any single server.

**Applied to mesh**: Tasks are parceled across idle nodes. Nodes that contribute more get priority access. The swarm of inference nodes is more capable than any single node.

### Folding@Home / BOINC
Scientific compute donated by volunteers. Idle GPUs and CPUs run protein folding simulations or mathematical computations when the owner isn't using them. Aggregate output rivals research clusters.

**Applied to mesh**: Idle Nexus nodes donate inference cycles to the pool. A mesh of budget nodes running varied models can collectively handle workloads no single node could.

### Windows Update Delivery Optimization
Microsoft P2P update distribution. Machines on the same LAN share update data with each other, reducing external bandwidth load. Users can schedule when sharing is allowed and set bandwidth caps.

**Applied to mesh**: Mesh participation respects operator-defined schedules and bandwidth limits. You can say "mesh gets access after 11pm" or "never on weekdays."

### BOINC Result Validation
BOINC runs the same computation on multiple independent nodes and compares results to detect corruption or cheating. Agreement validates the result; disagreement triggers re-computation.

**Applied to mesh**: High-stakes inference tasks can be run on multiple nodes. Divergent outputs surface for review. Consistent results can be trusted as validated.

### Tor (Isolation Model)
Tor relays traffic between layers with no single relay seeing the full picture. Isolation between layers prevents any relay from knowing both origin and destination.

**Applied to mesh**: Mesh nodes process tasks in isolation. A node processing a mesh request sees only the inference payload — never who originated it, what their private context is, or what the result will be used for.

### Hyperspace Pods (Deployed Validation)
Hyperspace is a production distributed inference network with 2M+ autonomous nodes as of early 2026. Uses libp2p for peer discovery and tensor/pipeline parallelism to shard single large models across machines that could not fit them individually. 660+ autonomous agents running 27,000+ experiments validated the model at scale.

**Applied to mesh**: Proves the core premise — idle compute across distributed consumer hardware aggregates into meaningful inference capacity. Hyperspace is the clearest existing validation that mesh-style distributed inference works outside of academic settings.

**Where Nexus Mesh diverges**: Hyperspace solves one problem: run one model that doesn't fit in a single machine's VRAM. Each node carries a slice of the same model; activations stream between layers. Nexus Mesh solves a different problem: run many different models across independent nodes and combine their reasoning. Mode B (Trusted Sandbox) enables diverse specialized reasoners executing in parallel — a code-specialist, a reasoning-specialist, and a domain-specialist reasoning independently on the same problem. Mode R extends this further: N nodes producing N independent reasoning chains asynchronously, synthesized into one output. That produces composite intelligence a single large model cannot replicate. Mode 0 (E1) covers Hyperspace's specific use case — LAN machines pooling VRAM to run one large model together — but as a deferred optional extension, not the primary design.

---

## Four Deployment Modes

Nexus Mesh operates in four distinct modes. Each has a different trust model, network requirement, and use case. They are not interchangeable and do not share infrastructure.

---

### Mode 0: Local Pool

Your machines, your LAN, your Nexus as the hub. No external network required.

- Shard one large model across multiple machines you own on the same LAN
- Pipeline parallelism — each machine holds a slice of the model's layers
- Activations stream between machines on the LAN (fast enough to be practical)
- Use case: run a model that doesn't fit in any single machine's VRAM
- Nexus orchestrates which machine handles which layers
- All machines are yours — trust is implicit

**Trust level**: Implicit (your hardware). **Network**: LAN only. Activation streaming makes WAN impractical.

**Reference implementation**: exo (github.com/exo-explore/exo) — same pipeline parallelism pattern. Mode 0 adds Nexus orchestration and multi-operator capability for LAN trusted peers.

**Status**: Phase 6 — deferred. Designed for research-scale deployments with multiple local machines.

---

### Mode A: Idle Mesh (Public Compute Donation)

Anonymous contribution of idle inference cycles. Analogous to Folding@Home and BOINC.

- Tasks arrive from the mesh as opaque inference payloads
- Your node sees: the prompt, the model required, the output format
- Your node never sees: who asked, why, what system it's part of
- Maximum isolation: mesh execution is fully sandboxed from your private Nexus environment
- Ratio enforcement: nodes that contribute more get priority access to the pool
- Resource governance: owner-defined throttle, schedule, and bandwidth limits apply

**Trust level**: Zero. Every mesh task is assumed potentially hostile until executed in sandbox.

---

### Mode B: Trusted Sandbox (Explicit Peer Access)

Direct peer-to-peer trust relationships with known operators. Analogous to giving a colleague access to a specific shared workspace.

- Operator A explicitly grants Operator B access to a named sandbox
- Sandbox can contain shared context, tools, and optionally provider access
- "You can use my DeepSeek-R1 for this project" — direct provider sharing
- Trust is scoped, explicit, and revocable
- LAN trusted peers can extend into Mode 0 VRAM pooling
- Still sandboxed from private data, but richer interaction is allowed

**Trust level**: Named, hardware-bound, cryptographically verified. Revocable by either party.

---

### Mode R: Research (Async Batch)

Long-running research workloads with no interactive latency requirement. Analogous to BOINC / Folding@Home for scientific compute — submit a mission, come back to results.

- Submit a research task to the deferred queue
- Nodes execute during their idle windows — no real-time response expected
- Multi-node consensus: the same question sent to N nodes produces N independent reasoning chains from N different models
- Synthesis happens when all chains complete
- You return hours or days later to a finished result
- WAN activation streaming (slow) is acceptable here — latency is irrelevant to the outcome

**Trust level**: Mode A (anonymous, sandboxed) or Mode B (named, scoped) — operator's choice.

**Use case examples**: Deep research across conflicting sources, overnight analysis of large datasets, multi-perspective synthesis on complex topics, batch classification at scale.

**The key property**: 10 nodes running 10 different models on the same question produces 10 independent reasoning chains. No single model or datacenter can replicate this — they produce one chain, however many machines support it. Mode R produces a synthesized output that is qualitatively broader than any single inference.

---

## Prior Art — What Exists and Where the Gap Is

Several distributed inference projects exist. None combine what Nexus Mesh does.

| System | Multi-Operator | Privacy | Ratio/Reputation | Agent Platform | Mode | Status |
|---|---|---|---|---|---|---|
| **Petals** (BigScience) | ✓ | ✗ | ✗ | ✗ | Layer sharding | Research only |
| **Exo** | ✗ (your devices only) | ✗ | ✗ | ✗ | Layer sharding (LAN) | Open source, active |
| **Hyperspace Pods** | ✓ | ✗ | ✗ | ✗ | Layer sharding (WAN batch) | Deployed, 2M+ nodes |
| **Hivemind** | ✓ | ✗ | ✗ | ✗ | Layer sharding | Research library |
| **Bittensor** | ✓ | ✗ (blockchain = public) | ✓ (crypto tokens) | ✗ | Complete models | Deployed, crypto-native |
| **Federated LLM research** (arxiv) | ✓ | ✓ | ✗ | ✗ | Varies | Papers only |
| **vLLM / NVIDIA Dynamo** | ✗ (single org) | ✗ | ✗ | ✗ | Single org | Enterprise, centralized |
| **Nexus Mesh** | ✓ | ✓ | ✓ | ✓ | Complete models + layer sharding (Mode 0) | Design phase |

**Exo and Hyperspace** are the clearest technical references for Mode 0 (local pool) and Mode R (async batch) respectively. Neither is being adopted wholesale — they validate specific patterns. Nexus Mesh is not a fork of either.

**Critical architectural distinction — Petals vs. Nexus Mesh:**

Petals shards a SINGLE model's layers across multiple nodes. All nodes work together to produce one inference from one model. It is a distributed execution of one model.

Nexus Mesh keeps each node running its own COMPLETE models independently. Nodes produce diverse, independent inference chains from different models. It is an ensemble of independent reasoners.

These are fundamentally different architectures with different outputs. Petals makes one model faster. Nexus Mesh makes inference broader and more diverse.

**Why Bittensor doesn't solve this:**

Bittensor has ratio/reputation via blockchain tokens, which is the closest to Nexus Mesh's incentive model. However:
- Blockchain transparency is structurally incompatible with data sovereignty
- Crypto-native — requires token economics to participate
- No agent platform integration (adapters, orchestrator, sessions, memory)
- No trusted sandbox mode between known peers
- Identity is wallet-based, not hardware-bound

**The gap Nexus Mesh fills:**

No deployed system combines: multi-operator federation + data sovereignty + ratio enforcement + agent platform integration + hardware-agnostic (budget to Citadel) + four deployment modes (0/A/B/R) + local-first routing. This is original design space.

---

## What Makes This Different

No commercial or open-source system in 2026 combines all of the following:

| Capability | Nexus Mesh | Commercial AI | Open Source Alt |
|---|---|---|---|
| Local-first inference | ✓ | ✗ (cloud native) | Partial |
| Multi-provider routing | ✓ | ✗ (single provider) | Partial |
| Distributed mesh inference | ✓ | ✗ | ✗ |
| Data sovereignty | ✓ | ✗ | Partial |
| Compute donation / ratio | ✓ | ✗ | ✗ |
| Trusted sandbox collaboration | ✓ | ✗ | ✗ |
| Provider sharing between peers | ✓ | ✗ | ✗ |
| Zero marginal inference cost | ✓ | ✗ | ✓ (local only) |
| Multi-protocol adapters | ✓ | ✗ | ✗ |
| Self-improving (skill/memory) | ✓ | ✗ | ✗ |

**The key distinction**: Commercial AI platforms distribute ONE model across many machines so it runs faster. Nexus Mesh distributes MANY different models across many machines so it reasons more broadly. These are architecturally opposite approaches.

---

## What the Mesh Is Not

- **Not a training cluster**: Mesh nodes do inference only. No weights are shared, modified, or trained across the mesh.
- **Not a replacement for your local stack**: Local inference always takes priority. Mesh is supplemental.
- **Not mandatory**: Fully optional. A standalone Nexus deployment with no mesh participation is complete and functional.
- **Not a cloud service**: No central coordinator can revoke access, change pricing, or shut down the mesh. Peers connect directly.

---

## Related Documents

- [02-architecture.md](02-architecture.md) — Technical layers, resource governance, IPFS/libp2p transport
- [03-security.md](03-security.md) — Threat model, isolation architecture, sandbox design
- [04-protocol.md](04-protocol.md) — Task descriptors, peer discovery, ratio enforcement, result validation
- [citadel-tier.md](../citadel-tier.md) — High-end hardware that maximizes mesh contribution
- [phone-llm-cluster.md](../phone-llm-cluster.md) — Low-cost edge nodes in a Nexus deployment
