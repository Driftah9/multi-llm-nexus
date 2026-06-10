# AI Provider Reference — Full Landscape

**Purpose:** Single source of truth for the AI providers you can integrate into multi-llm-nexus. Covers free tiers, rate limits, models, access requirements, and where each fits in the tier routing system.

**Example tier-1 stack:** A common starting set is Claude (or any subscription/API frontier model), Groq, Cerebras, Gemini, GitHub Models, Mistral, SambaNova, Cohere (RAG), NVIDIA NIM, and a local Ollama for offline fallback. Mix and match based on which keys you have.

**This file is the installer's provider catalog.** The setup wizard draws the selectable-provider list and each provider's auth model + free-tier constraints from the knowledge captured here. When you drop in a key for any provider below, the integration is already known — auth type, OpenAI-compatible `base_url`, and rate/free-tier limits — so it "just works" without new code (the `openai`-compatible type fronts most API providers via `base_url`; native types cover the rest).

### Keeping this current — adding a new provider

When a new provider launches (or an existing one's tiers change), capture it here so it's never overlooked, then wire it into the installer. Research these five facts:

1. **Auth model** — subscription, free-tier API key, paid key, or local (no key). What env var holds the key?
2. **Endpoint + OpenAI-compat** — is there an OpenAI-compatible `base_url`? (If yes, no new provider module is needed — reuse the `openai` type.) If not, which native type/SDK?
3. **Free-tier limits** — RPM / RPD / TPM / TPD, monthly caps, credit expiry. These feed `provider_quota` enforcement.
4. **Models + tier fit** — model IDs and whether they suit nano / standard / deep.
5. **Signup URL + setup time.**

Then: add a summary-table row + a full section below, and register it in the wizard's provider list and `src/providers/registry.py` (see `CONTRIBUTING.md` → *Adding a provider*).

---

## Quick Summary Table

| Provider | Tier | Free? | RPM | RPD | TPM | TPD | OpenAI Compat | Status |
|---|---|---|---|---|---|---|---|---|
| **Claude (Anthropic)** | all | Subscription (Max) | Unrestricted | Unrestricted | — | — | No (CLI) | ✅ Active |
| **Groq** | nano | ✅ Yes | 30 | 14,400 | 6,000 | 500K | ✅ Yes | ✅ Active |
| **Cerebras** | standard | ✅ Yes | 30 | 14,400 | 60,000 | 1M | ✅ Yes | ✅ Active |
| **Google Gemini** | standard | ✅ Yes | 10 | 1,500 | 250,000 | — | ✅ Yes | ✅ Active |
| **GitHub Models** | standard/deep | ✅ Yes | 10–15 | 50–150 | — | — | ✅ Yes | ✅ Active |
| **Mistral** | standard | ✅ Yes | ~unlimited | — | 500,000 | — | ✅ Yes | ✅ Active |
| **SambaNova** | deep | ✅ Yes | 10 | 1,000 | — | — | ✅ Yes | ✅ Active |
| **Cohere** | RAG only | ✅ Yes | 20 | — | — | 1K calls/mo | No (v2 API) | ✅ Active (RAG) |
| **NVIDIA NIM** | standard fallback | Credits (1K free) | 40 | — | — | — | ✅ Yes | ✅ Active |
| **Ollama / qwen2.5:3b** | offline fallback | Free (local) | 3 | — | — | — | ✅ Yes | ✅ Active |
| **Ollama / tinyllama** | emergency only | Free (local) | 5 | — | — | — | ✅ Yes | ✅ Active |
| **OpenRouter** | standard | ⚠️ 50 RPD free | 20 | 50 / 1K* | — | — | ✅ Yes | ⏸ Hold |
| **HuggingFace** | nano | ✅ Yes | dynamic | dynamic | — | — | Partial | 🔲 DNS issue |
| **Cloudflare AI** | multimodal | ✅ Yes | — | — | 10K neu/day | — | No (CF) | 🔲 Staged |
| **DeepInfra** | standard | $5 credits | — | — | — | — | ✅ Yes | 🔲 Staged |
| **Together AI** | standard | ❌ None | — | — | — | — | ✅ Yes | 🔜 Future |
| **Perplexity** | deep | Trial only | — | — | — | — | Partial | 🔜 Future |

*OpenRouter: 50 RPD without purchase, 1K RPD after one-time $10 deposit (permanent unlock).

---

## Active Providers (Connected Now)

### Claude — Anthropic
- **Access:** Claude subscription (Max or Pro tier) — or an Anthropic API key
- **Tier:** All tiers (nano triage via Haiku, standard via Sonnet, deep via Opus)
- **Rate model:** A subscription can be treated as **unrestricted** (run until error, then failover); session limits such as rolling-window and weekly caps are surfaced only as errors. An API key meters per-token instead.
- **Integration:** CLI subprocess (e.g. `claude --model <haiku|sonnet|opus> ...`) or the standard Anthropic API
- **Routing role:** Primary for all tiers; strong fallback of last resort; common choice for the synthesis/arbiter seat in a council
- **Models in use:**
  - `haiku` — triage + fast nano tasks
  - `sonnet` — standard default
  - `opus` — deep reasoning, max effort

---

### Groq — LPU Cloud
- **Access:** Free tier API key (`GROQ_API_KEY`)
- **Tier:** nano (triage workhorse, fast classification)
- **Rate limits (free):**
  - 30 RPM / 14,400 RPD
  - 6,000 TPM ← **tightest constraint**
  - 500,000 TPD
- **Models available:**
  - `llama-3.3-70b-versatile` — council member (30 RPM)
  - `llama-3.1-8b-instant` — triage/nano (30 RPM)
  - `llama-3.2-11b-vision-preview` — vision tasks
  - `llama-3.2-1b-preview` — ultra-fast nano
  - `mixtral-8x7b-32768` — MoE, 32K context
- **OpenAI compat:** ✅ `https://api.groq.com/openai/v1`
- **Key:** Get from [console.groq.com](https://console.groq.com) — no card
- **Strength:** Fastest inference (LPU hardware). Best for triage and routing decisions.
- **Limit to watch:** 6K TPM cap means long prompts eat budget fast. Keep triage prompts short.

---

### Cerebras — Custom Wafer-Scale Hardware
- **Access:** Free tier API key (`CEREBRAS_API_KEY`)
- **Tier:** standard / deep (high token volume)
- **Rate limits (free):**
  - 30 RPM / 14,400 RPD
  - 60,000 TPM
  - 1,000,000 TPD ← **highest free TPD of any provider**
- **Models available:**
  - `qwen-3-235b-a22b-instruct-2507` — flagship (primary)
  - `gpt-oss-120b` — 120B OSS model
  - `zai-glm-4.7` — GLM series
  - `llama3.1-8b` — fast nano
- **OpenAI compat:** ✅ `https://api.cerebras.ai/v1`
- **Key:** Get from [cloud.cerebras.ai](https://cloud.cerebras.ai) — no card
- **Strength:** 1M TPD daily budget + fast hardware. Best sustained-volume provider. Best price-per-token in free tier.
- **Limit to watch:** The Qwen3 235B model ID has variant gotchas (a wrong variant returns 404). Confirm model IDs before use.

---

## Staged Providers (Config Ready, Key Needed)

### Google Gemini — AI Studio
- **Access:** Free API key (`GOOGLE_API_KEY`) — no card
- **Tier:** standard (high-throughput volume)
- **Rate limits (free):**
  - 10 RPM / 1,500 RPD
  - 250,000 TPM ← **highest free TPM of any provider**
  - No documented daily token cap
- **Models:**
  - `gemini-2.5-flash` — primary (fast, 128K context, thinking mode)
  - `gemini-2.5-flash-exp` — preview
  - `gemini-1.5-pro` — older, slower, lower limits
  - `gemini-2.0-flash` — stable general-purpose option
- **OpenAI compat:** ✅ `https://generativelanguage.googleapis.com/v1beta/openai/`
- **Key source:** [ai.google.dev](https://ai.google.dev) → "Get API Key"
- **Setup time:** ~2 min, no payment
- **Strength:** Best sustained throughput (250K TPM). Long context free (128K). Multimodal (text + image).
- **Best for:** High-volume standard completions, long-context work, image input tasks
- **Routing rule:** Match `summarize|extract|reformat|translate|long text|full document`

---

### GitHub Models — Azure-Backed Gateway
- **Access:** GitHub Personal Access Token (`GITHUB_TOKEN`) — existing GitHub account
- **Tier:** standard
- **Rate limits (free):**
  - Low-tier models (Llama, Phi, Mistral): 15 RPM / 150 RPD / 8K input / 4K output
  - High-tier models (GPT-4o, o3, Grok-3): 10 RPM / 50 RPD / 8K input / 4K output
- **Models (free access):**
  - `gpt-4o` — OpenAI flagship, free
  - `gpt-4.1` — GPT-4 Turbo, free
  - `o3` — OpenAI's reasoning model, free
  - `grok-3` — xAI's Grok, free (only free path)
  - `claude-3.5-sonnet` — Anthropic via GitHub, free
  - `llama-3.2-11b` — Meta, 15 RPM / 150 RPD
  - `phi-4` — Microsoft, 15 RPM / 150 RPD
- **OpenAI compat:** ✅ `https://models.inference.ai.azure.com/v1`
- **Key source:** GitHub → Settings → Developer settings → Personal access tokens (classic) → check `api` scope
- **Setup time:** ~3 min
- **Unique value:** Only free path to GPT-4o, o3, and Grok-3 without paying OpenAI/xAI. Essential for council diversity.
- **Best for:** Council member perspective diversity; testing closed frontier models at zero cost

---

### Mistral — La Plateforme (France-Hosted)
- **Access:** Free API key (`MISTRAL_API_KEY`) — phone verification, no card
- **Tier:** standard
- **Rate limits (free):**
  - No published RPM (implied very high)
  - ~500K TPM / ~1B tokens per month (estimated based on behavior)
  - No daily cap documented
- **Models:**
  - `mistral-large-latest` — flagship
  - `mistral-small` — faster, cheaper (also free)
  - `codestral` — code-focused
  - `mistral-medium` — balanced
  - `pixtral-12b` — multimodal (vision)
- **OpenAI compat:** ✅ `https://api.mistral.ai/v1`
- **Key source:** [console.mistral.ai](https://console.mistral.ai) — phone verify
- **Setup time:** ~2 min + phone verify
- **Strength:** EU data residency (France-hosted). GDPR-native. ~1B tokens/month is the most generous published monthly budget of any free tier.
- **Best for:** EU compliance routing, GDPR-sensitive prompts, high-volume sustained loads when Gemini is saturated
- **Routing rule:** Match `gdpr|eu|europe|personal data|data residency`

---

### SambaNova — Custom RDU Hardware
- **Access:** Free API key (`SAMBANOVA_API_KEY`) — no card
- **Tier:** deep
- **Rate limits (free):**
  - 10 RPM / 1,000 RPD
  - $5 one-time credits at signup (30-day expiry — separate from persistent free tier)
- **Models (current as of 2026-06-09 — verified via API):**
  - `DeepSeek-V3.2` — primary deep model (strong reasoning, replaces retired 405B)
  - `Meta-Llama-3.3-70B-Instruct` — standard fallback
  - `Llama-4-Maverick-17B-128E-Instruct` — multimodal
  - `gpt-oss-120b` — 120B OSS model
  - `MiniMax-M2.7`, `gemma-3-12b-it`, `gemma-4-31B-it` — alternatives
  - ~~`llama-3.1-405b`~~ — **retired** (no longer available on free tier)
- **OpenAI compat:** ✅ `https://api.sambanova.ai/v1`
- **Key source:** [cloud.sambanova.ai](https://cloud.sambanova.ai) — email signup
- **Strength:** RDU hardware (custom silicon). DeepSeek V3.2 is a strong deep-tier model.
- **Best for:** Deep tier council participation, complex reasoning when Claude is limited
- **Note:** Model lineup changes without notice — verify model IDs via `/v1/models` before adding new ones

---

---

## Local Providers (Ollama)

### Ollama — Local (Primary)
- **Access:** Local service, no API key required
- **Endpoint:** `http://localhost:11434/v1` (OpenAI-compatible)
- **Role:** Offline fallback only — activates when ALL remote providers are unreachable
- **Triage:** On a CPU-only host, not recommended for triage (latency too high); becomes a triage candidate once a GPU makes inference fast.
- **Models:**
  - `qwen2.5:3b` (1.9GB) — capable nano worker, correct on factual tasks. Good primary local model.
  - `tinyllama:latest` (0.6GB) — emergency last resort. Fast but unreliable on complex tasks.
  - `nomic-embed-text` (0.3GB) — embeddings only, not for chat routing
- **Latency benchmark (CPU-only, indicative):**
  - `qwen2.5:3b`: ~12–14s avg (varies with system load)
  - `tinyllama`: ~7–18s avg (CPU contention)
- **RPM limit:** keep low (e.g. 3 RPM qwen3b, 5 RPM tinyllama) — CPU-bound, pace carefully

### Ollama — Second Host (Overflow, optional)
- **Access:** Another Ollama host on your LAN, no API key required
- **Endpoint:** `http://<lan-host>:11434/v1` (e.g. `http://192.0.2.10:11434/v1`)
- **Role:** Overflow/redundancy when the primary local Ollama is busy or down
- **Models:** Same as primary — `qwen2.5:3b`, `tinyllama:latest`
- **Priority:** Tried after the primary local host within the same tier

### GPU Upgrade Path
When a capable GPU is available (e.g. a datacenter-class card or equivalent):
1. Re-run latency benchmarks — target <2s for qwen3b
2. Pull a larger capable model (e.g. qwen2.5:7b or llama3.2:7b)
3. Move local models to the TOP of the nano/standard roster in your provider resolver config
4. Wire triage to use the local model instead of a remote one
5. Local becomes cost-zero triage + low-latency nano worker

---

### OpenRouter — Multi-Provider Gateway
- **Access:** Free account (`OPENROUTER_API_KEY`) — key stored in `.env` but **commented out**
- **Tier:** standard (gateway abstraction)
- **Status:** ⏸ HOLD — 50 RPD free limit is too low for production routing
- **Rate limits (free, no deposit):**
  - 20 RPM / **50 RPD** ← not viable as a routing fallback
- **Rate limits (after one-time $10 deposit — permanent unlock):**
  - 20 RPM / **1,000 RPD**
- **Free model variants (`:free` suffix, 29 models as of 2026-06):**
  - `deepseek/deepseek-r1:free` — DeepSeek R1 reasoning
  - `qwen/qwen3-coder-480b:free` — Qwen3 480B coder (262K context)
  - `meta-llama/llama-4-maverick:free` — Llama 4
  - `openrouter/free` — auto-router picks from all free models randomly
- **OpenAI compat:** ✅ `https://openrouter.ai/api/v1`
- **Key source:** [openrouter.ai](https://openrouter.ai) — email signup
- **Activate when:** $10 deposit is made (1K RPD) OR DeepSeek R1 / Qwen3 480B access is specifically needed
- **Unique value:** Gateway to 200+ models including otherwise-inaccessible free variants. `openrouter/free` auto-router is useful for council diversity.

---

## Tier 2: Specialized Providers

### Cohere — RAG & Retrieval Specialist
- **Access:** Free API key (`COHERE_API_KEY`) — email, no card
- **Tier:** standard (specialist use only — not general chat)
- **Rate limits (free):**
  - 20 RPM (chat)
  - 10 RPM (rerank)
  - 2,000 inputs/min (embed)
  - **1,000 API calls/month total** ← hard monthly cap
- **Models:**
  - `command-r-plus` — best for RAG pipelines
  - `command-r` — lighter
  - `embed-english-v3.0` — embeddings
  - `rerank-english-v3.0` — **best-in-class reranking**
- **OpenAI compat:** ❌ Native SDK required
- **Key source:** [cohere.com](https://cohere.com) — email signup
- **Best for:** Document reranking (best in class), embedding generation, retrieval-augmented generation
- **Do NOT use for:** General chat (1K calls/month is consumed fast; save for RAG-specific tasks)

---

### NVIDIA NIM — Scientific Domains
- **Access:** 1,000 free credits on signup + can request 4,000 more (`NVIDIA_NIM_API_KEY`)
- **Tier:** standard (domain-specific routing)
- **Rate limits:**
  - 40 RPM
  - Credits-based (not persistent free — expires)
- **Models (91+ specialized):**
  - `meta/llama-3.1-70b-instruct` — general
  - `nvidia/bio-llm` — biology/genomics
  - `nvidia/climate-llm` — climate/weather
  - `nvidia/safety-checker` — content safety
  - Various chemistry, materials science, code-specific models
- **OpenAI compat:** ✅ `https://integrate.api.nvidia.com/v1`
- **Key source:** [api.nvidia.com/nim](https://api.nvidia.com/nim)
- **Best for:** Scientific domain routing (biology, genomics, climate). Docker self-hosted NIM containers available separately for local inference.
- **Caveat:** Credits expire. Not a persistent free tier like Groq/Cerebras. Plan usage carefully.

---

### HuggingFace Inference API — OSS Model Explorer
- **Access:** Free account + API token (`HF_API_KEY`)
- **Tier:** nano (latency-tolerant exploration)
- **Rate limits:**
  - Dynamic, undocumented (~hundreds of requests/hour estimated)
  - Cold start latency on unpopular models
  - PRO plan ($9/mo): 2M credits/month for dedicated endpoints
- **Models:** 300,000+ community models including:
  - `mistralai/Mistral-7B-Instruct-v0.3`
  - `microsoft/phi-4`
  - `google/gemma-2-9b-it`
  - Any public model on HuggingFace Hub
- **OpenAI compat:** Partial — `https://api-inference.huggingface.co/v1` (newer serverless models)
- **Key source:** [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
- **Best for:** Discovering and testing open-source models, cold-start-acceptable research tasks, access to non-commercial or specialized community models

---

### Cloudflare Workers AI — Edge + Multimodal
- **Access:** Cloudflare account (`CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`)
- **Tier:** standard (multimodal specialist)
- **Rate limits (free):**
  - 10,000 neurons/day (Cloudflare proprietary compute unit)
  - 300+ global PoPs
- **Models:**
  - `@cf/meta/llama-3.3-70b-instruct-fp8` — text
  - `@cf/black-forest-labs/flux-1-schnell` — image generation (FLUX)
  - `@cf/openai/whisper` — speech-to-text
  - `@cf/meta/m2m100-1.2b` — translation
  - Various TTS models
- **OpenAI compat:** ❌ Proprietary CF bindings (different API pattern)
- **Key source:** Cloudflare dashboard → API Tokens → Create custom token (Workers + Account permissions)
- **Best for:** Image generation, TTS, STT, edge-latency-sensitive text inference
- **Unique value:** Only free provider covering text + image + audio in one account. 300 global PoPs for low latency.

---

## Tier 3: Paid Fallback

### DeepInfra — Cheapest Paid Option
- **Access:** $5 free credits on signup, then pay-as-you-go (`DEEPINFRA_API_KEY`)
- **Tier:** standard (budget paid fallback)
- **Rate limits:** No hard limits (paid)
- **Pricing:** $0.02–0.06 per 1M tokens (cheapest in market)
- **Models:** 50+ including Llama 3 70B, Mistral, Zephyr, CodeLlama
- **OpenAI compat:** ✅ `https://api.deepinfra.com/v1/openai`
- **Key source:** [deepinfra.com](https://deepinfra.com)
- **Best for:** Overflow when all free tiers are saturated. Use as last fallback before Claude.

---

### Together AI
- **Access:** Paid — $5 minimum, no free tier
- **Tier:** standard
- **OpenAI compat:** ✅
- **Key source:** [together.ai](https://together.ai)
- **Hold reason:** No persistent free tier. DeepInfra is cheaper for the same use case. Add only if DeepInfra is unavailable.

---

### Perplexity API
- **Access:** Trial credits only
- **Tier:** deep (search-augmented reasoning)
- **Unique capability:** Web search built into model calls (Perplexity Sonar)
- **Key source:** [perplexity.ai/api](https://perplexity.ai/api)
- **Hold reason:** Trial-based, not persistent free. Add when search-augmented generation is specifically needed in the council.

---

## Tier Mapping — Where Each Provider Fits

```
NANO TIER (fast, cheap, triage/classification/routing)
  Remote (primary):
  ├── Groq Llama-8B        — primary (~0.5s LPU inference)
  ├── Cerebras Llama-8B    — overflow (1M TPD)
  ├── GitHub Llama-11B     — fallback (150 RPD)
  └── Claude Haiku         — subscription fallback
  Offline fallback (activates only when ALL remote providers are down):
  ├── qwen2.5:3b @local    — capable nano, ~12s CPU
  ├── qwen2.5:3b @overflow — second local host
  ├── tinyllama @local     — emergency, ~7s but low quality
  └── tinyllama @overflow  — last resort

STANDARD TIER (most requests — summaries, coding, drafting)
  Remote (primary):
  ├── Cerebras Qwen3-235B  — primary (1M TPD, fast)
  ├── Google Gemini Flash  — high throughput (250K TPM)
  ├── Mistral Large        — EU compliance, ~1B/month
  ├── GitHub GPT-4o        — frontier diversity (50 RPD)
  ├── Groq 70B             — fast inference if TPM allows
  ├── NVIDIA Llama-70B     — NIM credits fallback
  └── Claude Sonnet        — subscription fallback
  Offline fallback: same local chain as nano tier

DEEP TIER (complex reasoning, architecture, production changes)
  Remote (primary):
  ├── Claude Opus          — primary (unrestricted subscription)
  ├── SambaNova DeepSeek   — DeepSeek V3.2 on RDU hardware (1K RPD)
  ├── SambaNova Llama-70B  — Llama 3.3 70B on RDU (1K RPD)
  ├── GitHub o3            — OpenAI reasoning model (50 RPD)
  ├── Cerebras Qwen3-235B  — sustained volume fallback
  └── Claude Sonnet        — subscription fallback
  Offline fallback: qwen2.5:3b (best local for deep — tinyllama not suitable)

SPECIALIST ROUTING (not general tier — function-specific)
  ├── Cohere              — RAG rerank/embed only (1K calls/mo hard cap)
  ├── NVIDIA NIM          — 120+ scientific/domain models (credits)
  └── Cloudflare AI       — image/audio/multimodal (staged)
```

**Triage** always uses Claude Haiku (remote). Local models take over triage only after GPU upgrade makes latency competitive (target: <2s).

---

## Rate Limit Comparison (Free Tiers Only)

```
TPM (tokens per minute — sustained throughput):
  Google Gemini    ████████████████████████████ 250,000
  Cerebras         ████████                      60,000
  Mistral          varies (high, ~500K cap)
  Groq             █                              6,000

TPD (tokens per day — daily budget):
  Cerebras         ████████████████████████████ 1,000,000
  Groq             ████████████████              500,000
  Google Gemini    unknown (no published cap)
  Mistral          ~15B/month (500K TPM × 24h)

RPD (requests per day — call frequency):
  Groq             ████████████████████████████ 14,400
  Cerebras         ████████████████████████████ 14,400
  Google Gemini    ████                          1,500
  Mistral          (no published RPD)
  SambaNova        █                             1,000
  GitHub Models    ▌                             50–150
```

---

## Monthly Token Budget (Free Tiers, Estimated)

| Provider | Monthly Estimate | Basis |
|---|---|---|
| Groq | 15B tokens | 500K TPD × 30 |
| Cerebras | 30B tokens | 1M TPD × 30 |
| Google Gemini | ~225B tokens | 250K TPM × 24h × 30 |
| Mistral | ~1B tokens | stated cap |
| SambaNova | ~30M tokens | 1K RPD × 30 × ~1K tok avg |
| GitHub Models | ~1.5M tokens | 50 RPD × 30 × ~1K tok avg |
| HuggingFace | ~3M tokens | dynamic |
| Cloudflare | variable | 10K neurons/day |
| **Total free** | **~270B+ tokens/month** | |

For context: at frontier API pricing of ~$3–15/M tokens, buying 270B tokens would cost **$800K–4M**. The free tier stack is a significant resource.

---

## Provider Onboarding Checklist

```
Priority 1 — Get these first (10 min total, no payment):
[ ] Google Gemini     → ai.google.dev → "Get API Key"
[ ] GitHub Models     → github.com/settings/tokens → new classic token, api scope
[ ] Mistral           → console.mistral.ai → email + phone verify

Priority 2 — Get when you're ready to expand:
[ ] SambaNova         → cloud.sambanova.ai → email signup
[ ] OpenRouter        → openrouter.ai → email signup

Priority 3 — Specialized use:
[ ] Cohere            → cohere.com → email (for RAG only)
[ ] NVIDIA NIM        → api.nvidia.com/nim (for scientific routing)
[ ] HuggingFace       → huggingface.co/settings/tokens (for OSS model testing)
[ ] Cloudflare AI     → Cloudflare dashboard (for multimodal)

Priority 4 — Paid fallback:
[ ] DeepInfra         → deepinfra.com ($5 credits, keep as overflow)
```

**All keys go in your project's `.env`** (never committed — Nexus ships with placeholders only). The provider resolver reads them at startup.

---

## Key Env Vars Reference

```bash
# Example .env format (use placeholders until you have real keys)
CEREBRAS_API_KEY=csk_xxxxxxxx
GROQ_API_KEY=gsk_xxxxxxxx

# Priority 1 — get these first
GOOGLE_API_KEY=AIzaSyxxxxxxxx
GITHUB_TOKEN=ghp_xxxxxxxx
MISTRAL_API_KEY=xxxxxxxx

# Priority 2
SAMBANOVA_API_KEY=sb-xxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx

# Priority 3 (specialized)
COHERE_API_KEY=xxxxxxxx
NVIDIA_NIM_API_KEY=nvapi-xxxxxxxx
HF_API_KEY=hf_xxxxxxxx
CLOUDFLARE_API_TOKEN=xxxxxxxx
CLOUDFLARE_ACCOUNT_ID=xxxxxxxx

# Priority 4 (paid fallback)
DEEPINFRA_API_KEY=xxxxxxxx
```

---

## How Activation Works

Activating a provider is configuration-only — no code changes required:

1. Add the provider's API key to your project's `.env` (use the env-var names in the reference above).
2. Set `enabled: true` for that provider in `config/providers.yaml`.
3. Restart Nexus. The provider resolver reads the key at startup, runs a key-availability check, and slots the provider into its tier with ordered fallback.

The resolver handles tier → provider → model selection, rate-limit pre-checks before each invoke, and usage logging after each call. Staged providers (config present, key missing) stay dormant until their key appears in `.env`.
