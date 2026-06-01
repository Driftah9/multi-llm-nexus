# Nexus Mesh — Architecture

---

## Full Stack Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 0: HARDWARE (per node — any tier)                        │
│  Budget: CPU + RAM + optional GPU (V100 16GB, RTX 3090, etc.)   │
│  Mid: Dual GPU + 128GB RAM                                      │
│  Citadel: 8-GPU SXM2 server (e.g. multi-GPU HPC node, NVLinked) │
│  Edge: Phone-LLM-Cluster (CPU inference via Exo/llama.cpp-rpc)  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: INFERENCE ENGINE (per node)                           │
│  ik_llama.cpp (GPU, MoE-optimized) │ Ollama (CPU/GPU, general) │
│  Phone cluster (edge, lightweight) │ vLLM (high-throughput)    │
│  Hot-loadable model library — operator-curated per node        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2: NEXUS CORE (private zone — never exposed to mesh)     │
│  Provider Router │ Orchestrator │ Sessions │ Memory │ Adapters  │
│  ─────────────────────────────────────────────────────────────  │
│                    ISOLATION BOUNDARY                           │
│  ─────────────────────────────────────────────────────────────  │
│  LAYER 2b: MESH SANDBOX (isolated execution zone)              │
│  Accepts incoming task payloads │ Runs inference only           │
│  No access to private zone      │ Returns result only           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: MESH PROTOCOL (ONS transport backbone)               │
│  Peer discovery (DHT) │ Task routing │ Result validation        │
│  Ratio tracking │ Trust registry │ Revocation propagation      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 4: MESH NETWORK (swarm of nodes)                        │
│  10 nodes → 10 independent inference chains                    │
│  Specialist diversity → each node hot-loads what it excels at  │
│  Aggregate throughput → scales linearly with node count        │
└─────────────────────────────────────────────────────────────────┘
```

---

## ONS as Transport Backbone

The Orbital Network Sync (ONS) project was designed with hardware-bound device identity, P2P encrypted transport, friend-group cooperative mode, and distributed revocation. These primitives map directly onto mesh requirements:

| ONS Primitive | Mesh Use |
|---|---|
| Hardware-bound identity | Node authentication (cannot spoof a node) |
| Friend-group cooperative mode | Trusted peer relationships (Mode B) |
| P2P encrypted transport | Task payloads and results cross mesh securely |
| Distributed revocation | Remove a bad actor without central authority |
| No central server | Mesh operates even if coordinator goes down |

ONS moves files. Nexus Mesh moves inference tasks. Same protocol layer, different payload type. The transport is already designed.

---

## Resource Governance

Resource governance defines how much of your hardware the mesh can use and when.

### Priority Hierarchy

```
1. OWNER ACTIVE USE      → Mesh receives NOTHING (hard preemption, no queue)
2. OWNER TASK IN QUEUE   → Mesh suspended until queue clears
3. SYSTEM IDLE           → Mesh receives authorized throttle
4. SCHEDULED WINDOW      → Mesh receives authorized throttle only during window
```

**Hard preemption** means when you start using your system, mesh tasks are killed immediately. Not paused. Not "finish the current piece." Killed. Your hardware is yours.

### Throttle Configuration

```yaml
# Example mesh resource config
mesh:
  enabled: true
  mode: public          # public | trusted | both | disabled

  resources:
    gpu_idle_percent: 50        # % of GPU available to mesh when idle
    cpu_idle_percent: 30        # % of CPU available to mesh when idle
    ram_reserved_gb: 8          # Always reserved for owner, never touched by mesh
    preempt_immediately: true   # Kill mesh tasks the moment owner needs resources

  network:
    upload_mbps: 10             # Max upstream bandwidth for mesh traffic
    download_mbps: 10           # Max downstream bandwidth
    per_connection_mbps: 2      # Per-peer connection cap

  schedule:
    enabled: false              # Set to true to restrict mesh to time windows
    windows:
      - days: [mon, tue, wed, thu, fri]
        hours: "23:00-07:00"    # Weeknights only
      - days: [sat, sun]
        hours: "00:00-23:59"    # All weekend
