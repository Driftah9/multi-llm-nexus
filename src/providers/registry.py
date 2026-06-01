"""
Provider registry — the single source of truth for every AI provider Nexus supports.

Defines: provider catalog, model→tier mapping, required packages, env vars,
access modes, tool support level, and base URLs for OpenAI-compatible providers.

The wizard and router both read from here — nothing is hardcoded elsewhere.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# Tier definitions
# ─────────────────────────────────────────────

TIER_NANO     = "nano"       # triage / classification — fastest, cheapest
TIER_STANDARD = "standard"   # primary workload — best value, tool use
TIER_DEEP     = "deep"       # complex reasoning / escalation — most capable


# ─────────────────────────────────────────────
# Provider catalog entry
# ─────────────────────────────────────────────

@dataclass
class ProviderDef:
    type_id: str                        # key used in providers.yaml
    display_name: str                   # shown to the operator
    description: str
    provider_class: str                 # class name in this package
    access_modes: list[str]             # Access mode(s) this provider supports:
                                        #   "subscription_cli" — official CLI drives API via flat subscription (Claude Code today)
                                        #   "api_key"          — REST API with a secret key, pay-per-token or prepaid credits
                                        #   "local"            — runs on the operator's hardware, no external calls
                                        #   "cloud_account"    — needs a cloud provider account (AWS, GCP, Azure)
                                        # Note: consumer web subscriptions (ChatGPT Plus, X Premium, Gemini Advanced)
                                        # do NOT provide programmatic access and are NOT represented here.
    env_vars: list[str]                 # required env vars (empty = none)
    packages: list[str]                 # pip packages to install
    tool_support: str                   # "mcp" | "function_call" | "native" | "partial" | "none"
    model_discovery: str                # "query_api" | "static" | "local_query"
    base_url: Optional[str] = None      # for OpenAI-compatible providers
    free_tier: bool = False
    notes: str = ""
    models: dict[str, str] = field(default_factory=dict)   # model_name → tier
    capabilities: list[str] = field(default_factory=list)  # capability tags for smart routing:
                                                            #   code, search, reasoning, rag,
                                                            #   vision, local, eu_residency
                                                            # Empty = general purpose (tier-only routing applies)
                                                            # Populated: capability-router can prefer this provider
                                                            # when triage detects a matching task type.


# ─────────────────────────────────────────────
# Full provider catalog
# ─────────────────────────────────────────────

PROVIDERS: dict[str, ProviderDef] = {

    # ── Anthropic ──────────────────────────────────────────────────────────

    "claude_code": ProviderDef(
        type_id="claude_code",
        display_name="Anthropic / Claude  (CLI — subscription)",
        description="Claude via Claude Code CLI. Full MCP tool ecosystem. No API key needed.",
        provider_class="ClaudeCodeProvider",
        access_modes=["subscription_cli"],
        env_vars=[],
        packages=[],
        tool_support="mcp",
        model_discovery="static",
        notes="Requires `claude` CLI on PATH and valid auth (claude login). "
              "MCP servers (HA, Playwright, Obsidian, etc.) attach here, not to API providers.",
        models={
            "claude-haiku-4-5-20251001": TIER_NANO,
            "claude-sonnet-4-6":         TIER_STANDARD,
            "claude-opus-4-7":           TIER_DEEP,
        },
    ),

    "anthropic": ProviderDef(
        type_id="anthropic",
        display_name="Anthropic / Claude  (API key)",
        description="Direct Anthropic API. Prompt caching, extended thinking, no CLI required.",
        provider_class="AnthropicProvider",
        access_modes=["api_key"],
        env_vars=["ANTHROPIC_API_KEY"],
        packages=["anthropic"],
        tool_support="native",
        model_discovery="static",
        notes="Prompt caching is enabled by default — saves cost on long system prompts.",
        models={
            "claude-haiku-4-5-20251001": TIER_NANO,
            "claude-sonnet-4-6":         TIER_STANDARD,
            "claude-opus-4-7":           TIER_DEEP,
        },
    ),

    # ── OpenAI ─────────────────────────────────────────────────────────────

    "openai": ProviderDef(
        type_id="openai",
        display_name="OpenAI",
        description="GPT-4o, o3, and the full OpenAI model family.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["OPENAI_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        notes="ChatGPT Plus subscription does NOT give API access — a separate API key is required.",
        models={
            "gpt-4o-mini":          TIER_NANO,
            "gpt-4o-mini-2024-07-18": TIER_NANO,
            "gpt-4o":               TIER_STANDARD,
            "gpt-4o-2024-11-20":    TIER_STANDARD,
            "gpt-4-turbo":          TIER_STANDARD,
            "o1-mini":              TIER_STANDARD,
            "o1":                   TIER_DEEP,
            "o3":                   TIER_DEEP,
            "o3-mini":              TIER_STANDARD,
        },
    ),

    # ── Google Gemini ──────────────────────────────────────────────────────

    "gemini": ProviderDef(
        type_id="gemini",
        display_name="Google Gemini  (AI Studio)",
        description="Gemini Flash, Pro, and Ultra via Google AI Studio. Free tier available.",
        provider_class="GeminiProvider",
        access_modes=["api_key"],
        env_vars=["GOOGLE_API_KEY"],
        packages=["google-generativeai"],
        tool_support="native",
        model_discovery="query_api",
        free_tier=True,
        notes="Get a free key at aistudio.google.com — no billing required for Flash.",
        models={
            "gemini-2.0-flash":          TIER_NANO,
            "gemini-2.0-flash-lite":     TIER_NANO,
            "gemini-1.5-flash":          TIER_NANO,
            "gemini-1.5-flash-8b":       TIER_NANO,
            "gemini-1.5-pro":            TIER_STANDARD,
            "gemini-2.0-pro-exp":        TIER_STANDARD,
            "gemini-2.0-flash-thinking-exp": TIER_DEEP,
        },
    ),

    "vertex_ai": ProviderDef(
        type_id="vertex_ai",
        display_name="Google Vertex AI  (GCP)",
        description="Gemini models via Google Cloud. GCP project + billing required.",
        provider_class="GeminiProvider",
        access_modes=["cloud_account"],
        env_vars=["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_REGION"],
        packages=["google-generativeai", "google-cloud-aiplatform"],
        tool_support="native",
        model_discovery="query_api",
        notes="Uses Application Default Credentials (gcloud auth). "
              "Different billing path from AI Studio.",
        models={
            "gemini-1.5-flash":  TIER_NANO,
            "gemini-1.5-pro":    TIER_STANDARD,
            "gemini-2.0-flash-thinking-exp": TIER_DEEP,
        },
    ),

    # ── Groq ───────────────────────────────────────────────────────────────

    "groq": ProviderDef(
        type_id="groq",
        display_name="Groq",
        description="Open-source models at ~500 tok/s. Best cloud triage option.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["GROQ_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://api.groq.com/openai/v1",
        free_tier=True,
        notes="Groq hosts Llama, Mistral, Gemma, DeepSeek — not their own models. "
              "Free tier is generous. Ideal for triage.",
        models={
            "llama-3.1-8b-instant":           TIER_NANO,
            "gemma2-9b-it":                   TIER_NANO,
            "llama-3.3-70b-versatile":        TIER_STANDARD,
            "mixtral-8x7b-32768":             TIER_STANDARD,
            "deepseek-r1-distill-llama-70b":  TIER_DEEP,
            "llama-3.3-70b-specdec":          TIER_DEEP,
        },
    ),

    # ── Mistral ────────────────────────────────────────────────────────────

    "mistral": ProviderDef(
        type_id="mistral",
        display_name="Mistral AI",
        description="Mistral Small, Medium, Large. EU-hosted, GDPR-friendly.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["MISTRAL_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://api.mistral.ai/v1",
        capabilities=["eu_residency"],
        notes="Hosted in EU. Use when data residency requirements apply.",
        models={
            "mistral-small-latest":  TIER_NANO,
            "open-mistral-7b":       TIER_NANO,
            "mistral-medium-latest": TIER_STANDARD,
            "open-mixtral-8x7b":     TIER_STANDARD,
            "mistral-large-latest":  TIER_DEEP,
            "open-mixtral-8x22b":    TIER_DEEP,
        },
    ),

    # ── DeepSeek ───────────────────────────────────────────────────────────

    "deepseek": ProviderDef(
        type_id="deepseek",
        display_name="DeepSeek",
        description="DeepSeek V3 (chat) and R1 (reasoning). Extremely low pricing.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["DEEPSEEK_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="static",
        base_url="https://api.deepseek.com/v1",
        capabilities=["reasoning", "code"],
        notes="deepseek-chat (V3) is ~$0.01/1M tokens. deepseek-reasoner (R1) is chain-of-thought.",
        models={
            "deepseek-chat":     TIER_STANDARD,
            "deepseek-reasoner": TIER_DEEP,
        },
    ),

    # ── xAI / Grok ─────────────────────────────────────────────────────────

    "xai": ProviderDef(
        type_id="xai",
        display_name="xAI / Grok",
        description="Grok models via xAI API. X Premium subscription ≠ API access.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["XAI_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://api.x.ai/v1",
        notes="X/Twitter subscription does not include API. Separate key from console.x.ai.",
        models={
            "grok-2-mini":  TIER_NANO,
            "grok-2":       TIER_STANDARD,
            "grok-3":       TIER_DEEP,
            "grok-3-mini":  TIER_NANO,
        },
    ),

    # ── Cohere ─────────────────────────────────────────────────────────────

    "cohere": ProviderDef(
        type_id="cohere",
        display_name="Cohere",
        description="Command R and R+. Best for RAG/retrieval-heavy workloads.",
        provider_class="CohereProvider",
        access_modes=["api_key"],
        env_vars=["COHERE_API_KEY"],
        packages=["cohere"],
        tool_support="native",
        model_discovery="static",
        free_tier=True,
        capabilities=["rag"],
        notes="Strong RAG grounding. Not a general-purpose primary — use as specialist.",
        models={
            "command-r":      TIER_STANDARD,
            "command-r-plus": TIER_DEEP,
            "command-light":  TIER_NANO,
        },
    ),

    # ── Together.ai ────────────────────────────────────────────────────────

    "together": ProviderDef(
        type_id="together",
        display_name="Together.ai",
        description="50+ open-source models (Llama, Mistral, Qwen, DBRX, etc.).",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["TOGETHER_API_KEY"],
        packages=["openai"],
        tool_support="partial",
        model_discovery="query_api",
        base_url="https://api.together.xyz/v1",
        notes="Tool calling is model-dependent — check model card before relying on it.",
        models={
            "meta-llama/Llama-3.2-3B-Instruct-Turbo":  TIER_NANO,
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": TIER_NANO,
            "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": TIER_STANDARD,
            "mistralai/Mistral-7B-Instruct-v0.3": TIER_NANO,
            "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": TIER_DEEP,
        },
    ),

    # ── Fireworks.ai ───────────────────────────────────────────────────────

    "fireworks": ProviderDef(
        type_id="fireworks",
        display_name="Fireworks.ai",
        description="Fast open model hosting. FireLLaVA, Llama, Mixtral.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["FIREWORKS_API_KEY"],
        packages=["openai"],
        tool_support="partial",
        model_discovery="query_api",
        base_url="https://api.fireworks.ai/inference/v1",
        models={
            "accounts/fireworks/models/llama-v3p1-8b-instruct":  TIER_NANO,
            "accounts/fireworks/models/llama-v3p1-70b-instruct": TIER_STANDARD,
            "accounts/fireworks/models/mixtral-8x22b-instruct":  TIER_DEEP,
        },
    ),

    # ── Perplexity ─────────────────────────────────────────────────────────

    "perplexity": ProviderDef(
        type_id="perplexity",
        display_name="Perplexity",
        description="Online models with live web search built in.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["PERPLEXITY_API_KEY"],
        packages=["openai"],
        tool_support="none",
        model_discovery="static",
        base_url="https://api.perplexity.ai",
        capabilities=["search"],
        notes="Models have web search baked in — use as a research specialist, not primary.",
        models={
            "llama-3.1-sonar-small-128k-online": TIER_NANO,
            "llama-3.1-sonar-large-128k-online": TIER_STANDARD,
            "llama-3.1-sonar-huge-128k-online":  TIER_DEEP,
        },
    ),

    # ── Hugging Face ───────────────────────────────────────────────────────

    "huggingface": ProviderDef(
        type_id="huggingface",
        display_name="Hugging Face Inference",
        description="Serverless Inference API. Free tier on popular models.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["HF_TOKEN"],
        packages=["openai"],
        tool_support="partial",
        model_discovery="query_api",
        base_url="https://api-inference.huggingface.co/v1",
        free_tier=True,
        notes="Serverless inference — cold start latency possible. Not for triage.",
        models={},
    ),

    # ── Cerebras ───────────────────────────────────────────────────────────

    "cerebras": ProviderDef(
        type_id="cerebras",
        display_name="Cerebras",
        description="Wafer-scale chip inference. Very fast on supported models.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["CEREBRAS_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://api.cerebras.ai/v1",
        models={
            "llama3.1-8b":  TIER_NANO,
            "llama3.1-70b": TIER_STANDARD,
        },
    ),

    # ── Amazon Bedrock ─────────────────────────────────────────────────────

    "bedrock": ProviderDef(
        type_id="bedrock",
        display_name="Amazon Bedrock",
        description="AWS-hosted Claude, Llama, Mistral, Titan under one AWS bill.",
        provider_class="BedrockProvider",
        access_modes=["cloud_account"],
        env_vars=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
        packages=["boto3"],
        tool_support="native",
        model_discovery="query_api",
        notes="AWS_PROFILE also accepted for named profiles. Billing via AWS account. "
              "Bedrock model IDs include version suffixes that change with model updates — "
              "verify current IDs at docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html.",
        models={
            "anthropic.claude-3-haiku-20240307-v1:0":     TIER_NANO,
            "anthropic.claude-3-5-sonnet-20241022-v2:0":  TIER_STANDARD,
            "anthropic.claude-3-opus-20240229-v1:0":      TIER_DEEP,
            "meta.llama3-1-8b-instruct-v1:0":             TIER_NANO,
            "meta.llama3-1-70b-instruct-v1:0":            TIER_STANDARD,
            "meta.llama3-1-405b-instruct-v1:0":           TIER_DEEP,
            "mistral.mistral-7b-instruct-v0:2":           TIER_NANO,
            "mistral.mixtral-8x7b-instruct-v0:1":         TIER_STANDARD,
            "mistral.mistral-large-2402-v1:0":            TIER_DEEP,
            "amazon.titan-text-lite-v1":                  TIER_NANO,
            "amazon.titan-text-express-v1":               TIER_STANDARD,
        },
    ),

    # ── Azure OpenAI ───────────────────────────────────────────────────────

    "azure_openai": ProviderDef(
        type_id="azure_openai",
        display_name="Azure OpenAI",
        description="OpenAI models via Azure. Enterprise compliance, data residency.",
        provider_class="OpenAIProvider",
        access_modes=["cloud_account"],
        env_vars=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_VERSION"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="static",
        notes="base_url is your Azure endpoint. Deployment name replaces model name.",
        models={},
    ),

    # ── Ollama ─────────────────────────────────────────────────────────────

    "ollama": ProviderDef(
        type_id="ollama",
        display_name="Ollama  (local)",
        description="Local models. Zero cost, nothing leaves the network.",
        provider_class="OllamaProvider",
        access_modes=["local"],
        env_vars=[],
        packages=["httpx"],
        tool_support="partial",
        model_discovery="local_query",
        capabilities=["local"],
        notes="Install at ollama.ai. Run `ollama pull <model>` before use. "
              "phi4-mini (~3.8B, ~2.5GB RAM) is the recommended nano/triage model.",
        models={
            "phi4-mini":       TIER_NANO,
            "llama3.2:1b":     TIER_NANO,
            "llama3.2:3b":     TIER_NANO,
            "phi3:mini":       TIER_NANO,
            "gemma2:2b":       TIER_NANO,
            "llama3.1:8b":     TIER_STANDARD,
            "mistral:7b":      TIER_STANDARD,
            "gemma2:9b":       TIER_STANDARD,
            "codellama:7b":    TIER_STANDARD,
            "qwen2.5:7b":      TIER_STANDARD,
            "llama3.1:70b":    TIER_DEEP,
            "qwen2.5:32b":     TIER_DEEP,
            "mixtral:8x7b":    TIER_DEEP,
            "llama3.1:405b":   TIER_DEEP,
        },
    ),

    # ── LM Studio ──────────────────────────────────────────────────────────

    "lm_studio": ProviderDef(
        type_id="lm_studio",
        display_name="LM Studio  (local)",
        description="Local GUI model manager with OpenAI-compatible API server.",
        provider_class="OpenAIProvider",
        access_modes=["local"],
        env_vars=[],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="http://localhost:1234/v1",
        notes="Start the LM Studio server before use. Models vary by what user has downloaded.",
        models={},
    ),

    # ── vLLM ───────────────────────────────────────────────────────────────

    "vllm": ProviderDef(
        type_id="vllm",
        display_name="vLLM  (local, multi-vendor GPU)",
        description=(
            "High-throughput LLM inference server. Supports NVIDIA (CUDA), "
            "AMD (ROCm), and Intel (XPU/SYCL) GPUs from a single runtime. "
            "OpenAI-compatible API. The universal local inference backend — "
            "use when hardware is not NVIDIA or when you need multi-GPU tensor "
            "parallelism without NVLink."
        ),
        provider_class="VllmProvider",
        access_modes=["local"],
        env_vars=[],
        packages=["httpx"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="http://localhost:8000/v1",
        capabilities=["local", "code", "search"],
        notes=(
            "Install: pip install vllm. "
            "Run: vllm serve <model> --port 8000. "
            "Intel XPU: add --device xpu. "
            "AMD ROCm: install vllm with ROCm backend. "
            "Multi-GPU: add --tensor-parallel-size <N>. "
            "Unlike ik_llama.cpp (CUDA-only, MoE-optimized), vLLM covers all "
            "three major GPU vendors. Use ik_llama for NVIDIA MoE workloads "
            "(deferred expert loading), vLLM for everything else."
        ),
        models={
            "meta-llama/Llama-3.1-8B-Instruct":    TIER_NANO,
            "meta-llama/Llama-3.2-3B-Instruct":    TIER_NANO,
            "Qwen/Qwen2.5-7B-Instruct":            TIER_STANDARD,
            "Qwen/Qwen2.5-14B-Instruct":           TIER_STANDARD,
            "mistralai/Mistral-7B-Instruct-v0.3":   TIER_STANDARD,
            "meta-llama/Llama-3.1-70B-Instruct":    TIER_DEEP,
            "Qwen/Qwen2.5-72B-Instruct":            TIER_DEEP,
            "meta-llama/Llama-3.1-405B-Instruct":   TIER_DEEP,
        },
    ),

    # ── GitHub Models ──────────────────────────────────────────────────────
    #
    # GitHub's model marketplace. Free GitHub account gets limited daily quota;
    # GitHub Copilot subscription unlocks higher rate limits on the same endpoint.
    # OpenAI-compatible. Auth via GITHUB_TOKEN (personal access token).
    # Serves frontier models from multiple vendors under one key:
    # GPT-4o, Claude, Llama, Mistral, Phi, Cohere, and others.
    # The model list changes as GitHub adds or rotates vendor access.

    "github_models": ProviderDef(
        type_id="github_models",
        display_name="GitHub Models  (Copilot subscription or free)",
        description="Frontier models via GitHub's marketplace. Free tier + Copilot higher limits. Single GITHUB_TOKEN.",
        provider_class="OpenAIProvider",
        access_modes=["api_key", "subscription_cli"],
        env_vars=["GITHUB_TOKEN"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://models.inference.ai.azure.com",
        free_tier=True,
        notes=(
            "GITHUB_TOKEN = personal access token from github.com/settings/tokens. "
            "Free GitHub account: rate-limited to ~15-50 req/day depending on model. "
            "Copilot subscription (Individual $10/mo or Business $19/mo): higher limits on same endpoint. "
            "Model availability changes — query the API for current list. "
            "Good zero-cost path to GPT-4o, Claude, and Llama without separate API accounts."
        ),
        models={
            "gpt-4o-mini":                          TIER_NANO,
            "gpt-4o":                               TIER_STANDARD,
            "Meta-Llama-3.1-8B-Instruct":           TIER_NANO,
            "Meta-Llama-3.1-70B-Instruct":          TIER_STANDARD,
            "Meta-Llama-3.3-70B-Instruct":          TIER_STANDARD,
            "Mistral-small":                        TIER_NANO,
            "Mistral-large":                        TIER_DEEP,
            "Phi-3-mini-4k-instruct":               TIER_NANO,
            "Phi-3-medium-4k-instruct":             TIER_STANDARD,
            "Phi-4":                                TIER_STANDARD,
            "AI21-Jamba-1.5-Mini":                  TIER_NANO,
            "AI21-Jamba-1.5-Large":                 TIER_STANDARD,
        },
    ),

    # ── OpenRouter ─────────────────────────────────────────────────────────
    #
    # API aggregator routing to 100+ models across 30+ providers with a single key.
    # Not a subscription — pay-per-token credits (pre-purchase or on-demand).
    # Value: one key covers Claude, GPT, Gemini, Mistral, Llama, DeepSeek, and more.
    # Useful when operators want provider diversity without managing 10+ API accounts,
    # or want automatic fallback across providers.

    "openrouter": ProviderDef(
        type_id="openrouter",
        display_name="OpenRouter  (multi-provider aggregator)",
        description="100+ models via one API key. Automatic fallback across providers. Pay-per-token credits.",
        provider_class="OpenAIProvider",
        access_modes=["api_key"],
        env_vars=["OPENROUTER_API_KEY"],
        packages=["openai"],
        tool_support="function_call",
        model_discovery="query_api",
        base_url="https://openrouter.ai/api/v1",
        free_tier=False,
        notes=(
            "Model IDs use provider/model format: anthropic/claude-sonnet-4-6, "
            "openai/gpt-4o, google/gemini-2.0-flash, meta-llama/llama-3.3-70b-instruct. "
            "Automatic provider fallback available via :nitro/:floor suffixes. "
            "Free models available (check openrouter.ai/models — filter by free). "
            "Useful as a single entry point when operators want access to all providers "
            "without separate API accounts. Trade-off: adds a hop and slight latency."
        ),
        models={
            "google/gemini-2.0-flash":                         TIER_NANO,
            "meta-llama/llama-3.1-8b-instruct":                TIER_NANO,
            "anthropic/claude-haiku-4-5-20251001":             TIER_NANO,
            "openai/gpt-4o-mini":                              TIER_NANO,
            "openai/gpt-4o":                                   TIER_STANDARD,
            "anthropic/claude-sonnet-4-6":                     TIER_STANDARD,
            "google/gemini-2.0-pro-exp":                       TIER_STANDARD,
            "meta-llama/llama-3.3-70b-instruct":               TIER_STANDARD,
            "mistralai/mistral-large-latest":                  TIER_STANDARD,
            "deepseek/deepseek-chat":                          TIER_STANDARD,
            "anthropic/claude-opus-4-7":                       TIER_DEEP,
            "openai/o3":                                       TIER_DEEP,
            "deepseek/deepseek-r1":                            TIER_DEEP,
            "meta-llama/llama-3.1-405b-instruct":              TIER_DEEP,
        },
    ),
}


# ─────────────────────────────────────────────
# Model tier inference
# ─────────────────────────────────────────────

# Patterns for models not in the static catalog — infer tier by name/size
_NANO_PATTERNS = [
    r":1b\b", r":3b\b", r"-1b\b", r"-3b\b",
    r"mini", r"small", r"lite", r"tiny", r"instant",
    r"flash", r"haiku",
]
_STANDARD_PATTERNS = [
    r":7b\b", r":8b\b", r":9b\b", r":13b\b", r":14b\b",
    r"-7b\b", r"-8b\b", r"-13b\b",
    r"medium", r"sonnet",
]
_DEEP_PATTERNS = [
    r":30b\b", r":32b\b", r":34b\b", r":70b\b", r":72b\b", r":405b\b",
    r"-70b\b", r"-405b\b",
    r"large", r"ultra", r"opus", r"plus",
    r"reasoner", r"r1",
]


def infer_tier(model_name: str) -> str:
    """
    Infer nano/standard/deep tier from a model name when it's not in the catalog.
    Falls back to 'standard' if no pattern matches.
    """
    name = model_name.lower()
    for pattern in _NANO_PATTERNS:
        if re.search(pattern, name):
            return TIER_NANO
    for pattern in _DEEP_PATTERNS:
        if re.search(pattern, name):
            return TIER_DEEP
    for pattern in _STANDARD_PATTERNS:
        if re.search(pattern, name):
            return TIER_STANDARD
    return TIER_STANDARD


def get_tier(provider_type: str, model_name: str) -> str:
    """Return the tier for a given provider+model. Falls back to infer_tier."""
    pdef = PROVIDERS.get(provider_type)
    if pdef and model_name in pdef.models:
        return pdef.models[model_name]
    return infer_tier(model_name)


def get_models_for_tier(provider_type: str, tier: str) -> list[str]:
    """Return all known models for a provider at a given tier."""
    pdef = PROVIDERS.get(provider_type)
    if not pdef:
        return []
    return [m for m, t in pdef.models.items() if t == tier]


def recommended_triage_model(provider_type: str) -> Optional[str]:
    """Return the best triage (nano) model for a provider, if known."""
    candidates = get_models_for_tier(provider_type, TIER_NANO)
    return candidates[0] if candidates else None


def list_providers_by_access(access_mode: str) -> list[ProviderDef]:
    """
    Filter providers by access mode.
    Modes: subscription_cli, api_key, local, cloud_account
    Querying "subscription" matches both "subscription_cli" and any future subscription variants.
    """
    if access_mode == "subscription":
        return [p for p in PROVIDERS.values()
                if any(m.startswith("subscription") for m in p.access_modes)]
    return [p for p in PROVIDERS.values() if access_mode in p.access_modes]


def list_subscription_cli_providers() -> list[ProviderDef]:
    """
    Return providers with a CLI-driven subscription access path.
    These are presented differently in the wizard — no API key entry,
    instead the operator authenticates via the provider's own CLI tooling.
    Today this is only claude_code. The hook is here for when others follow.
    """
    return list_providers_by_access("subscription_cli")


def providers_with_free_tier() -> list[ProviderDef]:
    return [p for p in PROVIDERS.values() if p.free_tier]
