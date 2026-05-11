# GPU Pool Topology for Nexus

**For Operators running Nexus on a multi-GPU inference server.**

If you have a single GPU or a workstation-class build, this document is not for you — skip it. If you have a server with 4 or more GPUs and are thinking about how to organize them for best results with Nexus, read on.

---

## What This Document Covers

How to tell Nexus about your GPU pool topology so it can route intelligently across them. This includes:

- What pools are and why they matter
- How to declare pool topology in `config/pools.yaml`
- How Nexus uses pool config to route around busy pools
- How to launch the right inference server per pool
- Which operator profile fits your situation

---

## The Core Concept: Pools

A **pool** is a group of one or more GPUs dedicated to a specific role in your Nexus deployment. There are two pool modes:

**Independent** — each GPU in the pool runs its own model instance. No GPU-to-GPU coordination. Maximum concurrent model variety. Best for always-on lightweight workers (triage, standard, specialist roles).

**Pooled** — multiple GPUs combine via tensor parallelism to serve one larger model. Requires NVLink or equivalent high-bandwidth interconnect between the cards. Best for 70B+ class models that don't fit on a single card.

From Nexus's perspective, each pool is one or more provider entries. Nexus doesn't manage GPU assignment — you do that when launching your inference servers. What Nexus does is watch pool health and route around busy pools.

---

## When You Need This

You need `pools.yaml` if:

- You have 4 or more GPUs
- Some of those GPUs are grouped for tensor-parallel inference (pooled mode)
- You want Nexus to route to a secondary deep pool when the primary is busy

You don't need `pools.yaml` if:

- You have a single GPU or two independent GPUs
- All your providers are cloud APIs
- You're fine with requests queueing naturally when a pool is busy

If `pools.yaml` doesn't exist, Nexus operates normally with no pool awareness. The file is fully optional.

---

## Operator Profiles

### General — Independent Workers + One Deep Pool

You have some cards running lightweight models all the time, and one pooled group for heavy requests. Users can wait for the deep pool to finish — no need to route around it.

```
[GPU 0] triage    — phi4-mini         (always resident)
[GPU 1] standard  — llama3.1:8b       (always resident)
[GPU 2] code      — qwen2.5-coder:14b (always resident)
[GPU 3] vision    — qwen2-vl:7b       (always resident)
[GPU 4+5] deep    — llama3.3:70b      (pooled, tensor-parallel)
```

`providers.yaml` maps all five to Nexus tiers. `pools.yaml` documents the topology. `pool_fallback: false` — queuing is fine.

---

### Advanced — Redundant Deep Pools

You have two pooled groups for the deep tier. When Pool A is serving an active inference, Pool B takes the next request instead of queueing.

```
[GPU 0-3] independent workers (same as above)
[GPU 4+5] deep_primary — model A     (pooled)
[GPU 6+7] deep_secondary — model B   (pooled, fallback)
```

`pool_fallback: true`. Nexus polls vLLM's `/metrics` on each pooled endpoint. When `deep_primary` has requests waiting, new deep-tier requests go to `deep_secondary`.

---

### Specialist — Multiple Model Pools

You route different task types to different GPU pools. Code requests go to a code-specific pool. Deep analysis goes to the reasoning pool. Each pool is optimized for its workload.

```
[GPU 0+1] triage + standard  (independent)
[GPU 2+3] code_pool — Qwen Coder 32B or Codestral (pooled)
[GPU 4-7] deep_pool — llama3.3:70b Q8 (pooled, 4-way)
```

Routing patterns in `providers.yaml` direct code-pattern messages to the code provider, reasoning-pattern messages to the deep provider. Pools just add load awareness on top.

---

### Science / Maximum Pool

All compute in one or two massive pools. You need the most VRAM possible for a single model. Triage can be cloud or a small local model on a separate card.

```
[GPU 0-7] flagship — all pooled, one massive model
```

One pool, `pool_fallback: false`. Nexus sends everything to that endpoint. No routing decisions needed beyond triage.

---

## Configuration

Copy `config/pools.yaml.example` to `config/pools.yaml` and uncomment the profile that matches your setup.

### Key Fields

