# Local LLM Setup & Token-Cost Optimization

## Overview

Nexus can run a local LLM (via Ollama) for **zero token cost** on lightweight tasks. The hardware detection phase of the installer recommends whether your system is suitable, and the router automatically offloads nano-tier work to the local model while reserving expensive cloud providers for complex queries.

**Benefit:** Simple reminders, status checks, self-evaluations, and triage can all run on free local inference. Your expensive provider quota goes to work that actually needs it.

---

## Hardware Detection

During setup, the wizard scans your system:

```
  CPU: 4 cores
  RAM: 16 GB
  GPU: None (CPU-only)

✓ Local LLM recommended — llama3.2:3b
  (CPU inference — slower but still useful for triage.)
```

### Recommendations by Hardware

| RAM | CPU Cores | GPU | Recommended Model | Use Case |
|---|---|---|---|---|
| < 8 GB | — | — | Not recommended | Insufficient for local LLM |
| 8 GB | 2-4 | — | `phi4-mini` (3.8B) | Triage, embeddings, simple Q&A |
| 8 GB | 4+ | — | `tinyllama` (1B) | Triage, reminders, status checks |
| 16 GB | 4+ | — | `llama3.2:3b` | All lightweight tasks |
| 32+ GB | — | — | `llama3.1:8b` | More capable local inference |
| Any | Any | NVIDIA 3GB+ | `llama3.2:3b` | GPU-accelerated, very fast |
| Any | Any | NVIDIA 6GB+ | `llama3.1:8b` | Full-powered local |
| Any | Any | AMD/Intel | `phi4-mini` | Varies by GPU |

---

## Installation

### Step 1: Install Ollama

