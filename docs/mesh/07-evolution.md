# Nexus Mesh — Evolution Pathways

**Optional architectural extensions that emerge from Mode B trusted sandbox collaboration.**

These are not required for initial mesh deployment but represent natural extensions once Mode B is operational and peer relationships mature.

---

## E1: Mode 0 — Local Pool / VRAM Pooling (Phase 6, Optional)

### What It Is

Mode 0 extends Nexus to orchestrate multiple machines on your own LAN as a single inference pool. Instead of each machine running only the models it can fit, layers of a large model are distributed across machines — enabling models that no single machine could load.

**Reference implementation**: exo (github.com/exo-explore/exo) implements this exact pattern. Mode 0 adds Nexus orchestration on top.

### The Bandwidth Constraint

This mode is **LAN-only by design**. Activation streaming between layers requires high bandwidth:

| Model | Activation size per pass | LAN (1 Gbps) overhead | WAN (50 Mbps) overhead |
|---|---|---|---|
| 70B | ~200-500 MB | 1.6-4s | 32-80s |
| 671B (R1) | ~500MB-1GB | 4-8s | 80-160s |

LAN: adds seconds. WAN: adds minutes per pass — unusable for interactive inference. Mode 0 stays on LAN.

### Design

```yaml
mesh:
  mode_0:
    enabled: true
    machines:
      - host: 192.0.2.10
        vram_gb: 16
        role: hub          # Nexus coordinator
      - host: 192.0.2.11
        vram_gb: 16
        role: worker
      - host: 192.0.2.12
        vram_gb: 16
        role: worker
    
    layer_allocation: proportional   # Assign layers proportionally by VRAM
    parallelism: pipeline            # Sequential layer execution (pipeline)
    transport: libp2p_noise          # Encrypted activation streaming
```

### Execution Model

1. **Layer allocation**: Nexus assigns layers proportionally by each machine's available VRAM
2. **Pipeline parallelism**: Activation tensors stream from machine to machine in layer order
3. **Nexus as hub**: Your Nexus instance coordinates routing, tracks which machine holds which layers
4. **Mode B extension**: LAN-connected trusted peers with explicit grants can participate in the same pool

```
Your Nexus (hub, 192.0.2.10):
  - DeepSeek-R1 671B layers 0-43    (16GB)

Worker machine (192.0.2.11):
  - DeepSeek-R1 671B layers 44-80   (16GB)
  
Activation flow: layer 43 output → stream to 192.0.2.11 → layer 44 input
```

### When This Makes Sense

- Research-scale deployments with 3+ local machines
- Organizations that need to run 70B+ models that don't fit on a single node
- Situations where WAN latency for Mode R is acceptable but local inference speed matters

### When Mode R Is a Better Fit

If the workload is async and latency-insensitive, Mode R (deferred queue over WAN) achieves multi-model diversity without the LAN hardware requirement. Mode 0 produces one model's output faster. Mode R produces many models' outputs independently.

**Mode 0 = run one big model across your machines.**
**Mode R = run many models across the network, combine results.**

### Suggested Timeline

**Phase 6** — after Phases 1-5 are operational and validated. Requires:
- Mode B sandbox isolation proven (Phase 3)
- libp2p encrypted transport operational (Phase 4)
- Ratio accounting in place (Phase 2-3)
- 3+ local machines available for testing

---

## E2: Provider Delegation Through Trusted Peer (Phase 3 Adjacent)

### Problem It Solves

Mode B already allows "I grant you access to my DeepSeek-R1 for project X." But what about API providers?

**Use case**: Peer A has an Anthropic API key with generous rate limits. Peer B wants to route some tasks through Peer A's key instead of burning their own quota.

### Design

Extend Mode B sandbox permissions to include **provider delegation**:

```yaml
mode_b_sandbox:
  name: "shared-research"
  peers:
    - id: peer-hardware-id-456
      permissions: 
        - "inference"
        - "provider_delegation"  # New: allow using their provider connections
  
  delegated_providers:
    - provider: "anthropic"
      models: ["claude-opus-4-7"]  # Restrict to specific models
      max_tokens_per_month: 1000000  # Rate limit on delegation
      cost_split: "proportional"     # Who pays for tokens used?
```

### Execution Model

