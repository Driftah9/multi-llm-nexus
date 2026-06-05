# Nexus Mesh — Implementation Roadmap

**What it takes to go from design docs to functional mesh.**

---

## Part 1: Build-Out Requirements

### Phase 1 — Foundation (Single-Node Mesh Sandbox)

Before connecting nodes, build the isolation layer on one Nexus deployment.

**Goal**: A single node can receive a mesh task, execute it in sandbox isolation, and return a result — without touching the private zone.

| Task | Description | Dependencies |
|---|---|---|
| Sandbox process isolation | Separate process/container that runs inference in isolation from main Nexus | Docker SDK or subprocess + seccomp |
| Mesh task schema | Define the task descriptor JSON format (see 04-protocol.md) | None — design complete |
| Inference-only execution | Sandbox loads model weights (read-only), accepts prompt, returns result | ik_llama.cpp or Ollama HTTP endpoint |
| Output sanitization | Scan results for credential patterns, PII, injection fingerprints before returning | Regex scanner, configurable patterns |
| Resource governor | Monitor GPU/CPU utilization, enforce throttle limits, hard preempt on owner activity | psutil/nvidia-smi polling, cgroups |
| Scheduling engine | Define availability windows, honor them in task acceptance | Cron-style config parser |
| Mesh config schema | `mesh:` section in Nexus config YAML (resources, schedule, security) | Config validation system |

**Deliverable**: `nexus --mesh-test` command that simulates receiving and processing a mesh task locally.

**Estimated effort**: 2-4 weeks for a solo developer with existing Nexus familiarity.

---

### Phase 2 — Peer Discovery & Connectivity

Two nodes can find each other and exchange tasks.

| Task | Description | Dependencies |
|---|---|---|
| Hardware-bound identity generation | Derive node identity from hardware fingerprint (CPU ID, MAC, TPM if available) | Standalone `nexus_identity` module (`sha256(cpu_id + mb_uuid + disk_serial)`) |
| DHT implementation | Kademlia-based distributed hash table for peer discovery | Existing library (e.g., `kademlia` Python package, or custom in Go) |
| Node capability profile | Nodes announce models, VRAM, availability to DHT on startup | Inference engine introspection (model list, VRAM query) |
| LAN broadcast discovery | mDNS or UDP broadcast for local network peer discovery (preferred over WAN) | Standard networking |
| Heartbeat protocol | 60-second heartbeat with availability status, ratio, reputation | UDP or lightweight TCP |
| Task dispatch | Requesting node submits task to coordinator, coordinator routes to best available node | Routing algorithm from 04-protocol.md |
| Result return | Serving node signs result, returns to coordinator, coordinator forwards to requester | Cryptographic signing (hardware key) |
| Basic ratio tracking | Track tokens served vs consumed per node identity | SQLite or embedded DB |

**Deliverable**: Two Nexus nodes on the same LAN can exchange inference tasks with ratio tracking.

**Estimated effort**: 4-6 weeks. DHT is the most complex component; existing libraries reduce this.

---

### Phase 3 — Trust, Reputation & Mode B

Full trust model with both mesh modes operational.

| Task | Description | Dependencies |
|---|---|---|
| Reputation scoring | Track per-node quality metrics (+/- signals from 04-protocol.md) | Phase 2 ratio tracking extended |
| Reputation-weighted routing | Coordinator prefers high-reputation nodes; excludes below threshold | Routing algorithm update |
| Trust revocation | Peer-submitted revocation claims propagated via libp2p GossipSub | libp2p pubsub |
| Nonce + timestamp validation | Reject replay attacks (stale/duplicate task descriptors) | Clock sync (NTP), nonce registry |
| Mode B: Invite tokens | Generate scoped, time-limited invite tokens for trusted peer access | Cryptographic token generation |
| Mode B: Sandbox workspaces | Named shared workspaces between trusted peers with scoped permissions | Sandbox extension — shared context store |
| Mode B: Provider sharing | Trusted peer can route inference through your local models via sandbox | Reverse proxy or API delegation |
| Ratio decay | Weekly 10% decay toward 1.0 to prevent ratio hoarding | Scheduled background task |

