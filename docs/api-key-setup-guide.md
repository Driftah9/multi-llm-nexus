# API Key Setup Guide — Add Providers in 15 Minutes

**Goal:** Enable new free-tier providers one at a time. Each takes 2–3 minutes. No payment required for Tier 1.

---

## Prerequisites

If you want a couple of providers configured before you start, set them now (these two have persistent, no-card free tiers):
```bash
# Check current keys
env | grep -E "CEREBRAS|GROQ"

# Should show (placeholders until you add real keys):
# CEREBRAS_API_KEY=csk_xxxxxxxx
# GROQ_API_KEY=gsk_xxxxxxxx
```

If not set, add them to your project's `.env`:
```bash
echo "CEREBRAS_API_KEY=csk_xxxxxxxx" >> .env
echo "GROQ_API_KEY=gsk_xxxxxxxx" >> .env
```

---

## TIER 1: Add in Order (Persistent Free, 10 min total)

### 1. Google AI Studio (Gemini Flash)
**Time:** 2 min | **Card Required:** No | **Rate:** 10 RPM / 250K TPM

1. Go to [ai.google.dev](https://ai.google.dev)
2. Click **"Get API Key"** → **"Create API key in new project"**
3. Copy the key (format: `AIzaSy...`)
4. Add to `.env`:
   ```bash
   export GOOGLE_API_KEY="AIzaSy..."
   ```
5. Enable in `config/providers.yaml`:
   ```yaml
   google_gemini:
     enabled: true              # ← Change from false to true
   ```
6. Restart Nexus:
   ```bash
   source .venv/bin/activate && python -m src.main
   ```
7. Verify:
   ```bash
   curl -s http://localhost:8000/health | grep gemini
   ```

**Models Available Free:**
- `gemini-2.5-flash` (fast, 128K context)
- `gemini-2.5-flash-exp` (preview)
- `gemini-1.5-pro` (older, slower)

**Cost:** $0/month, forever.

---

### 2. GitHub Models (GPT-4o, o3, Grok-3 Free)
**Time:** 3 min | **Card Required:** No | **Rate:** 10–15 RPM

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **"Generate new token"** → **"Generate new token (classic)"**
3. Name: `ai-nexus`
4. Permissions: Check only **`api`** (read-only GitHub API access)
5. Click **"Generate token"** and copy (format: `ghp_...`)
6. Add to `.env`:
   ```bash
   export GITHUB_TOKEN="ghp_..."
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   github_models:
     enabled: true              # ← Change from false to true
   ```
8. Restart Nexus and verify.

**Models Available Free:**
- `gpt-4o` (OpenAI's GPT-4 level, free)
- `gpt-4.1` (GPT-4 Turbo)
- `o3` (OpenAI's reasoning model, free)
- `grok-3` (xAI's Grok, free)
- `claude-3.5-sonnet` (Anthropic, free)
- `llama-3.2-11b` (Meta, 15 RPM / 150 RPD for low-tier)

**Cost:** $0/month, forever.

**Note:** Each model has different rate limits. GPT-4o is 10 RPM / 50 RPD. Llama-3.2-11b is 15 RPM / 150 RPD.

---

### 3. Mistral Large (EU-Hosted, GDPR)
**Time:** 2 min | **Card Required:** No (phone verify) | **Rate:** ~500K TPM

1. Go to [console.mistral.ai](https://console.mistral.ai)
2. Click **"Sign Up"** and enter email
3. Verify email
4. Phone verification required (one-time)
5. In **"API Keys"** section, click **"Generate New Key"**
6. Copy the key
7. Add to `.env`:
   ```bash
   export MISTRAL_API_KEY="xxxxxxxx"
   ```
8. Enable in `config/providers.yaml`:
   ```yaml
   mistral:
     enabled: true              # ← Change from false to true
   ```
9. Restart Nexus and verify.

**Models Available Free:**
- `mistral-large-latest` (flagship)
- `mistral-small` (faster)
- `codestral` (code-focused)
- `mistral-medium`

**Cost:** $0/month, forever (with phone verify).

---

### 4. SambaNova (Llama 405B — Only Free 405B)
**Time:** 3 min | **Card Required:** No | **Rate:** 10 RPM / 1K RPD

1. Go to [cloud.sambanova.ai](https://cloud.sambanova.ai)
2. Click **"Sign Up"** (email + password)
3. Verify email
4. In **"API Keys"**, click **"Create Key"**
5. Copy (format: `sb-...`)
6. Add to `.env`:
   ```bash
   export SAMBANOVA_API_KEY="sb-..."
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   sambanova:
     enabled: true              # ← Change from false to true
   ```
8. Restart Nexus and verify.

**Models Available Free:**
- `llama-3.1-405b` (largest free model anywhere)
- `llama-3.1-70b`
- `llama-3.1-8b`

**Cost:** $0/month, forever.

---

### 5. OpenRouter (Gateway to 200+ Models)
**Time:** 5 min | **Card Required:** No ($10 optional for higher limits) | **Rate:** 20 RPM / 50–1K RPD

1. Go to [openrouter.ai](https://openrouter.ai)
2. Click **"Sign In"** → **"Create Account"**
3. Verify email
4. In **"Keys"** (sidebar), click **"Create Key"**
5. Copy (format: `sk-or-v1-...`)
6. Add to `.env`:
   ```bash
   export OPENROUTER_API_KEY="sk-or-v1-..."
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   openrouter:
     enabled: true              # ← Change from false to true
   ```
8. Restart Nexus and verify.

**Models Available Free (or free variants):**
- `deepseek/deepseek-r1:free` (free variant)
- `qwen/qwen3-coder-480b:free` (free variant)
- `openai/gpt-4o` (paid via OpenRouter)
- 200+ total providers/models

**Cost:** $0/month (free models), or credit-based for paid models.

**Optional:** Deposit $10 to unlock higher rate limits (20 RPM / 1K RPD instead of 20 RPM / 50 RPD).

---

## TIER 2: Specialized Providers (Add Later)

### 6. Cohere (RAG: Embed + Rerank)
**Time:** 2 min | **Card Required:** No | **Rate:** 20 RPM / 1K calls/month

1. Go to [cohere.com](https://cohere.com)
2. Click **"Sign Up"** (email)
3. Verify email
4. In **"API Keys"**, click **"Generate Key"**
5. Copy the key
6. Add to `.env`:
   ```bash
   export COHERE_API_KEY="xxxxxxxx"
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   cohere:
     enabled: true
   ```

**Use Case:** Retrieval & reranking only (1K calls/month is tight, so don't use for general chat).

**Models:**
- `command-r-plus` (best for RAG)
- `command-r`
- `embed-english` (embeddings)
- `rerank-english` (reranking)

---

### 7. NVIDIA NIM (91+ Scientific Domain Models)
**Time:** 3 min | **Card Required:** No (1K credits included) | **Rate:** 40 RPM

1. Go to [api.nvidia.com/nim](https://api.nvidia.com/nim)
2. Click **"Sign Up"** (email)
3. Verify email
4. In **"API Keys"**, click **"Generate Key"**
5. Copy (format: `nvapi-...`)
6. Add to `.env`:
   ```bash
   export NVIDIA_NIM_API_KEY="nvapi-..."
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   nvidia_nim:
     enabled: true
   ```

**Use Case:** Scientific domain queries (biology, climate, genomics).

**Models:**
- `meta/llama-3.1-70b-instruct` (general)
- `nvidia/bio-llm` (biology)
- `nvidia/climate-llm` (climate)

**Cost:** 1K free credits, then requests require additional credits.

---

### 8. Cloudflare Workers AI (Edge + Multimodal)
**Time:** 5 min | **Card Required:** No | **Rate:** Neurons/day (proprietary unit)

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com)
2. Click **"Workers & Pages"** (left sidebar)
3. Click **"Create Application"** → **"Create a Worker"**
4. In **"Settings"** → **"API Token"**, create token:
   - Name: `ai-nexus`
   - Permissions: **"Account"** scope, **"Workers"** permission
5. Copy `account_id` from dashboard URL (format: `xxxxx`)
6. Add to `.env`:
   ```bash
   export CLOUDFLARE_API_TOKEN="v1.0xxxxxx..."
   export CLOUDFLARE_ACCOUNT_ID="xxxxx"
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   cloudflare_workers:
     enabled: true
   ```

**Use Case:** Image generation, text-to-speech, edge inference.

**Models:**
- `@cf/meta/llama-3.3-70b-instruct-fp8` (text)
- `@cf/black-forest-labs/flux-1-schnell` (image generation)
- `@cf/openai/whisper` (speech-to-text)

---

## TIER 3: Paid Fallback (Add Later)

### DeepInfra (Cheapest Paid Option)
**Time:** 2 min | **Card Required:** No initially ($5 free credits) | **Rate:** Unlimited

1. Go to [deepinfra.com](https://deepinfra.com)
2. Click **"Sign Up"**
3. Verify email
4. In **"API Keys"**, click **"Create Key"**
5. Copy (format: `xxxxx-xxxxx...`)
6. Add to `.env`:
   ```bash
   export DEEPINFRA_API_KEY="xxxxx..."
   ```
7. Enable in `config/providers.yaml`:
   ```yaml
   deepinfra:
     enabled: true
   ```

**Cost:** $5 free credits at signup, then $0.02–0.06/M tokens (cheapest market option).

**Use case:** When free tiers are exhausted. Save for fallback.

---

## Activation Checklist

```bash
# 1. Set all keys in .env
cat >> .env << 'EOF'
export GOOGLE_API_KEY="..."
export GITHUB_TOKEN="..."
export MISTRAL_API_KEY="..."
export SAMBANOVA_API_KEY="..."
export OPENROUTER_API_KEY="..."
EOF

# 2. Load env
source .env

# 3. Update config/providers.yaml — change enabled: false → enabled: true for each

# 4. Restart Nexus
source .venv/bin/activate
python -m src.main

# 5. Test routing
# Send a test message through any connected adapter:
#   "Test: @nexus summarize this"
# Check the heartbeat for new providers showing active
```

---

## Cost Tracker

| Provider | Free Tier | Monthly Cost | Total Tokens/Month |
|---|---|---|---|
| **Groq** | ✅ Persistent | $0 | 500K TPD |
| **Cerebras** | ✅ Persistent | $0 | 1M TPD |
| **Google Gemini** | ✅ Persistent | $0 | ~7.5M TPM (250K × 30 days) |
| **GitHub Models** | ✅ Persistent | $0 | ~1.5M (50 RPD × 30) |
| **Mistral** | ✅ Persistent | $0 | ~15B (500K TPM × 24h × 30) |
| **SambaNova** | ✅ Persistent | $0 | ~30M (1K RPD × 30 × 1K tokens avg) |
| **OpenRouter** | Partial (free models) | $0–variable | Varies per model |
| **Cohere** | ✅ Persistent (1K/mo) | $0 | ~500K (1K calls × avg 500 tokens) |
| **NVIDIA NIM** | Credits only (1K) | $0–variable | ~500K (1K credits ÷ $0.002 per token) |
| **DeepInfra** | $5 credits | $0–0.10+ | Variable (5M free tokens) |
| **HuggingFace** | ✅ Persistent (minimal) | $0 | ~100K (dynamic limit) |
| **Cloudflare** | ✅ Persistent (10K neu/day) | $0 | Variable by compute |
| **Claude** | API only | $3–15 per 1M tokens | Variable |
| **All free combined (Tier 1)** | 100% free | $0 | **~50M+ tokens/month** |

---

## Monitoring

After enabling each provider, watch the heartbeat:

```
Groq · Llama-8B · nano — triage 0.2s
Cerebras · Qwen3-235B · standard — working 3.1s
Google · Gemini-Flash · standard — working 1.8s
GitHub · GPT-4o · standard — working 2.1s
Mistral · Mistral-Large · standard — working 2.4s
```

---

## Troubleshooting

**Provider shows "unhealthy":**
```bash
# Check rate limits
curl -v https://api.cerebras.ai/v1/health

# Check API key format
echo $GOOGLE_API_KEY | head -c 20

# Check base_url
grep base_url config/providers.yaml | head -5
```

**Key not being read:**
```bash
# Verify .env is sourced
echo $GOOGLE_API_KEY

# If empty, reload
source .env && python -m src.main
```

**Rate limit hit:**
```bash
# Check in Nexus logs
tail -f logs/nexus.log | grep "rate_limit\|429\|quota"

# Switch to different provider in routing rules
# or wait for daily reset
```

---

## Next Steps

1. Enable **Google Gemini** today (2 min, 250K TPM free)
2. Add **GitHub Models** tomorrow (3 min, GPT-4o/o3 free)
3. Add **Mistral** when you need EU compliance
4. Add **SambaNova** for 405B capability
5. Use **OpenRouter** as a gateway once you have a deposit

**Total setup time:** ~15 minutes across Tier 1. No payment required.

