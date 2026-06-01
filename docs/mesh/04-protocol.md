# Nexus Mesh — Protocol

**How nodes find each other, exchange tasks, track contributions, and maintain trust.**

---

## Peer Discovery

### DHT-Based Discovery (No Central Coordinator)

Nexus Mesh uses a Distributed Hash Table for peer discovery. No central coordinator is required. Nodes find each other directly.

```
Node joins mesh:
  1. Generate or load hardware-bound identity (via ONS identity layer)
  2. Bootstrap from known seed nodes (configurable list, or LAN broadcast)
  3. Announce capability profile to DHT:
     - Node ID (hardware-bound, cryptographic)
     - Available models and their capability tags
     - Current availability status (IDLE / ACTIVE / OFFLINE)
     - Mesh mode participation (public / trusted / both)
  4. Begin listening for task assignments from coordinator
```

### LAN vs WAN Discovery

```yaml
mesh:
  discovery:
    lan_broadcast: true        # Announce on LAN first (lower latency, trusted)
    wan_dht: true              # Join public DHT for wider mesh
    seed_nodes:                # Known bootstrap nodes (optional)
      - nexus-node-1.example.com:4444
    lan_priority: true         # Prefer LAN nodes for task routing
```

LAN nodes are always preferred over WAN when available — lower latency, higher trust, no external bandwidth consumed.

---

## Task Descriptor

Every mesh task is a structured payload. The descriptor defines what is needed without revealing private context.

```json
{
  "task_id": "a3f7c9b2-...",
  "nonce": "k9x2p1m...",
  "timestamp": 1748800000,
  "version": "1.0",
  
  "requirements": {
    "capability": "reasoning",
    "model_class": "70b+",
    "min_vram_gb": 16,
    "max_latency_ms": 30000
  },
  
  "payload": {
    "prompt": "...",
    "system_prompt": null,
    "temperature": 0.7,
    "max_tokens": 2048,
    "output_format": "text"
  },
  
  "routing": {
    "priority": "standard",
    "redundancy": 1,
    "validation_level": "standard"
  },
  
  "origin": {
    "node_id": "[HASHED — not plaintext]",
    "session_token": "[OPAQUE — serving node cannot derive identity]"
  }
}
```

**What is NOT in the task descriptor**:
- Operator identity in plaintext
- Private memory or context
- Session history
- Why the task is being executed
- What system the result will feed into

The serving node sees the prompt and requirements only.

---

## Capability Tags

Task descriptors specify a capability requirement. Nodes advertise which capabilities they can serve.

| Tag | Description | Minimum Model |
|---|---|---|
| `reasoning` | Complex multi-step reasoning | 70B+ or R1-class |
| `code` | Code generation, review, debugging | Coder-specialized 14B+ |
| `code_quick` | Simple code tasks, snippets | Coder 7B+ |
| `general` | General Q&A, summarization | Any 7B+ |
| `triage` | Classification, routing decisions | 3B+ (fast) |
| `embed` | Vector embeddings for RAG | Embed model (nomic-embed, etc.) |
| `vision` | Multimodal (image+text) | Vision-capable model |
| `voice` | STT/TTS pipeline | Whisper + Piper or equiv |
| `local` | Private — serve from local node only, no mesh | N/A |

The `local` tag bypasses the mesh entirely. Tasks tagged `local` never leave the operator's node.

---

## Node Capability Profile

Each node advertises its capabilities when joining the DHT:

```json
{
  "node_id": "sha256:hardware_fingerprint",
  "version": "nexus-mesh/1.0",
  "capabilities": ["reasoning", "code", "general"],
  "models": [
    {
      "name": "deepseek-r1-671b-iq2_k",
      "capabilities": ["reasoning"],
      "vram_required_gb": 16,
      "backend": "ik_llama.cpp"
    },
    {
      "name": "qwen2.5-coder-32b-iq3_k",
      "capabilities": ["code"],
      "vram_required_gb": 14,
      "backend": "ik_llama.cpp"
    }
  ],
  "resources": {
    "gpu_vram_total_gb": 256,
    "system_ram_gb": 128,
    "cpu_cores": 128
  },
  "availability": "IDLE",
  "mesh_mode": "public",
  "ratio": 1.42,
  "reputation": 0.94
}
```

---

## Task Routing

The mesh coordinator (can be distributed — any sufficiently available node) matches task requirements to node capabilities:

```
Task arrives with requirements:
  capability: "reasoning"
  model_class: "70b+"
  min_vram_gb: 16
  max_latency_ms: 30000

Coordinator queries DHT:
  1. Filter: nodes with capability "reasoning" AND vram >= 16GB AND status == IDLE
  2. Sort by:
     a. LAN preference (local before WAN)
     b. Reputation score (higher first)
     c. Ratio score (better contributors first)
     d. Available VRAM (more headroom first)
  3. Select top N nodes (N = redundancy setting)
  4. Dispatch task to selected nodes
  5. Set timeout = max_latency_ms
  6. On timeout: re-route to next available node
```

---

## Ratio Enforcement

Ratio tracks how much a node contributes vs. consumes.

```
ratio = inference_tokens_served / inference_tokens_consumed

>= 1.0  → Full priority: immediate routing, no queue
0.5-0.99 → Standard priority: queued behind ratio >= 1.0 nodes
0.1-0.49 → Reduced priority: significant queue, throttled access
< 0.1   → Minimal access: emergency only (cloud fallback recommended)
0.0     → No mesh access until contribution begins
```

Ratio is tracked per hardware-bound identity. It cannot be gamed by rejoining with a new account — the hardware fingerprint persists.