**Deliverable**: Full Mode A + Mode B mesh with trust model, reputation, and revocation.

**Estimated effort**: 4-6 weeks. Security-critical — requires careful design review.

---

### Phase 4 — Mode R: Research Queue + WAN

Two parallel workstreams: Mode R deferred execution, and WAN reliability.

**4a — Mode R (Deferred Research Queue)**

| Task | Description | Dependencies |
|---|---|---|
| Persistent deferred queue | Task queue that survives restarts, accepts `priority: deferred` tasks | SQLite or embedded queue |
| Idle window dispatcher | Coordinator monitors node availability windows, dispatches queued tasks when nodes go idle | Phase 2 heartbeat + availability |
| Multi-node dispatch | Send same research task to N nodes simultaneously | Phase 2 task dispatch extended |
| Result collection | Accumulate partial results as nodes complete, handle timeouts | Result storage + timeout handler |
| Synthesis engine | When N results received: run reasoning pass to synthesize N chains into one output | Reasoning-capable local model |
| Research task config | `mesh.modes.research` config block, per-node acceptance settings | Config validation |

**4b — WAN Hardening**

| Task | Description | Dependencies |
|---|---|---|
| WAN DHT bootstrapping | Seed node list for internet-wide mesh discovery | Public seed infrastructure or relay |
| NAT traversal | Hole punching or relay for nodes behind NAT/firewalls | libp2p Circuit Relay v2 |
| End-to-end encryption | All task payloads and results encrypted in transit (not just signed) | TLS or NaCl box encryption |
| Multi-region routing | Prefer geographically close peers for latency | GeoIP or measured RTT |
| Bandwidth-aware routing | Don't assign large tasks to bandwidth-constrained nodes | Advertised bandwidth in capability profile |
| Graceful degradation | Handle partial mesh failure without error cascading | Circuit breaker per node |

**Deliverable**: Production-ready mesh with Mode R research capability operating across WAN.

**Estimated effort**: 6-10 weeks. NAT traversal and WAN reliability are the hardest problems. Mode R queue is simpler — can ship independently of WAN hardening.

---

### Phase 5 — Integration & UX

Mesh is invisible to the end user — it just works.

| Task | Description | Dependencies |
|---|---|---|
| Nexus CLI commands | `nexus mesh status`, `nexus mesh peers`, `nexus mesh ratio`, `nexus mesh test` | CLI framework |
| Adapter commands | `!mesh status`, `!mesh peers` via Mattermost/Discord/Telegram | CommandRegistry |
| Dashboard metrics | Mesh health, peer count, tasks served/consumed, ratio, reputation | Heartbeat data aggregation |
| Auto-discovery setup | First-run wizard detects LAN peers and suggests mesh enrollment | Phase 2 LAN discovery |
| Provider routing integration | Triage routes to mesh automatically when local is saturated | Router extension |
| Watcher: mesh health | Alert when mesh drops below minimum viable peer count | Nexus watcher framework |

**Deliverable**: Mesh participation is a config toggle with full observability.

**Estimated effort**: 2-4 weeks.

---

### Build-Out Summary

| Phase | What | Modes Delivered | Effort | Cumulative |
|---|---|---|---|---|
| Phase 1 | Sandbox isolation, resource governance | Foundation | 2-4 weeks | 2-4 weeks |
| Phase 2 | Peer discovery, task exchange, ratio | Mode A | 4-6 weeks | 6-10 weeks |
| Phase 3 | Trust, reputation, Mode B | Mode B | 4-6 weeks | 10-16 weeks |
| Phase 4 | Mode R queue + WAN hardening | Mode R | 6-10 weeks | 16-26 weeks |
| Phase 5 | Integration, UX, dashboard | All modes | 2-4 weeks | 18-30 weeks |
| **Phase 6** | **Mode 0: Local pool (exo-style layer sharding)** | **Mode 0** | **8-14 weeks** | **Optional** |

