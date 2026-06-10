# AI Provider Integration Roadmap
**Goal:** Harness ALL providers (free, paid, subscription, local, gateway) within their rate limit constraints. Offload token burn from Claude to cheaper/free alternatives while monitoring usage.

**The landscape:**
- A typical starting stack is a frontier model (e.g. Claude via CLI or API) plus a couple of fast free tiers like Cerebras and Groq.
- Beyond that there are 20+ more providers with persistent free tiers and paid options to layer in.
- Architecture ready: Multi-LLM-Nexus can route across any provider count.

---

## TIER 1: Add First (Persistent Free, High Value, OpenAI-Compatible)

### 1. Google AI Studio (Gemini)
**Integration Effort:** ⭐ (5 min)  
**Provider Type:** Native SDK + OpenAI-compat endpoint  
**Status:** Not connected

```yaml
google_gemini:
  type: openai
  model: gemini-2.5-flash
  api_key: ${GOOGLE_API_KEY}
  base_url: https://generativelanguage.googleapis.com/v1beta/openai/
  priority: 2
  tier: standard
  display_prefix: Google
  model_display: Gemini-Flash
  access_tier: free
  rpm: 10
  rpd: 1500
  tpm: 250000    # Highest free throughput
  tpd: null      # No daily limit documented
```

**Rate Limits (Free):**
- 10 RPM / 1,500 RPD / 250K TPM
- No daily token cap published (appears unlimited)

**Best Use Cases:**
- High-volume standard completions (10 RPM sustainable)
- Multimodal (text + image in Flash)
- Long-context (128K on Flash)

**Why Add:** Best free sustained throughput. OpenAI-compat endpoint = drop-in replacement.