**Grace period for new nodes**: New nodes receive ratio = 0.8 (standard access) for the first 24 hours. After that, actual ratio applies.

**Ratio decay**: Ratio decays toward 1.0 slowly over time (weekly: 10% decay toward 1.0). Prevents ratio hoarding from old contributions becoming a permanent free pass.

---

## Result Return and Validation

```
Serving node completes inference:
  1. Run output sanitization (credential scan, injection fingerprint scan)
  2. Sign result with hardware-bound key
  3. Return result + signature to coordinator
  4. Coordinator forwards to requesting node

Requesting node receives result:
  1. Verify signature (hardware-bound — confirms which node served it)
  2. If validation_level == standard: accept (sanitization already done)
  3. If validation_level == strict:
     - Wait for second result from second serving node
     - Diff results
     - If agree: accept
     - If diverge: flag, escalate to third node or return to local
  4. Log result hash + serving node ID (for reputation tracking)
  5. Update serving node's reputation score based on result quality (if evaluable)
```

---

## Reputation System

Reputation tracks the reliability and honesty of each node over time.

```
Initial reputation:   0.75 (new node, trusted but unproven)
Range:                0.0 (untrusted) to 1.0 (fully trusted)

Positive signals:
  +0.01 per validated correct result (strict mode comparison)
  +0.005 per clean result (standard mode, no sanitization flags)
  +0.002 per task completed within latency requirement

Negative signals:
  -0.10 for sanitization flag in output
  -0.15 for result divergence (strict mode, node was the outlier)
  -0.20 for task timeout (node accepted task, failed to complete)
  -0.50 for detected injection attempt in payload served
  -1.00 (instant revocation) for confirmed malicious behavior

Decay: -0.02 per week of inactivity (prevents stale high-reputation dead nodes)
```

Nodes below `min_node_reputation` threshold (operator-configured, default 0.6) are ignored in routing queries.

---

## Trust Revocation

When a node is identified as malicious or compromised:

1. Any mesh member can submit a revocation claim (signed with their hardware key, with evidence)
2. Revocation is propagated via ONS distributed revocation protocol
3. Receiving nodes cache the revocation locally
4. Revoked node ID is excluded from all future routing queries
5. Revocation does not require central authority — propagates peer-to-peer

Revocation by a single node is a "soft flag" (reputation penalty, increased scrutiny). Revocation with evidence corroborated by multiple nodes triggers hard exclusion.

---

## Scheduling and Availability Windows

Operators define when their node is available to the mesh:

```yaml
mesh:
  schedule:
    enabled: true
    mode: scheduled    # always | scheduled | never
    
    windows:
      # Weeknights: available midnight to 7am
      - days: [mon, tue, wed, thu, fri]
        start: "23:00"
        end: "07:00"
      
      # Full weekends
      - days: [sat, sun]
        start: "00:00"
        end: "23:59"
    
    # Always refuse mesh regardless of schedule if these processes are running
    active_use_processes:
      - firefox
      - chrome
      - steam
      - any_llm_query   # If owner is running local inference, mesh suspended
    
    # Override: always serve high-reputation trusted peers even outside schedule
    trusted_peers_bypass_schedule: false
```

When a node goes offline (outside schedule, or owner preemption), the mesh coordinator marks it as UNAVAILABLE. Tasks in-flight are re-routed. Tasks queued for that node are re-dispatched.

---

## Mode B: Trusted Peer Protocol

For trusted sandbox access between known operators:

```
Operator A invites Operator B to sandbox "project-x":
  1. A generates a scoped invite token (signed, time-limited, specifies sandbox + permissions)
  2. A sends token to B via out-of-band channel (Mattermost DM, encrypted message)
  3. B presents token + hardware identity to A's node
  4. A's node verifies: token valid, not expired, identity matches expected
  5. B receives scoped access to sandbox "project-x"

Permissions in invite token:
  - read_context: bool (can read shared sandbox context)
  - write_context: bool (can contribute to shared sandbox)
  - use_providers: list of provider IDs (e.g., ["deepseek_local"])
  - expiry: timestamp
  - revocable: true (A can revoke at any time)
```

Provider sharing means Operator B can route inference tasks to Operator A's local models — but only through the sandbox, not the private zone.

---

## Bandwidth and Network Constraints

Not all operators have high-speed connections. The protocol respects this:

```yaml
mesh:
  network:
    upload_mbps: 10          # Advertised to coordinator — used for task routing
    download_mbps: 10
    per_task_max_kb: 256     # Reject tasks with payload > this size
    result_max_kb: 512       # Truncate or reject results exceeding this
```

The coordinator uses advertised bandwidth when routing tasks — large tasks won't be routed to constrained nodes. A node with 1 Mbps upload will only receive small, fast inference tasks proportional to its bandwidth.

---

## Wire Protocol Summary

```
Node joins DHT        → ANNOUNCE(capability_profile)
Node receives task    → TASK(descriptor) → RESULT(output, signature)
Node goes offline     → UNAVAILABLE(node_id, reason)
Node revokes peer     → REVOKE(target_id, evidence, signature)
Mode B invite         → INVITE(sandbox_id, scoped_token, expiry)
Mode B access         → ACCESS(sandbox_id, token, hardware_identity)
Heartbeat (60s)       → HEARTBEAT(node_id, availability, ratio)
```

All messages are signed with the node's hardware-bound key. Unsigned or unverifiable messages are dropped.

---

## Related Documents

- [01-overview.md](01-overview.md) — Concept, analogies, two modes
- [02-architecture.md](02-architecture.md) — Architecture, resource governance
- [03-security.md](03-security.md) — Threat model, isolation, sandbox
