# Server-Grade Builds for Nexus

**Hardware that qualifies for the Citadel tier.**

This document lists the categories of server hardware that meet Citadel requirements: 4+ GPUs, high-bandwidth GPU-to-GPU interconnect (NVLink or equivalent), and the physical infrastructure to keep them running.

This is not a buying guide — it's a reference for understanding what's available on the secondary market and what each platform class offers. Prices shift quarterly. Always validate current market availability before committing.

---

## What Qualifies

A server qualifies for the Citadel tier if it has:

1. **4 or more GPU sockets** (SXM2, SXM4, or equivalent form factor — not PCIe-only)
2. **GPU-to-GPU high-bandwidth interconnect** (NVLink 2.0+ or NVSwitch)
3. **Adequate power delivery** (typically 2000W+ for 4 GPUs, 4000W+ for 8 GPUs)
4. **Server-grade cooling** (sustained full-load thermal management, not workstation fans)

PCIe-only multi-GPU servers exist (4× RTX 3090 in a tower, for example) and can run multiple independent Nexus workers. But without NVLink, they can't pool GPUs for tensor parallelism — which limits the deep tier to whatever fits on a single card. Those systems belong at the Dreadnought tier, not Citadel.

---

## GPU Generations on the Secondary Market

### Volta — V100 SXM2 (2017-era, widely available used)

The most accessible entry to Citadel-tier hardware. V100 SXM2 is the GPU that shipped in every major vendor's AI server from 2018-2020. Corporate decomissions have flooded the secondary market.

| Spec | V100 SXM2 16GB | V100 SXM2 32GB |
|---|---|---|
| VRAM | 16 GB HBM2 | 32 GB HBM2 |
| Memory bandwidth | 900 GB/s | 900 GB/s |
| NVLink version | 2.0 (6 lanes, 300 GB/s aggregate) | 2.0 (6 lanes, 300 GB/s aggregate) |
| FP16 Tensor | 125 TFLOPS | 125 TFLOPS |
| BF16 | No (Volta limitation) | No |
| TDP | 300W | 300W |
| Used price (2026) | $100-200/card | $300-500/card |

**Strengths:** Cheap, widely available, proven for inference, ECC HBM2.
**Limitations:** No BF16 (fine-tuning needs adaptation), Volta driver support will eventually sunset, FP16-only Tensor Cores.

### Ampere — A100 SXM4 (2020-era, increasingly available used)

The next generation up. A100 addresses every V100 limitation while increasing VRAM capacity significantly. Starting to appear on the secondary market as cloud providers rotate out first-generation A100 deployments.

| Spec | A100 SXM4 40GB | A100 SXM4 80GB |
|---|---|---|
| VRAM | 40 GB HBM2e | 80 GB HBM2e |
| Memory bandwidth | 1,555 GB/s | 2,039 GB/s |
| NVLink version | 3.0 (12 lanes, 600 GB/s aggregate) | 3.0 (12 lanes, 600 GB/s aggregate) |
| FP16 Tensor | 312 TFLOPS | 312 TFLOPS |
| BF16 | Yes | Yes |
| TF32 | Yes | Yes |
| TDP | 400W | 400W |
| Used price (2026) | $2,000-4,000/card | $4,000-8,000/card |

**Strengths:** BF16/TF32 support, higher memory bandwidth, larger VRAM, double the NVLink bandwidth, longer driver support runway.
**Limitations:** Higher power draw (400W vs 300W), used prices still significantly above V100, requires SXM4 baseboard (not compatible with SXM2 sockets).

### Hopper — H100 SXM5 (2023-era, rare on secondary market)

The current datacenter flagship. Extremely rare on the used market in 2026. Mentioned for completeness — if you encounter one at a reasonable price, it's the best inference GPU available. But don't plan around finding one.

| Spec | H100 SXM5 80GB |
|---|---|
| VRAM | 80 GB HBM3 |
| Memory bandwidth | 3,350 GB/s |
| NVLink version | 4.0 (18 lanes, 900 GB/s) |
| FP8 Tensor | Yes |
| TDP | 700W |
| Used price (2026) | $8,000-15,000/card (when available) |

---

## Server Platform Categories

### 4U Class — 4 to 8 GPU (Primary Citadel Platform)