1. Peer B submits a task to their local router
2. Router checks providers — local quota exhausted or rate-limited
3. Router checks Mode B trusted peers for delegated providers
4. Peer A's API credentials are used **within a scoped context only**
5. Peer B receives result; usage is metered and logged
6. **Cost attribution**: Token count attributed to Peer B's account for billing clarity

### Security Requirement

Provider credentials MUST be used in a **completely isolated subprocess** that:
- Never touches the main Nexus process
- Never accesses other provider credentials
- Returns only the inference result
- Logs all usage for attribution

This is functionally similar to the main sandbox isolation, just for provider access.

### Advantages

- Maximizes resource utilization (unused quota becomes available to trusted peers)
- Enables small teams to share premium API access
- Automatic fallback when one peer's quota is exhausted

### Disadvantages

- Trust risk: credentials in use by another peer
- Accounting complexity: who pays for delegated tokens?
- Quota gaming: one peer could monopolize shared access

### Suggested Timeline

**Phase 3 or Phase 3+**: Requires Mode B infrastructure and sandbox isolation proven.

---

## E3: Collective Model Fine-Tuning (Post-Phase 4)

### Concept

Once VRAM pooling is operational, several specialized peers could **collectively fine-tune a shared base model** on a domain-specific dataset.

Example: 5 peers in a medical AI research sandbox fine-tune Qwen-72B on clinical notes. Each peer contributes:
- Compute (GPUs idle-time for training passes)
- Data (anonymized patient outcomes, filtered locally before leaving the node)
- Validation (results are validated peer-by-peer)

Result: Customized medical model owned collectively, with provenance audit trail.

**This is speculative and far out.** Requires:
- WAN stability (Phase 4 prerequisite)
- Distributed training infrastructure (entirely new component)
- Data privacy validation (rigorous before clinical use)
- Fault tolerance (training interrupted by node failure = expensive)

**Not a Phase 1–5 priority.** Documented for long-term vision.

---

## E4: Ratio-Based Compute Bartering (Post-Phase 5)

### Concept

Once the mesh is production-stable and ratio tracking is reliable, enable **explicit compute trades between peers**:

```
Peer A to Peer B: "You have 2.0 ratio (surplus). I have 0.5 ratio (deficit).
Trade: I'll give you access to my DeepSeek-R1 for 2 weeks in exchange for 
running my 100-task batch jobs at priority."
```

Both peers agree, transaction is logged, ratio adjustments are finalized when complete.

**Status**: Vision only, not a roadmap priority. Enables peer-to-peer compute economics without blockchain or tokens.

---

## E5: Phone-LLM-Cluster Edge Integration (Concurrent with Phases)

### Concept

Phone cluster (low-power edge inference) integrated into mesh as a specialized node tier.

Each phone contributes:
- Lightweight classification tasks (text embedding, intent routing, filtering)
- Ultra-low-latency responses (edge proximity)
- Collective VRAM from the cluster (6 phones × 8GB = ~48GB theoretical aggregate)

Mesh learns phone cluster specialization and routes lightweight triage tasks to it automatically.

**Already documented** in phone-llm-cluster project. Mesh architecture is compatible with phone-tier nodes.

---

## Decision Criteria for Evolution Adoption

Before implementing any evolution:

1. **Is it needed for core mesh function?** (No → lower priority)
2. **Does it depend on earlier phases?** (Yes → timeline shifts)
3. **Does it increase security surface?** (Yes → requires audit)
4. **Does it add complexity vs. benefit?** (Ratio matters)
5. **Can operators opt out?** (Yes → safer adoption)

**E1 (VRAM pooling)** ranks high: Unlocks specialized peer value without breaking data sovereignty.

**E2 (Provider delegation)** ranks medium: Reduces quota friction but increases credential risk.

**E3 (Collective fine-tuning)** ranks low: Speculative, far-out, requires new infrastructure.

**E4 (Compute bartering)** ranks low: Vision-level, no immediate use case.

**E5 (Phone integration)** ranks medium-high: Already designed, aligns with board portfolio.

---

## Related Documents

- [02-architecture.md](02-architecture.md) — Full stack (VRAM pooling extends this)
- [03-security.md](03-security.md) — Sandbox isolation (provider delegation requires equal rigor)
- [04-protocol.md](04-protocol.md) — Ratio and trust (needed for all evolutions)
- [05-implementation.md](05-implementation.md) — Build phases (evolution timeline anchors here)
