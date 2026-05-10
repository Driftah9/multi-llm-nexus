# Phone LLM Cluster

Using smartphones as distributed inference nodes inside a Nexus deployment.

---

## The Concept

Modern smartphones contain serious inference hardware — NPUs rated at 35–98 TOPS, 6–12GB of unified memory, and efficient mobile SoCs — that sit idle the vast majority of the time. A shelf of used flagship phones running a stripped OS can function as a dedicated, low-power inference fabric, with Nexus acting as the maestro that routes work to it.

The phones are not standalone AI assistants. They are dumb inference nodes. The intelligence, routing, session management, and platform adapters all live on the Nexus machine. The phone cluster is just a pool of distributed compute that Nexus talks to through a single API endpoint.

---

## Architecture

```
Nexus Machine
├── Nexus software          (maestro — routing, tiers, sessions, adapters)
├── Docker containers
│   ├── Cluster coordinator (Exo or llama.cpp-rpc server)
│   ├── API gateway         (exposes OpenAI-compat endpoint to Nexus)
│   └── Node monitor        (health checks, failover signaling)
└── PCIe USB expansion card(s)
    ├── USB 3.x → Phone node 1  (triage model — always resident)
    ├── USB 3.x → Phone node 2  (standard shard)
    ├── USB 3.x → Phone node 3  (standard shard)
    ├── USB 3.x → Phone node 4  (deep shard)
    ├── USB 3.x → Phone node 5  (deep shard)
    └── USB 3.x → Phone node 6  (RAG / embeddings)
```

The Docker container on the Nexus machine handles node registration, shard assignment, and model coordination. To Nexus itself, the entire phone cluster looks like any other OpenAI-compatible provider endpoint — one `base_url` in `providers.yaml`.

```yaml
# providers.yaml
phone_cluster:
  type: openai
  model: llama3.1:70b
  base_url: http://localhost:52415/v1
  api_key: no-key
```

---

## Why USB, Not WiFi

USB tethering creates a direct point-to-point network interface between the Nexus machine and each phone — no router, no shared medium, no contention between nodes. Each phone gets its own dedicated port from the PCIe expansion card.

**Recommended PCIe cards:** Look for cards running the **Renesas µPD720201/202** or **ASMedia ASM3142** chipset. These provide true per-port bandwidth rather than a shared internal hub. A 4-port USB 3.2 Gen 2 card gives each phone a dedicated 10 Gbps lane.

USB also delivers power simultaneously — phones charge while running inference, so battery state is never a constraint.

Two 4-port PCIe cards = 8 phone nodes, all with independent full-bandwidth connections.

---

## Phone OS: LineageOS as the Inference OS

Stock Android on an older phone runs Google Play Services, background sync, push notification daemons, telemetry, and manufacturer bloat — all competing for the RAM and CPU cycles that should go toward inference.

**LineageOS removes most of this.** What remains:
- Kernel + hardware drivers
- ADB access (Nexus machine manages nodes over USB)
- llama.cpp server or Exo node running as a persistent service
- USB tethering enabled, WiFi and mobile data off

The phone stops being a phone. It becomes an inference appliance with an NPU on board.

LineageOS has wide support for **2018–2021 era flagship devices** — the Snapdragon 845 / 855 / 865 generation is well covered with stable, maintained ROMs. The SD865 in particular has a strong Hexagon DSP that llama.cpp can target directly.

Alternatively, **stock Android with ADB and minimal background services** works on devices where LineageOS support is limited or where a clean install isn't desired.

---

## Minimum Viable Phone Spec

| Spec | Minimum | Recommended |
|---|---|---|
| RAM | 6 GB | 8–12 GB |
| SoC | Snapdragon 845 / Exynos 9810 era | Snapdragon 865 / A14 Bionic era |
| Storage | 64 GB | 128 GB |
| USB | USB 3.0 tethering | USB 3.1 |
| OS | Stock Android 10+ (minimal) | LineageOS 20+ |

A 6GB phone with SD845 holds a 3B model entirely in memory with room for the OS overhead. An 8GB phone runs a 7B shard comfortably. Six 8GB phones = ~48GB distributed unified memory.

---

## Tier Role Assignment

Each phone (or group of phones) maps to a Nexus tier:

| Role | Tier | Node count | Model |
|---|---|---|---|
| Triage | nano | 1 phone | phi4-mini (~2.5GB) — always resident, sub-1s |
| Embeddings | — | 1 phone | nomic-embed-text (~0.3GB) — RAG support |
| Standard | standard | 2–3 phones | llama3.1:8b distributed, or one 8B per node |
| Deep | deep | Full cluster | llama3.1:70B sharded across all nodes |

The triage node is the most important single device in the cluster — it runs on every incoming message before routing. Keeping it on a dedicated phone ensures the standard and deep rings never interfere with classification latency.

---

## What the Docker Layer Buys You

Running the coordinator in Docker on the Nexus machine means:

- **Node health monitoring** — if a phone drops out or restarts, the coordinator detects it. Nexus can fall back to a different provider tier automatically via the routing config.
- **Shard rebalancing** — when a node rejoins, the coordinator redistributes model shards without manual intervention.
- **Single endpoint** — the phone cluster stays as one `base_url` in `providers.yaml` regardless of how many nodes are behind it.
- **Updatable in place** — update the coordinator image without touching the phones.

---

## Power Profile

A 6-phone cluster at ~8–12W per device = **50–72W total** running a distributed 70B inference workload.

A single V100 SXM2 at full load = 250–300W.

For always-on operation (triage node never sleeps, standard and deep available on demand), the phone cluster is a low-power inference appliance that fits on a shelf and runs on a single power strip.

---

## The Full Stack with Nexus

When combined with a V100 build (see README hardware section), the full deployment looks like:

| Layer | Component | Role |
|---|---|---|
| Maestro | Nexus | Routing, sessions, all platform adapters |
| Coordination | Docker on Nexus | Phone cluster manager, API gateway |
| Phone fabric | 4–8 node shelf | Low-power distributed inference |
| Local powerhouse | V100 SXM2 32GB | High-VRAM models, GPU-speed single-session |
| Cloud burst | Groq / Anthropic | Overflow, max-quality requests |

Each layer is a provider in `providers.yaml`. Nexus routes to whichever tier the message calls for. The operator never changes their workflow as the infrastructure scales.

---

## Current State of the Tooling

**Exo** is the primary project enabling heterogeneous device clusters for LLM inference. It supports iOS and Android nodes, uses a ring topology for distributed inference, and exposes an OpenAI-compatible API. It is actively developed but still maturing — evaluate stability before committing it to a production deployment.

**llama.cpp RPC mode** is the alternative — more manual to set up, but more stable and widely tested. Each phone runs a `llama-rpc-server` process; the host machine runs the main llama.cpp process and distributes layers to the nodes over the network.

Both paths expose an endpoint that Nexus connects to as a standard OpenAI-compatible provider. No changes to the Nexus codebase are needed.