```yaml
pools:
  <pool_name>:
    description: "Human-readable label"
    mode: independent | pooled
    gpus: [0, 1, 2, 3]           # which physical GPUs are in this pool
    vram_per_gpu: 16              # GB per card — used for validation warnings
    providers: [triage, standard] # names from providers.yaml in this pool
    metrics_url: http://localhost:8000/metrics  # vLLM metrics (pooled only)

routing:
  pool_fallback: true | false
  busy_threshold:
    queue_depth: 2                # requests waiting > N = busy
    cache_usage: 0.85             # KV cache > 85% = busy
  poll_interval: 10               # seconds between metrics polls
```

**`gpus`** — informational, used for validation warnings. Nexus does not set `CUDA_VISIBLE_DEVICES`.

**`vram_per_gpu`** — if you mix different VRAM sizes in a `pooled` pool, Nexus will warn at startup. Tensor parallelism requires homogeneous card sizes.

**`metrics_url`** — only relevant for `pooled` mode using vLLM. Points to vLLM's Prometheus metrics endpoint. Without this, load-aware routing is disabled for that pool.

**`pool_fallback`** — when `true`, Nexus routes to a non-busy pool before queuing or using cloud fallback. When `false`, requests queue naturally on the configured provider.

---

## Launching Inference Servers Per Pool

Nexus talks to inference servers via their HTTP endpoints. You configure which GPU each server uses at launch time.

### Independent Workers (Ollama, one per GPU)

```bash
# Launch one Ollama instance per GPU on separate ports
CUDA_VISIBLE_DEVICES=0 OLLAMA_HOST=0.0.0.0:11434 ollama serve &
CUDA_VISIBLE_DEVICES=1 OLLAMA_HOST=0.0.0.0:11435 ollama serve &
CUDA_VISIBLE_DEVICES=2 OLLAMA_HOST=0.0.0.0:11436 ollama serve &
CUDA_VISIBLE_DEVICES=3 OLLAMA_HOST=0.0.0.0:11437 ollama serve &

# Load models
curl http://localhost:11434/api/pull -d '{"name": "phi4-mini"}'
curl http://localhost:11435/api/pull -d '{"name": "llama3.1:8b"}'
# etc.
```

Each instance is a separate provider entry in `providers.yaml` with its own `base_url`.

### Pooled Deep Tier (vLLM, tensor-parallel)

```bash
# Pool A: GPUs 4+5, tensor-parallel
CUDA_VISIBLE_DEVICES=4,5 python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.3-70B-Instruct \
  --tensor-parallel-size 2 \
  --quantization awq \
  --port 8000

# Pool B: GPUs 6+7, different model or same model as fallback
CUDA_VISIBLE_DEVICES=6,7 python -m vllm.entrypoints.openai.api_server \
  --model mistralai/Mixtral-8x7B-Instruct-v0.1 \
  --tensor-parallel-size 2 \
  --port 8001
```

Pool A maps to a provider with `base_url: http://localhost:8000/v1`. Pool B maps to `http://localhost:8001/v1`. The `metrics_url` in `pools.yaml` points to `http://localhost:8000/metrics` and `http://localhost:8001/metrics`.

### Maximum Pool (vLLM, all GPUs)

```bash
# All 8 GPUs in one tensor-parallel pool
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-405B-Instruct \
  --tensor-parallel-size 8 \
  --quantization awq \
  --port 8000
```

One endpoint, one provider in `providers.yaml`.

---

## How Nexus Routes With Pool Awareness

When `pools.yaml` is present:

1. A message arrives from any adapter
2. Triage classifies it (task type, tier)
3. Router selects the matching provider
4. Before sending: PoolManager checks if that provider's pool is busy
5. If busy and `pool_fallback: true`: selects the next non-busy provider in the ProviderChain
6. If all pools are busy: routes normally to the primary provider (doesn't block)

For pooled providers, "busy" is determined by vLLM's `/metrics` endpoint:
- `vllm:num_requests_waiting` > threshold → busy
- `vllm:gpu_cache_usage_perc` > threshold → busy

For independent Ollama workers, "busy" is inferred from response time degradation (no metrics endpoint available in Ollama).

---

## What Nexus Does Not Do

To be explicit about the boundary:

- Nexus does not assign GPUs to processes
- Nexus does not load or unload models
- Nexus does not manage `CUDA_VISIBLE_DEVICES`
- Nexus does not restart crashed inference servers
- Nexus does not auto-scale pool size based on demand

These are operator responsibilities. Nexus is the routing and orchestration layer above them.

---

## Homogeneous VRAM Requirement for Pooled Mode

Tensor parallelism (the mechanism that lets vLLM spread one model across multiple GPUs) requires the GPUs in a pool to have the same VRAM capacity. When you mix card sizes in a pooled group — for example, two 16GB cards and two 32GB cards — the 16GB cards become the bottleneck for each tensor shard. The extra capacity on the 32GB cards goes unused.

**Practical rule: group same-size cards together.**

- 4× 16GB → pool A (independent workers, or pooled at 64GB effective)
- 4× 32GB → pool B (tensor-parallel, 128GB effective)

Not:

- 2× 16GB + 2× 32GB → tensor-parallel pool (only 64GB effective, 32GB wasted)

If you declare a pooled pool with mixed `vram_per_gpu`, Nexus will log a warning at startup but will not prevent it — the Operator may have a reason.

---

## Metrics Reference (vLLM)

vLLM exposes a Prometheus-format endpoint at `/metrics`. Nexus reads the following:

| Metric | Meaning | Busy signal |
|---|---|---|
| `vllm:num_requests_waiting` | Requests in queue | > 2 (configurable) |
| `vllm:num_requests_running` | Active in-flight batch | informational |
| `vllm:gpu_cache_usage_perc` | KV cache pressure (0.0–1.0) | > 0.85 (configurable) |

Poll interval defaults to 10 seconds. Health state older than 3× poll interval is considered stale and ignored.

---

## providers.yaml Reference (Multi-Pool Example)

A full `providers.yaml` for the Advanced profile (4 workers + 2 deep pools):

```yaml
providers:
  triage:
    type: ollama
    model: phi4-mini
    base_url: http://localhost:11434/v1

  standard:
    type: ollama
    model: llama3.1:8b
    base_url: http://localhost:11435/v1

  code:
    type: ollama
    model: qwen2.5-coder:14b
    base_url: http://localhost:11436/v1

  vision:
    type: ollama
    model: qwen2-vl:7b
    base_url: http://localhost:11437/v1

  deep:
    type: vllm
    model: meta-llama/Llama-3.3-70B-Instruct
    base_url: http://localhost:8000/v1

  deep_b:
    type: vllm
    model: mistralai/Mixtral-8x7B-Instruct-v0.1
    base_url: http://localhost:8001/v1

routing:
  default: standard
  triage: triage
  patterns:
    - match: "\\bcode\\b|\\bdebug\\b|\\bfunction\\b|\\brefactor\\b"
      provider: code
    - match: "\\bimage\\b|\\bphoto\\b|\\bvisual\\b"
      provider: vision
    - match: "\\bthink\\b|plan|architect|analyze|design|compare"
      provider: deep
```

With `pools.yaml` declaring `deep` and `deep_b` in separate pools with `pool_fallback: true`, Nexus automatically routes to `deep_b` when `deep` is busy.

---

## NVLink: What It Does and What It Doesn't

NVLink is the high-bandwidth interconnect between GPUs in server-class hardware. When present:

- GPU-to-GPU bandwidth: ~300-600 GB/s (vs. ~32 GB/s for PCIe 3.0)
- Enables tensor parallelism to be practical — the per-layer all-reduce synchronization that would saturate PCIe is fast over NVLink

NVLink does not automatically pool VRAM into one address space. Applications (vLLM, NCCL) use the interconnect for coordinated computation — each GPU still has its own discrete VRAM. The "pool" is a logical construct managed by the inference framework.

For the Operator: if your server has NVLink-connected GPUs and you're using vLLM for tensor parallelism, the interconnect is being used correctly. If you're running independent Ollama instances on each GPU, NVLink is present but not being used — which is fine for independent mode.

---

## Getting Help

If you're unsure which profile fits your hardware, post your GPU count, card model, and VRAM per card. The pool configuration follows from that information directly.