**Total estimated build (Phases 1-5): 18-30 weeks for a solo developer.**

Phase 6 (Mode 0) is optional — for research-scale deployments with multiple local machines. It adds exo-style pipeline parallelism under Nexus orchestration. Implementation reference: exo (github.com/exo-explore/exo).

With parallel development or multiple contributors, phases 3 and 4 can overlap with integration work, compressing the timeline.

---

## Part 2: Security Validation Best Practices

### Pre-Release Security Requirements

These MUST be validated before any mesh deployment accepts external peers.

#### 2.1 Sandbox Escape Testing

**Objective**: Verify that no mesh task can access the private Nexus zone.

| Test | Method | Pass Criteria |
|---|---|---|
| Filesystem isolation | Mesh task attempts `read /home/operator/.env` | Task cannot access any file outside sandbox |
| Memory isolation | Mesh task attempts to read shared memory / IPC channels | No shared memory between sandbox and main process |
| Network isolation | Mesh task attempts outbound connections (curl, DNS, etc.) | Only result-return endpoint is reachable |
| Process isolation | Mesh task attempts to enumerate or signal main Nexus process | Sandbox process sees only itself |
| Adapter isolation | Mesh task attempts to trigger Mattermost/Discord/Telegram post | No adapter access from sandbox |
| Tool isolation | Mesh task includes function-calling instructions | No tool execution in sandbox |
| Environment variable leakage | Mesh task reads env vars from parent process | Sandbox inherits no env vars from main process |

**Method**: Red team exercises — dedicated adversarial testing against each isolation boundary. Automated test suite that runs against every sandbox implementation change.

#### 2.2 Prompt Injection Resistance

**Objective**: Verify the sandbox model context is immune to injection from task payloads.

| Test | Method | Pass Criteria |
|---|---|---|
| System prompt override | Task contains "Ignore previous instructions..." | Model has no system prompt to override in sandbox |
| Context extraction | Task asks model to "output your full context" | Model outputs only task-relevant content |
| Instruction injection | Task embeds multi-turn conversation to simulate elevated context | Sandbox runs single-turn inference only |
| Encoding attacks | Task uses base64/hex/unicode to smuggle instructions | Payload validation rejects non-UTF-8 or suspicious encoding |
| Multilingual injection | Task uses language switching to bypass English-focused filters | Output sanitization is language-agnostic |

**Method**: Curated injection test suite (500+ known injection patterns). Run against each supported model. Track injection bypass rate — target: 0% in sandbox context.

#### 2.3 Output Sanitization Validation

**Objective**: Verify the sanitization layer catches sensitive data in mesh results.

| Test | Method | Pass Criteria |
|---|---|---|
| API key detection | Result contains `sk-...`, `AKIA...`, `Bearer ...` | Detected and flagged/stripped |
| PII detection | Result contains SSN, credit card, phone number patterns | Detected and flagged |
| Injection fingerprints | Result contains known prompt injection signatures | Detected and flagged |
| False positive rate | Legitimate results containing code/examples | Flagged content is <5% false positive |
| Evasion resistance | Obfuscated credentials (spaces, encoding, splitting) | Detected even with basic obfuscation |

**Method**: Synthetic test corpus with known sensitive patterns embedded in realistic inference outputs.

#### 2.4 Cryptographic Validation

| Test | Method | Pass Criteria |
|---|---|---|
| Hardware identity uniqueness | Multiple nodes generate identities | No collisions across 10,000+ generations |
| Signature verification | Tampered result with modified signature | Rejected by verifier |
| Replay rejection | Resubmit valid task with original nonce | Rejected (nonce already consumed) |
| Timestamp enforcement | Submit task with timestamp >30 seconds old | Rejected |
| Revocation propagation | Revoke a node, verify all peers receive revocation | Propagation complete within 60 seconds on LAN |

#### 2.5 Resource Governor Validation