```

### GPU Idle Detection

The mesh controller monitors GPU utilization on a configurable interval (default: 5 seconds). States:

- **ACTIVE** (>60% utilization or owner process detected): Mesh suspended
- **TRANSITIONING** (30-60%): Mesh held pending, not yet allocated
- **IDLE** (<30%): Mesh may allocate up to `gpu_idle_percent`

When owner activity returns:
1. Mesh tasks receive SIGTERM (graceful attempt, 2 second window)
2. If not cleared: SIGKILL
3. GPU reallocated to owner

The mesh coordinator is notified that the node is unavailable. It re-routes pending tasks to other available nodes.

---

## Model Specialization — The "Rare Piece" Value

In BitTorrent, seeders holding rare pieces are the most valuable nodes in a swarm. The same principle applies to the mesh.

A mesh where every node runs the same 7B model provides redundancy but no diversity. A mesh where nodes specialize by capability produces compounding value:

| Node Type | Specialization | Mesh Value |
|---|---|---|
| Citadel + DeepSeek-R1 671B | Deep reasoning chains | Extremely high (rare, powerful) |
| Mid-tier + Qwen-Coder 32B | Complex code analysis | High (specialized) |
| Budget + Whisper + Piper | Voice STT/TTS | High if no other voice nodes |
| Budget + nomic-embed | RAG / vector retrieval | Moderate (useful, common) |
| Budget + 7B general | General Q&A, triage | Low individual, high collective |
| Phone cluster | Lightweight classification | Low per-phone, scalable |

Operators are encouraged to specialize their node's model library around their hardware and use case. The mesh benefits from diversity more than uniformity.

---

## Two-Mode Topology

### Mode A: Public Mesh (Anonymous Compute Pool)

```
Node A ──▶ Mesh Coordinator ──▶ Node B (idle, sandboxed)
                               Node C (idle, sandboxed)
                               Node D (idle, sandboxed)
```

- Tasks routed by coordinator based on model requirements, node availability, and ratio
- Requesting node knows result; serving node does not know requester
- All execution in isolated sandbox (see security doc)
- Ratio tracked per hardware-bound identity

### Mode B: Trusted Sandbox (Explicit Peer Network)

```
Operator A ◀──▶ Shared Sandbox ◀──▶ Operator B
     │                                    │
     └──── Operator A's private zone      └──── Operator B's private zone
           (never shared)                       (never shared)
```

- Operators explicitly invite peers to named sandboxes
- Sandbox contains shared context, collaborative workspaces, optionally shared providers
- "I grant you access to my DeepSeek-R1 for project X" is a scoped, revocable grant
- Private zones remain entirely separate

Modes A and B run on separate infrastructure paths. A node participating in both maintains separate execution contexts for each.

---

## Aggregate Capability — Why the Math Matters

### 10 Budget Nodes in Mesh

Each node: V100 16GB, 64GB RAM, 24 cores

- Aggregate VRAM: 160GB
- Aggregate RAM (expert streaming): 640GB
- Average idle capacity (70% idle assumed): ~112GB VRAM available to mesh at any moment
- Complex research task: parceled into subtasks, distributed across 7 idle nodes simultaneously
- Independent reasoning chains returned, synthesized at requesting node

Cost to the mesh: electricity. No token metering. No API keys.

### 10 Citadel Nodes in Mesh

Each node: 8× V100 32GB, 2× EPYC 7742, 128GB RAM

- Aggregate VRAM: 2,560GB (80 GPUs)
- Aggregate RAM: 1,280GB
- Aggregate CPU cores: 1,280 (at full mesh participation)
- Capability: rivals a small AI research cluster
- Cost: electricity per operator, zero marginal inference

### The Parallel Reasoning Advantage

A datacenter cluster serves ONE model to many users. The clustering exists to make one model faster.

A Nexus mesh runs MANY different models, each independently. The clustering produces diversity of perspective, not just speed.

**10 nodes running DeepSeek-R1 671B independently on the same complex question produces 10 distinct reasoning chains.** No single datacenter model can do this — it can only produce one chain, however many machines support it. Synthesizing 10 independent reasoning chains is a qualitatively different output.

---

## Graceful Degradation

The mesh is supplemental. Local-first is always the default.

When mesh nodes drop offline (maintenance, owner activity, network issues):
- Requesting node falls back to local inference
- If local is insufficient: cloud API fallback (existing Nexus behavior)
- No hard dependency on mesh availability

A 10-node mesh that drops to 3 produces slower queue times, not failures. The system degrades gracefully because no single node is required.

---

## Related Documents

- [01-overview.md](01-overview.md) — Concept, analogies, two modes
- [03-security.md](03-security.md) — Threat model, isolation, sandbox design
- [04-protocol.md](04-protocol.md) — Wire protocol, peer discovery, ratio enforcement