The 4U rackmount form factor is the sweet spot for Citadel builds. These servers were designed from the ground up for 4-8 GPU workloads with full NVLink topology, adequate power delivery, and cooling for sustained load.

**What to look for:**
- 8× SXM2 or SXM4 sockets on a GPU baseboard (not all need to be populated)
- NVLink hybrid cube mesh topology (each GPU connects to 4 others)
- Dual CPU sockets (for memory bandwidth to feed the GPUs)
- 4× high-wattage PSUs (2200W each for V100, 3000W each for A100)
- 16+ front drive bays and onboard M.2 NVMe

**Representative vendors** (SXM2 era):
- Supermicro (SYS-4029GP series, X11DGO-T baseboard)
- HPE (Apollo 6500 Gen10, XL270d accelerator tray)
- Dell (DSS 8440)
- Gigabyte (G481-S80)
- Inspur (NF5468M5)

**Representative vendors** (SXM4 / A100 era):
- Supermicro (SYS-420GP series)
- HPE (Apollo 6500 Gen10 Plus)
- Dell (PowerEdge XE8545)
- Lenovo (ThinkSystem SR670 V2)

These are examples, not endorsements. Any server with the right socket count, NVLink topology, and power delivery qualifies.

### Reference Platforms (Nvidia-Branded)

Nvidia's own server products use the same GPU baseboards that OEM vendors license. They show up on the secondary market and are worth understanding:

**DGX-1V** — 3U, 8× V100 SXM2, hybrid cube mesh NVLink. The reference design that every OEM cloned. Often available used with full documentation. Note: uses Tesla-branded V100s and custom NVMe, but functionally identical to OEM equivalents.

**DGX-2** — 10U, 16× V100 SXM3, full NVSwitch fabric. Different socket generation (SXM3, not SXM2). Full all-to-all NVLink via NVSwitch. Significantly larger and more expensive. Do not confuse with DGX-1V — they are not compatible.

**DGX A100** — 6U, 8× A100 SXM4, NVSwitch 2.0 full-mesh. The reference A100 platform. Starting to appear on the secondary market from cloud provider rotations.

### 1U/2U Class — 4 GPU (Compact Citadel)

If rack space or cooling is constrained, some vendors offered SXM2 platforms in 1U or 2U form factors with 4 GPUs:

- Dell PowerEdge C4140 (1U, 4× V100 SXM2 in NVLink configuration — specifically "config K")
- Supermicro SYS-1029GQ-TVRT (1U, 4× V100 SXM2)

These cap at 4 GPUs and have tighter thermal margins, but they meet Citadel requirements. Good for operators who want Citadel capability without the 4U footprint.

---

## Practical Considerations

### Power

Server-grade GPU hardware has real power requirements. Plan for this before purchasing.

| Configuration | Typical Draw | Circuit Required |
|---|---|---|
| 4× V100 SXM2 + system | ~1,800W peak | 20A 240V dedicated, or 2× 20A 120V |
| 8× V100 SXM2 + system | ~3,200W peak | 30A 240V dedicated |
| 4× A100 SXM4 + system | ~2,400W peak | 30A 240V dedicated |
| 8× A100 SXM4 + system | ~4,500W peak | 40A 240V or 2× 30A 240V |

In most US homes, a dedicated 240V circuit requires an electrician and a panel slot. This is not a plug-and-play situation for the full 8-GPU configurations. 4-GPU configurations are more manageable but still require a dedicated circuit.

### Cooling

4U GPU servers generate significant heat. At full load, 8× V100 produces ~2.4kW of heat from the GPUs alone, plus CPUs and PSUs.