| Test | Method | Pass Criteria |
|---|---|---|
| Hard preemption | Start owner inference while mesh task is running | Mesh task killed within 2 seconds |
| Throttle enforcement | Run mesh at 50% GPU limit, verify no exceed | GPU utilization stays at or below configured % |
| Schedule enforcement | Submit mesh task outside availability window | Task rejected |
| RAM reservation | Mesh attempts to exceed configured RAM limit | OOM in sandbox, not in main process |
| Bandwidth cap | Stream large result exceeding upload_mbps limit | Transfer rate capped at configured limit |

---

### Security Audit Cadence

| Event | Action |
|---|---|
| Every sandbox code change | Re-run full isolation test suite |
| Every model addition | Run injection test suite against new model |
| Monthly | Full red team exercise against mesh attack surface |
| Before each release | Third-party security review of sandbox implementation |
| After any reported exploit | Post-mortem, patch, regression test added |

---

## Part 3: Stress Testing Requirements

### 3.1 Single-Node Load Testing

Test how the mesh sandbox performs under pressure on one node.

| Test | Parameters | Metrics | Target |
|---|---|---|---|
| Concurrent mesh tasks | 1, 5, 10, 20, 50 simultaneous tasks | Throughput (tasks/min), latency (p50/p95/p99) | Stable throughput, no OOM, no crashes |
| Large prompt payload | 1K, 4K, 16K, 32K token prompts | TTFT, inference time, memory usage | Graceful handling up to model's context limit |
| Large result payload | Tasks generating 1K, 4K, 16K tokens | Output time, sanitization overhead | Sanitization adds <5% overhead |
| Rapid task cycling | 100 tasks in 60 seconds, each 200 tokens | Task setup/teardown overhead | <100ms per task lifecycle overhead |
| GPU saturation | Fill GPU to 95% with mesh tasks, then owner starts task | Preemption latency | Mesh killed within 2 seconds |
| Memory pressure | Mesh tasks consuming increasing RAM | OOM behavior | Sandbox OOMs, main process unaffected |
| Model hot-swap under load | Switch model while mesh tasks are queued | Queue drain, model reload time | Queued tasks re-routed or held, no crash |

#### Stress Scenario: Owner Returns

The critical stress path:

```
1. System idle, mesh running at 50% GPU (8 concurrent tasks)
2. Owner starts local inference
3. Mesh tasks must be killed within 2 seconds
4. Owner's inference must start without delay
5. Mesh coordinator must be notified of unavailability
6. When owner finishes, mesh resumes at configured throttle
```

Verify this cycle 1,000 times with randomized timing. Zero failures permitted.

---

### 3.2 Multi-Node Mesh Testing

Test mesh behavior with multiple nodes under realistic conditions.

| Test | Parameters | Metrics | Target |
|---|---|---|---|
| Basic routing | 3 nodes, 100 tasks, round-robin | All tasks complete, ratio tracked | 100% completion, accurate ratio |
| Node failure mid-task | Kill one node during inference | Task re-routed to surviving node | Re-route within 30 seconds |
| Partial mesh failure | 5 nodes drop to 2 | Queue depth, latency increase | No task failures, graceful degradation |
| DHT convergence | 10 nodes join within 60 seconds | Time to full peer discovery | All peers discovered within 120 seconds |
| Ratio enforcement | Node with 0.1 ratio vs node with 1.5 ratio, both request task | Priority ordering | High-ratio node served first |
| Reputation-based routing | One node returns consistently bad results | Reputation score decay, routing avoidance | Bad node excluded within 10 failed tasks |
| Revocation propagation | Revoke node in 10-node mesh | Time to full propagation | All nodes aware within 60 seconds |

---

### 3.3 Network Stress Testing