Download from [ollama.com](https://ollama.com) or install via package manager:

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**macOS:**
```bash
brew install ollama
```

**Windows:**
Download installer from ollama.ai

### Step 2: Start Ollama

```bash
ollama serve
```

This starts the server on `http://localhost:11434`. Leave it running in the background or as a systemd service.

### Step 3: Pull a Model

Based on the installer recommendation:

```bash
# For 8GB RAM
ollama pull phi4-mini

# For 16GB+ RAM
ollama pull llama3.2:3b

# For 32GB+ or GPU
ollama pull llama3.1:8b
```

### Step 4: Enable in Nexus

Run the Nexus installer:

```bash
python -m src.setup.wizard
```

When asked about local LLM setup, answer yes. The wizard will:
- Detect Ollama installation
- Confirm model(s) are available
- Add Ollama to your `providers.yaml`

---

## Configuration

### providers.yaml

After setup, your config should look like:

```yaml
providers:
  primary:
    type: claude_code
    model: claude-sonnet-4-6

  triage:
    type: ollama
    model: phi4-mini
    endpoint: http://localhost:11434

  ollama:
    type: ollama
    model: phi4-mini
    endpoint: http://localhost:11434

routing:
  default: primary
  triage: triage
  local_offload: true       # ← Enable local offloading
  local: ollama             # ← Which provider to use for nano tasks
```

### Enable/Disable Local Offloading

In `config/providers.yaml`, set `local_offload`:

```yaml
routing:
  local_offload: true   # Enable — route nano-tier tasks to Ollama
  local: ollama
```

or

```yaml
routing:
  local_offload: false  # Disable — all tasks use configured providers
```

---

## How It Works

### Task Classification

Every message is classified as **nano**, **standard**, or **deep** tier:

| Tier | Example Tasks | Cost |
|---|---|---|
| **nano** | "what time is it?", status checks, simple confirmations, reminders | $0 (local) |
| **standard** | code reviews, explanations, single-file edits, typical conversations | $$ (cloud) |
| **deep** | multi-file refactors, architecture design, complex debugging | $$$ (expensive) |

### Routing Decision

```
Incoming message
    ↓
Triage classifies as [tier]
    ↓
local_offload enabled? & tier == nano? & Ollama running?
    ├─ YES → Route to Ollama (cost: $0)
    └─ NO  → Route to configured provider (cost: $$-$$$)
```

### Example Flows

**Scenario 1: Status check on Claude Opus**
```
User: "is the database up?"
  → Triage: nano tier
  → local_offload: true, Ollama available
  → Routes to phi4-mini on localhost
  → Response: "Database is healthy. Last check: 2 min ago."
  → Cost: $0
```

**Scenario 2: Complex code review**
```
User: "review this architecture and suggest improvements"
  → Triage: deep tier
  → Routes to primary (Claude Opus)
  → Cost: ~$0.05
```

**Scenario 3: Triage itself (meta!)**
```
The triage provider IS Ollama
  → phi4-mini classifies the message
  → Then routes result
  → Cost: $0
```

---

## Systemd Service (Optional)

To keep Ollama running automatically:

```ini
# /etc/systemd/system/ollama.service
[Unit]
Description=Ollama
After=network.target

[Service]
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Type=simple
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable ollama
sudo systemctl start ollama
```

---

## Monitoring

### Check if Ollama is Running

```bash
curl http://localhost:11434/api/tags
```

Returns:
```json
{
  "models": [
    {
      "name": "phi4-mini:latest",
      "size": 1234567890,
      ...
    }
  ]
}
```

### Logs

Ollama runs in foreground by default. If running as service:

```bash
journalctl -u ollama -f
```

### Test Inference

```bash
ollama run phi4-mini "what is 2+2?"
```

---

## Performance Notes

### CPU vs GPU

- **CPU**: Slow but free. ~1-2 tokens/sec depending on model and hardware
- **GPU**: Fast. 10-100+ tokens/sec depending on VRAM and model

For triage (short classifier queries), CPU is fine. For longer tasks, GPU is strongly recommended.

### Model Sizes

- **1B params** (phi4-mini, tinyllama): Fast, fit in 1-2GB RAM, ~60% accuracy
- **3B params** (llama3.2:3b): Balanced, 4-6GB, ~75% accuracy
- **8B params** (llama3.1:8b): Good quality, 8-16GB, ~85% accuracy
- **70B params**: Excellent, need 40GB+ VRAM (GPU only)

For triage, nano-tier models (1-3B) are sufficient. Larger models are over-qualified for simple classification.

---

## Troubleshooting

### "Ollama not found"

Ensure Ollama is installed and running:
```bash
which ollama
# /usr/local/bin/ollama

ollama serve  # in another terminal
```

### "No models installed"

List available:
```bash
ollama list
```

Pull a model:
```bash
ollama pull phi4-mini
```

### "Connection refused"

Ollama is not running. Start it:
```bash
ollama serve
```

Or check it's using the right port:
```bash
lsof -i :11434
```

### Slow response

CPU inference is inherently slower. Consider:
1. Adding GPU support
2. Using a smaller model (1B vs 8B)
3. Disabling local_offload for complex queries

### High memory usage

Local LLMs load the entire model into RAM. To reduce:
1. Use a smaller model (phi4-mini vs llama3.1:8b)
2. Unload other models: `ollama list` → manually delete via `ollama rm <model>`
3. Set memory limits via cgroups (Linux) or Activity Monitor (macOS)

---

## Cost Comparison

Approximate token costs (US list pricing, 2026 — verify current rates at each provider's pricing page before planning a budget):

| Task | Provider | Cost | Local |
|---|---|---|---|
| Triage 100 messages | Claude Opus | $0.50 | $0 |
| 10 simple Q&A | GPT-4o | $0.20 | $0 |
| Status check | Any | varies | $0 |
| Weekly self-eval | Sonnet | $0.05 | $0 |
| **Weekly savings** | — | **$1-5** | **$0** |

Over a month: **$5-20 saved** by offloading 20-30% of queries to local.

---

## Advanced: Custom Triage Provider

You can also run triage on a different (cheaper) cloud provider:

```yaml
routing:
  default: primary          # primary = expensive (Claude Opus)
  triage: groq              # triage = free (Groq instant)
  local_offload: true
  local: ollama

providers:
  primary:
    type: claude_code
    model: claude-sonnet-4-6

  groq:                      # Free tier: 30 req/min, fast
    type: openai
    model: llama-3.1-8b-instant
    api_key: ${GROQ_API_KEY}
    base_url: https://api.groq.com/openai/v1

  ollama:
    type: ollama
    model: phi4-mini
    endpoint: http://localhost:11434
```

This approach uses three tiers:
1. **Triage** = Groq (free)
2. **Nano tasks** = Ollama (free)
3. **Complex work** = Claude Opus (paid, only when needed)

---

## References

- [Ollama docs](https://github.com/ollama/ollama)
- [Supported models](https://ollama.ai/library)
- Model sizes: Use `ollama show <model>` to see parameters