Requirements:
- Room temperature below 25C / 77F at server intake
- Clear exhaust path (don't put it in a closet)
- Expect 70-80 dB fan noise at load — this is not quiet hardware

Many operators place these servers in a garage, basement, or dedicated closet with active ventilation. Some build sound-dampening enclosures with adequate airflow. Plan the physical environment before the purchase.

### Networking

Most servers ship with 1GbE onboard. This is fine for Nexus API traffic (messages are small). It is not fine for:
- Transferring model files between systems (a 70B model is 40-140GB depending on quantization)
- Multi-node inference (not a Citadel use case, but worth noting)

If you're moving models regularly, add a 10GbE NIC to one of the PCIe expansion slots. A used Mellanox ConnectX-4 is $30-50 and will save hours of transfer time.

### Storage

Models load once from disk into VRAM and stay resident. Storage IOPS only matter at first-load or model swap. Recommended layout:

- Boot: 2× SATA SSD in mirror (~$100-150 used)
- Model cache: 1× 2TB NVMe M.2 on the baseboard (~$120-180)
- Optional: NFS mount from your existing NAS for model library

### Operating System

**Ubuntu Server 22.04 or 24.04 LTS** is the standard recommendation. Nvidia data center drivers install cleanly, CUDA toolkit support is well-documented, and Ollama/vLLM both target Ubuntu as their primary platform.

Alternatives:
- **Proxmox** with GPU passthrough — adds snapshotting and VM isolation. Full PCIe passthrough of 8 GPUs to one VM works but limits flexibility. Useful if the server has other duties.
- **Rocky Linux / RHEL** — works but smaller community for AI-specific tooling.

For a Nexus-dedicated deployment, bare-metal Ubuntu with Docker for the inference containers (Ollama/vLLM) is the simplest path.

---

## Mapping Hardware to Nexus

Once your server is running, the hardware maps to Nexus through three configuration layers:

1. **Launch inference servers** with the right GPU assignments (CUDA_VISIBLE_DEVICES)
2. **Configure `providers.yaml`** with each inference endpoint
3. **Configure `pools.yaml`** with your pool topology

See [gpu-pool-topology.md](gpu-pool-topology.md) for the complete operational playbook — pool modes, operator profiles, launch commands, and metrics configuration.

The key principle: **Nexus doesn't know or care what GPU is behind an endpoint.** A provider entry pointing at `http://localhost:8000/v1` could be a V100 pool, an A100 pool, or a cloud API. The routing, triage, failover, and pool-aware selection all work identically. The hardware is the Operator's domain. The intelligence routing is Nexus's.

---

## Build Checklist

Before purchasing server hardware for a Citadel deployment:

- [ ] GPU socket type confirmed (SXM2, SXM4 — not PCIe-only)
- [ ] NVLink topology confirmed (hybrid cube mesh or NVSwitch)
- [ ] Power delivery: PSU count and wattage sufficient for target GPU count
- [ ] Power circuit: dedicated circuit available or electrician scheduled
- [ ] Cooling: room temperature, exhaust path, and noise tolerance confirmed
- [ ] Memory: balanced across NUMA nodes (6 DIMMs per CPU minimum)
- [ ] Storage: NVMe for model cache, mirrored boot drives
- [ ] Network: 10GbE NIC if transferring models between systems
- [ ] OS decision: bare-metal Ubuntu vs. Proxmox
- [ ] Driver compatibility: Nvidia data center driver version verified against Ollama/vLLM requirements
- [ ] Rails: included or sourced (4U servers are heavy — 60-80 lbs loaded)
- [ ] Physical location: noise, heat, and power all accounted for

---

## Honest Limitations

What Citadel-tier V100 hardware cannot do, regardless of configuration:

- **405B+ class models at inference quality** — Llama 3.1 405B at Q4 needs 200GB+ VRAM. Achievable with 8× 32GB V100 (256GB), but token generation speed will be slow. Practical frontier models this size need A100 80GB or better.
- **Full fine-tuning of 70B+ models** — V100 has no BF16 Tensor Cores (Volta is FP16-only). Fine-tuning frameworks that assume BF16 require adaptation. Inference is unaffected.
- **Frontier multimodal models** — GPT-4o-class multimodal isn't running locally on any current hardware at competitive quality. Specialist vision models (Qwen2-VL, LLaVA) work, but flagship multimodal remains cloud.
- **Real-time high-FPS video inference** — V100 doesn't have the decode throughput for real-time video at production quality.

What A100-class Citadel hardware adds:
- BF16/TF32 support (fine-tuning becomes practical)
- 40-80GB per card (405B-class at Q4 fits in 4× 80GB)
- Higher memory bandwidth (faster token generation)
- Longer driver support runway

The Operator decides which GPU generation fits their workload and budget. V100 is the accessible entry. A100 is the capability jump. H100 is the frontier — if you can find one.