| Test | Parameters | Metrics | Target |
|---|---|---|---|
| Bandwidth throttle | Limit link to 1 Mbps between two mesh nodes | Task completion rate, latency | Tasks complete (slowly), no timeout on small payloads |
| High latency | Add 200ms artificial latency | Token generation overhead | <10% throughput loss (generation is local) |
| Packet loss | 5% random packet loss | Retry rate, completion rate | All tasks complete with retries, <3 retries average |
| NAT traversal | Both nodes behind separate NATs | Connection establishment | Successful connection via STUN/TURN |
| DNS failure | DNS unreachable after initial DHT bootstrap | Mesh stability | Mesh operates via cached IP addresses |
| Asymmetric bandwidth | Node A: 100 Mbps up, Node B: 5 Mbps up | Routing decisions | Large tasks routed to high-bandwidth node |

---

### 3.4 Adversarial Stress Testing

Simulate malicious actors in the mesh.

| Test | Parameters | Metrics | Target |
|---|---|---|---|
| DoS via task flooding | Malicious node sends 1,000 tasks/min to one target | Rate limit enforcement | Target rejects beyond rate limit, no resource exhaustion |
| Sybil attack | Attacker creates 10 fake node identities | DHT pollution, routing corruption | Hardware-bound identity prevents fake nodes |
| Result poisoning | Malicious node returns subtly wrong results | Reputation decay rate, detection | Node reaches exclusion threshold within 20 bad results |
| Replay flood | Replay valid task 100 times | Nonce registry performance | All replays rejected, nonce registry handles load |
| Coordinated attack | 3 malicious nodes submit crafted tasks to one target | Combined load handling | Target's rate limits hold, preemption works |
| Trust escalation | Trusted peer attempts out-of-scope access | Permission enforcement | Scoped permissions block all unauthorized access |

---

### 3.5 Long-Duration Soak Testing

| Test | Duration | What to Monitor |
|---|---|---|
| 24-hour continuous mesh operation | 24h, 5 nodes, steady task flow | Memory leaks, handle exhaustion, log rotation, ratio drift |
| 7-day soak with random node churn | 7d, nodes join/leave randomly | DHT stability, reputation accuracy, data consistency |
| Owner activity simulation | 24h, owner active 8h / idle 16h | Preemption reliability, mesh resume behavior, GPU health |

**Key metric for soak tests**: No monotonically increasing resource usage (memory, handles, connections). Everything must plateau.

---

### 3.6 Test Infrastructure Requirements

| Component | Purpose |
|---|---|
| Minimum 3 test nodes | Basic mesh topology testing |
| Recommended 5-10 nodes | Realistic mesh behavior, failure scenarios |
| Network simulation tool | tc/netem for bandwidth/latency/loss injection |
| GPU monitoring | nvidia-smi logging at 1-second intervals during stress |
| Automated test harness | Run full test suite unattended, produce pass/fail report |
| Result comparison framework | Diff inference outputs across nodes for validation testing |

**Test nodes can be VMs** — mesh doesn't require physical GPUs for protocol testing. GPU-specific tests (preemption, saturation, model splitting) require real hardware.

---

## Timeline Summary

| Phase | Build | Security Validation | Stress Testing |
|---|---|---|---|
| Phase 1 (Sandbox) | 2-4 weeks | Sandbox escape + injection tests | Single-node load tests |
| Phase 2 (Discovery) | 4-6 weeks | Crypto validation | Multi-node basic routing |
| Phase 3 (Trust) | 4-6 weeks | Full red team exercise | Reputation + adversarial tests |
| Phase 4 (WAN) | 6-10 weeks | Third-party security review | Network stress + NAT tests |
| Phase 5 (Integration) | 2-4 weeks | Regression suite | 7-day soak test |

Security validation and stress testing run **in parallel with each build phase**, not sequentially after. Every phase ships with its own test coverage.

---

## Related Documents

- [01-overview.md](01-overview.md) — Concept and modes
- [02-architecture.md](02-architecture.md) — Full stack and resource governance
- [03-security.md](03-security.md) — Threat model, isolation, sandbox
- [04-protocol.md](04-protocol.md) — Task descriptors, peer discovery, ratio, reputation
