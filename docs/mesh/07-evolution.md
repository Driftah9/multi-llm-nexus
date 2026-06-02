# Nexus Mesh — Evolution Pathways

**Optional architectural extensions that emerge from Mode B trusted sandbox collaboration.**

These are not required for initial mesh deployment but represent natural extensions once Mode B is operational and peer relationships mature.

---

## E1: VRAM Pooling Grants (Post-Phase 3)

### Problem It Solves

Mode B already enables specialized peer collaboration — diverse reasoners producing independent chains in parallel. But there are use cases where two peers want to run a single model that neither could fit alone:

- Peer A: V100 16GB + code-specific models
- Peer B: V100 16GB + reasoning models
- Shared need: DeepSeek-R1 671B (needs 32GB+ in optimal precision)

Current Mode B doesn't support this. Each peer can delegate inference to the other, but only for models the peer already loads locally.

### Design

Extend the Mode B trust relationship to include **scoped VRAM sharing**:

```yaml
mode_b_sandbox:
  name: "research-collab"
  peers:
    - id: peer-hardware-id-123
      permissions: ["inference", "vram_loan"]  # New permission
  
  vram_loan:
    enabled: true
    max_gb: 16                  # Loan up to 16GB from this peer
    auto_return: true           # Return immediately after task completes
    # Peer's declaration: "You can use up to 16GB of my VRAM for collaboratively-owned models"
```

### Execution Model

1. **Joint Model Registration**: Both peers agree on a model to co-host
2. **Layer Distribution**: Layers sharded across peers (similar to how Hyperspace Pods does it)
3. **Pipeline Parallelism**: Activations streamed between peers via ONS encrypted transport
4. **Task Routing**: When either peer needs the model, it's already partially loaded on both
5. **Trust Isolation**: Only the explicitly-agreed model is shared; all other models remain private
6. **Ratio Accounting**: Compute contribution is tracked per peer (who did more work?)

### Example: Collaborative Research

```
Peer A (reasoning specialist):     Peer B (code specialist):
- DeepSeek-R1 layers 0-43         - DeepSeek-R1 layers 44-80
- Qwen-Coder 32B (local only)     - Custom RAG stack (local only)
- Phi-2 (local only)              - Piper TTS (local only)
```

Both peers can now run the full 671B model, but each carries only half the VRAM cost. Activations stream across their dedicated encrypted link. All other models remain private — no data sovereignty violation.

### Advantages Over Hyperspace Pods Model

- **Voluntary**, not mandatory. Peers opt into specific models.
- **Scoped**, not global. Each peer controls what gets shared.
- **Revocable**. Either peer can withdraw immediately.
- **Ratio-tracked**. Compute contributions are metered and decay-adjusted.
- **Private-by-default**. Only the agreed model is shared; all context remains local.

### Disadvantages

- **Latency overhead**: Pipeline parallelism slower than local execution (activation streaming adds latency per layer).
- **Bandwidth cost**: Each layer boundary requires activation streaming (typically 100MB–1GB per forward pass, depending on model).
- **Complexity**: Implementation requires careful management of partially-loaded models, failure recovery, and state synchronization.

### Suggested Timeline

**Post-Phase 3** (Trust and Mode B fully operational). Requires:
- Mode B sandbox isolation proven
- ONS encrypted transport layer proven
- Ratio accounting infrastructure in place
- Security review of shared-model state (ensure isolation still holds)

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