**Key:** `GOOGLE_API_KEY` from [ai.google.dev](https://ai.google.dev) — no card required.

---

### 2. GitHub Models (Frontier Models Free)
**Integration Effort:** ⭐ (5 min)  
**Provider Type:** OpenAI-compatible gateway  
**Status:** Not connected

```yaml
github_models:
  type: openai
  model: gpt-4o                           # or: gpt-4.1, o3, grok-3, claude-3.5
  api_key: ${GITHUB_TOKEN}                # Personal access token
  base_url: https://models.inference.ai.azure.com/v1
  priority: 2
  tier: standard
  display_prefix: GitHub
  model_display: GPT-4o
  access_tier: free
  rpm: 15                                 # Low-tier models: 15 RPM
  rpd: 150                                # Low-tier: 150 RPD
  tpm: null                               # Per-request limits only
  max_tokens_input: 8000
  max_tokens_output: 4000
```

**Rate Limits (Free):**
- Low-tier (Llama, Mistral, Phi): 15 RPM / 150 RPD / 8K in / 4K out
- High-tier (GPT-4o, o3, Grok-3): 10 RPM / 50 RPD / 8K in / 4K out

**Best Use Cases:**
- **Only free path to GPT-4o, o3, Grok-3** without paying OpenAI/xAI directly
- Testing closed models at zero cost
- Comparing Grok-3 output against Claude/Gemini

**Why Add:** Unique value — free access to frontier closed models.

**Key:** `GITHUB_TOKEN` from GitHub settings (Settings → Developer settings → Personal access tokens).

---

### 3. Mistral (La Plateforme)
**Integration Effort:** ⭐⭐ (10 min)  
**Provider Type:** OpenAI-compatible  
**Status:** Not connected

```yaml
mistral:
  type: openai
  model: mistral-large-latest              # or: mistral-small, codestral, mistral-medium
  api_key: ${MISTRAL_API_KEY}
  base_url: https://api.mistral.ai/v1
  priority: 3
  tier: standard
  display_prefix: Mistral
  model_display: Mistral-Large
  access_tier: free
  rpm: null                               # No published RPM, estimated ~500K TPM
  rpd: null
  tpm: 500000                             # ~1B tokens/month implied
  tpd: null                               # No daily cap
```

**Rate Limits (Free):**
- Phone verification only (no card)
- ~500K TPM / ~1B tokens/month (estimated)
- No published per-minute limit

**Best Use Cases:**
- EU data residency (hosted in France)
- GDPR-compliant completions
- Mistral Large as local Claude alternative

**Why Add:** EU alternative. Phone verify only. Generous monthly allowance.

**Key:** `MISTRAL_API_KEY` from [console.mistral.ai](https://console.mistral.ai) — phone verify.

---

### 4. SambaNova (Largest Free Model)
**Integration Effort:** ⭐⭐ (10 min)  
**Provider Type:** OpenAI-compatible  
**Status:** Not connected

```yaml
sambanova:
  type: openai
  model: llama-3.1-405b                   # **Only provider offering 405B free**
  api_key: ${SAMBANOVA_API_KEY}
  base_url: https://api.sambanova.ai/v1
  priority: 4
  tier: deep
  display_prefix: SambaNova
  model_display: Llama-405B
  access_tier: free
  rpm: 10                                 # 405B = 10 RPM
  rpd: 1000                               # 1K RPD for large models
  tpm: null
  tpd: null
```

**Rate Limits (Free):**
- 10 RPM / 1K RPD (405B model)
- Smaller models: up to 30 RPM
- $5 one-time signup credits (30-day expiry)

**Best Use Cases:**
- Deep reasoning (405B parameter model)
- Cost-free complex problem solving
- Testing 405B capability vs Claude

**Why Add:** Only free 405B in the world. RDU hardware (custom). Great for deep tier fallback.

**Key:** `SAMBANOVA_API_KEY` from [cloud.sambanova.ai](https://cloud.sambanova.ai) — no card.

---

### 5. OpenRouter (Provider Gateway)
**Integration Effort:** ⭐⭐⭐ (15 min, routing rules)  
**Provider Type:** Gateway (200+ providers, 1 key)  
**Status:** Not connected

```yaml
openrouter:
  type: openai
  model: openai/gpt-4o                    # or: deepseek/deepseek-r1:free, qwen/qwen3-coder-480b:free
  api_key: ${OPENROUTER_API_KEY}
  base_url: https://openrouter.ai/api/v1
  priority: 5
  tier: standard
  display_prefix: Router
  model_display: GPT-4o
  access_tier: mixed                      # Free models + paid
  rpm: 20                                 # Before deposit
  rpd: 1000                               # After $10 deposit
  tpm: null
  tpd: null
  routes:
    - model: deepseek/deepseek-r1:free    # Free variant
      rpm: 20
      rpd: 1000
    - model: qwen/qwen3-coder-480b:free   # Free variant
      rpm: 20
      rpd: 500
```

**Rate Limits (Free):**
- 20 RPM / 50 RPD before $10 deposit
- 20 RPM / 1K RPD after $10 deposit
- Free model variants: DeepSeek R1 :free, Qwen3-Coder-480B :free, Llama-4 :free

**Best Use Cases:**
- **Single API key to 200+ models across 50 providers**
- Automatic fallback if primary provider goes down
- Load balancing across providers
- Cost optimization (picks cheapest per-model)

**Why Add:** Gateway abstraction = resilience. Adds diversity without managing 20 API keys.

**Key:** `OPENROUTER_API_KEY` from [openrouter.ai](https://openrouter.ai).

---

## TIER 2: Add Second (Free or Low-Cost, Specialized)

### 6. Cohere (Embeddings + Rerank)
**Integration Effort:** ⭐⭐ (native SDK)  
**Provider Type:** Native SDK (not OpenAI-compat)  
**Status:** Not connected

```yaml
cohere:
  type: cohere
  model: command-r-plus                   # or: command-r, command-a-plus
  api_key: ${COHERE_API_KEY}
  priority: 6
  tier: standard
  display_prefix: Cohere
  model_display: Command-R+
  access_tier: free
  rpm: 20
  rpd: null
  tpd: 1000                               # **1K API calls/month hard cap**
  specialized: true                       # RAG/embed/rerank only
```

**Rate Limits (Free):**
- 20 RPM (chat)
- **1,000 API calls/month total** (tight constraint)
- 10 RPM (rerank)
- 2,000 inputs/min (embed)

**Best Use Cases:**
- **Reranking** (best-in-class for RAG reranking)
- Document embeddings
- Small-scale retrieval workflows

**Why Add:** 1K calls/month is tight but rerank is valuable for RAG. Separate it into specialist routing.

**Key:** `COHERE_API_KEY` from [cohere.com](https://cohere.com) — no card.

---

### 7. NVIDIA NIM (Scientific Domains)
**Integration Effort:** ⭐⭐⭐ (routing rules for domains)  
**Provider Type:** OpenAI-compatible  
**Status:** Not connected

```yaml
nvidia_nim:
  type: openai
  model: meta/llama-3.1-70b-instruct      # Standard model
  api_key: ${NVIDIA_NIM_API_KEY}
  base_url: https://integrate.api.nvidia.com/v1
  priority: 7
  tier: standard
  display_prefix: NVIDIA
  model_display: Llama-70B
  access_tier: credits
  rpm: 40
  rpd: null
  routes:
    - path: biology                       # domain-specific models
      models: [nvidia/bio-llm]
    - path: climate
      models: [nvidia/climate-llm]
    - path: safety
      models: [nvidia/safety-checker]
```

**Rate Limits (Free):**
- 1,000 API credits on signup (request 4,000 more)
- 40 RPM
- Credits expire — not persistent free

**Best Use Cases:**
- **Biology/genomics/climate-specific models** (91+ domain endpoints)
- Scientific research routing
- Docker self-hosted NIM containers (separate from API)

**Why Add:** Breadth of models. Scientific domain specialization.

**Key:** `NVIDIA_NIM_API_KEY` from [api.nvidia.com/nim](https://api.nvidia.com/nim).

---

### 8. DeepInfra (Cheapest Paid Fallback)
**Integration Effort:** ⭐ (OpenAI-compat)  
**Provider Type:** OpenAI-compatible  
**Status:** Not connected

```yaml
deepinfra:
  type: openai
  model: meta-llama/Llama-3-70b-instruct
  api_key: ${DEEPINFRA_API_KEY}
  base_url: https://api.deepinfra.com/v1/openai
  priority: 8
  tier: standard
  display_prefix: DeepInfra
  model_display: Llama-70B
  access_tier: paid                       # $5 free credits, then paid
  rpm: null
  rpd: null
  pricing:
    per_1m_tokens: 0.0435                 # Among cheapest in market
```

**Rate Limits:**
- $5 startup credits (no card required)
- 50+ models available
- Pricing: $0.02–0.06/M tokens (extremely competitive)

**Best Use Cases:**
- Cost optimization fallback
- High-volume batch processing
- Cheapest paid option after free credits expire

**Why Add:** When free tiers are exhausted, cheapest option to continue.

**Key:** `DEEPINFRA_API_KEY` from [deepinfra.com](https://deepinfra.com).

---

### 9. HuggingFace Inference API (OSS Exploration)
**Integration Effort:** ⭐⭐ (dynamic rate limits)  
**Provider Type:** Native SDK + OpenAI-compat options  
**Status:** Not connected

```yaml
huggingface:
  type: openai
  model: mistralai/Mistral-7B-Instruct-v0.3
  api_key: ${HF_API_KEY}
  base_url: https://api-inference.huggingface.co/v1
  priority: 9
  tier: nano
  display_prefix: HF
  model_display: Mistral-7B
  access_tier: free
  rpm: null                               # Dynamic, not published
  rpd: null
  tpd: null
  note: "Free tier has undocumented rate limits (~hundreds req/hr)"
```

**Rate Limits (Free):**
- Persistent free account
- ~hundreds requests/hour (dynamic, undocumented)
- Cold start latency on unpopular models
- PRO ($9/mo): 2M credits/month

**Best Use Cases:**
- Exploring open-source models
- Cold-start acceptable tasks
- 300+ community models available

**Why Add:** Breadth of OSS models. Accept latency for discovery/testing.

**Key:** `HF_API_KEY` from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

---

### 10. Cloudflare Workers AI (Edge + Multimodal)
**Integration Effort:** ⭐⭐⭐ (proprietary bindings)  
**Provider Type:** Proprietary (not OpenAI-compat)  
**Status:** Not connected

```yaml
cloudflare_workers:
  type: cloudflare
  models:
    text: "@cf/meta/llama-3.3-70b-instruct-fp8"
    image: "@cf/black-forest-labs/flux-1-schnell"
    audio_tts: "@cf/openai/whisper"
  api_key: ${CLOUDFLARE_API_TOKEN}
  account_id: ${CLOUDFLARE_ACCOUNT_ID}
  priority: 10
  tier: standard
  display_prefix: CF
  access_tier: free
  neurons_per_day: 10000                  # Cloudflare's compute unit
```

**Rate Limits (Free):**
- 10,000 neurons/day (Cloudflare's compute unit for cost)
- 300+ global edge PoPs
- Multimodal: text, image (FLUX), audio (Whisper, TTS)

**Best Use Cases:**
- Image generation (FLUX.2 free)
- Text-to-speech
- Edge inference (latency-sensitive)
- Multimodal workflows

**Why Add:** Only provider covering text + image + audio at edge with free tier. Unique value.

**Key:** `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` from Cloudflare dashboard.

---

## TIER 3: Add Later (Paid, Niche, or Declining Value)

| Provider | Type | Free Tier | OpenAI Compat | Use Case | Why Wait |
|---|---|---|---|---|
| **Together AI** | Cloud | ❌ None ($5 min) | Yes | General inference | No free tier; use DeepInfra instead (cheaper) |
| **Perplexity** | Cloud | Trial only | No | Search-aware LLM | Trial-based; expensive for free tier |
| **Fireworks AI** | Cloud | Small credits | Yes | Structured output | Credits-only; DeepInfra is cheaper |
| **xAI (Grok)** | Cloud | $25 credits | Yes | Grok reasoning | Not persistent; use GitHub Models instead |
| **AI21 Labs** | Cloud | $10 trial | No | Long-context | Jamba model interesting but trial-based |
| **Voyage AI** | Embeddings | Free tier | No | Code embeddings | Niche; add when building RAG layer |
| **Eden AI** | Gateway | Free tier | Yes | Multi-provider routing | Similar to OpenRouter; lower adoption |

---

## Integration Priority & Timeline

### Week 1 (Quick Wins — 30 min total)
1. Google AI Studio (drop-in, highest throughput)
2. GitHub Models (unique frontier access)
3. Mistral (EU alternative, generous limits)

```bash
# Setup
export GOOGLE_API_KEY="..."          # From ai.google.dev
export GITHUB_TOKEN="ghp_..."        # From GitHub settings
export MISTRAL_API_KEY="..."         # From console.mistral.ai
```

### Week 2 (Expand Capability — 45 min)
4. SambaNova (deep tier alternative)
5. OpenRouter (gateway = resilience)

```bash
export SAMBANOVA_API_KEY="..."       # From cloud.sambanova.ai
export OPENROUTER_API_KEY="..."      # From openrouter.ai
```

### Week 3 (Specialization — 30 min)
6. Cohere (RAG embedding/rerank)
7. NVIDIA NIM (scientific domains)

```bash
export COHERE_API_KEY="..."          # From cohere.com
export NVIDIA_NIM_API_KEY="..."      # From api.nvidia.com/nim
```

### Week 4+ (Fallback + Exploration — as needed)
8. DeepInfra (cheap paid fallback)
9. HuggingFace (OSS exploration)
10. Cloudflare Workers AI (multimodal/edge)

---

## Rate Limit Monitoring Strategy

All providers in this list are OpenAI-compatible or have clear APIs. Implement per-provider tracking:

```python
# In src/core/rate_limiter.py

class ProviderRateLimiter:
    """Track RPM/RPD/TPM/TPD per provider against their limits."""
    
    def __init__(self, provider_config):
        self.provider = provider_config['display_prefix']
        self.rpm_limit = provider_config.get('rpm')
        self.rpd_limit = provider_config.get('rpd')
        self.tpm_limit = provider_config.get('tpm')
        self.tpd_limit = provider_config.get('tpd')
        self.requests_this_minute = 0
        self.requests_this_day = 0
        self.tokens_this_minute = 0
        self.tokens_this_day = 0
    
    def can_route(self, prompt_tokens: int) -> bool:
        """Check if request fits within provider's rate limits."""
        if self.rpm_limit and self.requests_this_minute >= self.rpm_limit:
            return False
        if self.rpd_limit and self.requests_this_day >= self.rpd_limit:
            return False
        if self.tpm_limit and self.tokens_this_minute + prompt_tokens > self.tpm_limit:
            return False
        if self.tpd_limit and self.tokens_this_day + prompt_tokens > self.tpd_limit:
            return False
        return True
    
    def record(self, prompt_tokens: int, completion_tokens: int):
        """Log usage after request completes."""
        total_tokens = prompt_tokens + completion_tokens
        self.requests_this_minute += 1
        self.requests_this_day += 1
        self.tokens_this_minute += total_tokens
        self.tokens_this_day += total_tokens
        
        # Emit warning if approaching limits
        if self.tpd_limit and self.tokens_this_day > self.tpd_limit * 0.8:
            logger.warning(f"{self.provider}: 80% of daily token limit reached")
```

---

## Routing Strategy (Token Burn Offload)

**Goal:** Use free/cheap tiers for 80% of requests. Save Claude for 20%.

```yaml
# config/routing_rules.yaml

patterns:
  # Triage (always fast, always cheap)
  - match: "classify|categorize|triage"
    provider: groq                        # Fast + free
    reason: "Fast classification, no reasoning needed"

  # High-volume standard tasks
  - match: "summarize|extract|reformat|translate"
    provider: gemini                      # 250K TPM free throughput
    reason: "High volume, standard completions"

  # Bulk work under rate limits
  - match: "batch|bulk|process"
    providers: [google_gemini, mistral]   # Rotate under limits
    reason: "Offload volume to free tier"

  # Reasoning-intensive (reserved for Claude)
  - match: "think|plan|architect|analyze|strategy|research"
    provider: claude                      # Deep tier
    reason: "Complex reasoning only"

  # Fallback chain
  - default: groq
    fallback: [cerebras, gemini, mistral, deepinfra]
    reason: "Auto-route if primary busy"

  # Domain-specific
  - match: "biology|genomics|climate"
    provider: nvidia_nim                  # Domain models
    reason: "Specialized scientific endpoints"

  # Long-context
  - match: "large context|long text|full document"
    provider: gemini                      # 128K context free
    reason: "Handles 128K context window"

  # EU compliance
  - match: "gdpr|eu|europe|personal data"
    provider: mistral                     # France-hosted
    reason: "GDPR-compliant data residency"
```

---

## Cost Savings Projection

Assuming 1M tokens/day across the harness:

| Scenario | Provider Mix | Cost/Month | Claude Savings |
|---|---|---|---|
| **All Claude** | 100% Opus | ~$1,500–2,000 | — |
| **Current** | Groq (5%) + Cerebras (20%) + Claude (75%) | ~$1,100–1,400 | 25–30% |
| **Tier 1 added** | Free tiers (70%) + Claude (30%) | ~$300–500 | **70–75%** |
| **All providers** | Free (80%) + Paid fallback (15%) + Claude (5%) | ~$50–150 | **92–97%** |

**Key:** Free tier providers are bottleneck-gated (rate limits), not token-gated. Monitor them at saturation, not cost.

---

## Implementation Checklist

- [ ] Add Tier 1 providers (Google, GitHub, Mistral, SambaNova, OpenRouter) to `providers.yaml`
- [ ] Create `ProviderRateLimiter` in `src/core/rate_limiter.py`
- [ ] Implement daily/monthly rate limit tracking in triage classifier
- [ ] Add per-provider heartbeat display (show RPM/RPD usage %)
- [ ] Update routing rules to spread load across free tiers
- [ ] Add fallback chain: primary → fast → cheap → paid
- [ ] Document in-channel how to add new API keys (Settings → env vars)
- [ ] Build provider health dashboard (requests/minute, token throughput)

