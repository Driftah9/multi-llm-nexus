# The Citadel Tier

**The point where local AI stops being a compromise and becomes a fortress.**

---

## What the Citadel Tier Is

The Citadel is the hardware tier where an Operator achieves full local sovereignty over their AI stack. Every Nexus tier — triage, standard, deep, and specialist — runs locally on hardware the Operator owns. Cloud becomes optional fallback, not a dependency.

If the Dreadnought tier is a workstation that can run AI, the Citadel is an AI server that lives in your home or office. Consumer-grade GPUs can't compete with what the Citadel makes possible — not because of any single card's performance, but because of what happens when 4-8 server-grade GPUs operate as a coordinated system.

---

## How It Fits the Nexus Hardware Progression

Nexus runs on anything from a laptop to a datacenter. The hardware tiers are not gatekeeping — they're milestones along a sovereignty gradient.

| Tier | What It Is | Cloud Dependency | Local Capability |
|---|---|---|---|
| **Floor** | CPU + small GPU or cloud-only | All tiers cloud | Try Nexus, evaluate fit |
| **Consumer** | Single mid-range GPU (RTX 3060, 4070, etc.) | Deep tier cloud, standard local | Daily work local |
| **Dreadnought** | Dual consumer/prosumer GPUs (RTX 3090, V100 on adapter, etc.) | Deep tier often cloud | Standard fully local, deep occasionally local |
| **Citadel** | 4-8 server-grade GPUs with NVLink fabric | Cloud as fallback only | All tiers local at full quality |

Each tier is a valid stopping point. The Operator decides how far to go. Moving between tiers is a hardware change and a config file update — no Nexus code changes.

---

## What Makes Something a Citadel

The Citadel tier is not defined by a specific server model, GPU brand, or vendor. It's defined by four capabilities that consumer hardware can't deliver:

### 1. GPU-to-GPU High-Bandwidth Interconnect

NVLink, NVSwitch, or equivalent. Not PCIe. The interconnect bandwidth is what makes tensor parallelism practical — sharding a 70B model across 4 GPUs means every token generation triggers an all-reduce synchronization across all cards. Over PCIe 3.0 (~32 GB/s), that synchronization becomes a bottleneck. Over NVLink 2.0 (~300 GB/s aggregate per GPU), it's fast enough that the model runs as if it were on one large card.

Without high-bandwidth interconnect, you can still run multi-GPU workloads, but you're limited to independent models per GPU (no pooling). The Dreadnought tier works this way — two separate GPUs, each running its own model, with no GPU-to-GPU coordination.

### 2. Pool Density

Four or more GPUs means you can dedicate groups to different roles simultaneously:

- A pool of lightweight independent workers (triage, standard, specialist models that stay always-resident)
- A pool of larger cards tensor-parallel'd together for the deep tier (70B+ models at high quantization)
- Optionally, a redundant deep pool so one can serve while the other is already busy

Consumer builds max out at 2 cards in a workstation. Server platforms support 4-8 (and beyond, but 8 is the practical ceiling for this tier).

### 3. VRAM Scale

The deep tier's capability is a function of total poolable VRAM. Consumer workstations with 2 GPUs top out around 48GB (dual 24GB cards) — enough for 70B at Q3, which works but loses quality.

Citadel-class hardware reaches:

| Configuration | Usable VRAM | What It Runs |
|---|---|---|
| 4× 16GB pooled | ~60 GB | 70B Q4-Q5 |
| 2× 32GB pooled | ~60 GB | 70B Q5 |
| 4× 32GB pooled | ~120 GB | 70B Q8 (full quality), 100B-class Q4 |
| 8× 32GB pooled | ~240 GB | 70B FP16, 200B-class Q4 |
| 4× 40GB pooled (A100) | ~150 GB | 70B FP16 with KV headroom |
| 4× 80GB pooled (A100 80GB) | ~300 GB | 405B Q4, DeepSeek R1 Q2 |

The jump from Dreadnought (48GB, two independent cards) to Citadel entry (64-128GB, NVLink-pooled) is where the deep tier stops being a compromise.

### 4. Always-On Specialist Density

A Dreadnought runs 1-2 models concurrently. A Citadel runs 4-7+. This means:

- Triage stays resident (sub-second classification, never evicted)
- Standard stays resident (instant response for common requests)
- 2-3 specialists stay resident (code, vision, research — loaded and waiting)
- Deep pool is available on demand without evicting any of the above

On consumer hardware, loading a deep model evicts the specialist that was in VRAM. On a Citadel, the deep pool is separate hardware — specialists never get touched.

---

## What the Citadel Is Not

**Not a specific vendor or product.** Any server platform that meets the four criteria above qualifies. This includes used enterprise hardware from the secondary market, purpose-built AI servers, and homelab builds using server-grade components. See [server-grade-builds.md](server-grade-builds.md) for qualifying hardware examples.

**Not the ceiling.** Above the Citadel sits enterprise-grade hardware (DGX-class systems, NVSwitch full-mesh topologies, A100/H100/B200 at scale). That tier exists but is outside Nexus's intended scope — it requires dedicated infrastructure teams and budgets that don't fit the self-hosted operator model.

**Not required.** The Floor, Consumer, and Dreadnought tiers are all valid deployments. Many Operators will never need a Citadel. If cloud deep-tier fallback is acceptable for your workload, there's no reason to build one.

---

## How Nexus Uses a Citadel

From Nexus's perspective, a Citadel deployment is a set of provider endpoints organized into pools. The configuration lives in two files:

**`providers.yaml`** — maps each GPU worker or pool to a Nexus provider entry with its model, endpoint, and tier.

**`pools.yaml`** — declares the pool topology (which providers share which GPUs, which pools are independent vs. tensor-parallel, health endpoints for load-aware routing).

See [gpu-pool-topology.md](gpu-pool-topology.md) for the complete configuration guide, operator profiles, and inference server launch patterns.

The key architectural point: **moving from Dreadnought to Citadel is a config change, not a code change.** Nexus's tier routing, provider chain failover, and pool-aware selection all work the same way regardless of whether the provider is one GPU, eight GPUs, or a cloud API. The operator changes their hardware, updates two YAML files, and Nexus adapts.

---

## Sovereignty

The Citadel is where sovereignty becomes real. Below this tier, the deep tier depends on cloud providers — which means your hardest questions, your most sensitive data, and your most complex reasoning all flow through someone else's infrastructure.

At the Citadel tier:
- Every inference runs on hardware you own
- No API key can be revoked
- No pricing tier can change under you
- No terms of service can restrict what you ask
- No outage you don't control can take your agent offline

Cloud providers remain available as fallback for frontier-class models (405B+) or as a cost optimization when local compute is saturated. But they're optional. The Operator chooses when and whether to use them.

This is what the Citadel buys you that no amount of consumer hardware can: **the certainty that your AI platform answers to you and no one else.**

---

## Cost Reality

Citadel-tier hardware is not cheap, but it's achievable on a self-hosted budget because the secondary market makes enterprise hardware accessible at a fraction of original cost.

| Entry Point | Typical Cost | What You Get |
|---|---|---|
| Used 4-GPU server (V100 SXM2 16GB) | $3,500-4,500 | Full specialist farm, cloud deep fallback |
| Add 2× 32GB GPUs to existing server | +$600-1,000 | Deep tier goes local (70B Q5) |
| Used 8-GPU server (V100 SXM2 32GB) | $6,500-10,500 | Full sovereignty, 70B Q8+ local |
| A100 40GB 4-GPU server | $8,000-15,000 | Next-gen Citadel, BF16 support, larger models |
| A100 80GB 4-GPU server | $15,000-30,000 | 405B-class local at quantized quality |

These are used market prices. New hardware costs 5-10× more. The Citadel tier is a secondary market play — the same servers that cost $100K+ when deployed in datacenters three years ago are now available to individual operators.

Prices shift. Always validate current market pricing before committing. See [server-grade-builds.md](server-grade-builds.md) for specific platforms and market availability.

---

## Next Steps

1. **Decide if you need a Citadel.** If cloud deep-tier fallback is fine for your workload, stay at Dreadnought. No shame in that.
2. **Choose your hardware class.** See [server-grade-builds.md](server-grade-builds.md) for platforms that qualify.
3. **Plan your pool topology.** See [gpu-pool-topology.md](gpu-pool-topology.md) for how to organize GPUs into independent workers and pooled deep tiers.
4. **Deploy incrementally.** Start with a 4-GPU configuration and expand as workload demands. The Citadel scales with your budget.
