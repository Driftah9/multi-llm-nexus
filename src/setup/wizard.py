"""
Nexus install wizard — interactive first-time configuration.

Guides the operator through:
  1. Provider selection (which AI services do you have access to?)
  2. Per-provider configuration (subscription vs API key, test connection)
  3. Model discovery (query live APIs where supported, infer tiers)
  4. Role assignment (primary, triage, specialists)
  5. Config generation (writes providers.yaml + .env)

Run via: python -m src.setup.wizard
Or via: setup.sh (calls this automatically)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

# Resolve project root (two levels up from this file)
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"

from ..providers.registry import (
    PROVIDERS, TIER_NANO, TIER_STANDARD, TIER_DEEP,
    get_models_for_tier, get_tier, infer_tier, recommended_triage_model,
)


# ─────────────────────────────────────────────
# Terminal helpers
# ─────────────────────────────────────────────

def _c(text: str, code: str) -> str:
    """Wrap text in ANSI color if stdout is a TTY."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

def bold(t: str) -> str:   return _c(t, "1")
def green(t: str) -> str:  return _c(t, "32")
def yellow(t: str) -> str: return _c(t, "33")
def red(t: str) -> str:    return _c(t, "31")
def dim(t: str) -> str:    return _c(t, "2")
def cyan(t: str) -> str:   return _c(t, "36")

def header(title: str) -> None:
    width = 60
    print()
    print("─" * width)
    print(f"  {bold(title)}")
    print("─" * width)

def ask(prompt: str, default: str = "") -> str:
    display = f"{prompt}"
    if default:
        display += f" {dim(f'[{default}]')}"
    display += ": "
    try:
        val = input(display).strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

def ask_secret(prompt: str) -> str:
    import getpass
    try:
        return getpass.getpass(f"{prompt}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)

def ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({hint})").lower()
    if not val:
        return default
    return val.startswith("y")

def ask_choice(prompt: str, choices: list[str], default: int = 0) -> str:
    print(f"\n{prompt}")
    for i, c in enumerate(choices):
        marker = green("▶") if i == default else " "
        print(f"  {marker} ({i+1}) {c}")
    while True:
        raw = ask(f"Choice", str(default + 1))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print(red("  Invalid choice — enter a number from the list."))

def ask_multiselect(prompt: str, options: list[tuple[str, str]]) -> list[str]:
    """
    Multi-select from labeled options.
    options: list of (key, display_label)
    Returns selected keys.
    """
    selected: set[str] = set()
    print(f"\n{prompt}")
    print(dim("  Enter numbers separated by spaces/commas, or 'all', or 'none'."))
    for i, (key, label) in enumerate(options):
        print(f"  ({i+1}) {label}")
    while True:
        raw = ask("Select").lower().strip()
        if raw == "all":
            return [k for k, _ in options]
        if raw == "none":
            return []
        try:
            parts = raw.replace(",", " ").split()
            result = []
            for p in parts:
                idx = int(p) - 1
                if 0 <= idx < len(options):
                    result.append(options[idx][0])
                else:
                    raise ValueError()
            return result
        except (ValueError, IndexError):
            print(red("  Invalid input — use numbers from the list."))

def check_mark(ok: bool) -> str:
    return green("✓") if ok else red("✗")


# ─────────────────────────────────────────────
# Provider groups for the selection menu
# ─────────────────────────────────────────────

CLOUD_PROVIDERS = [
    ("anthropic",    "Anthropic / Claude — subscription (Claude Code CLI)"),
    ("claude_code",  "Anthropic / Claude — API key  (no CLI required)"),
    ("openai",       "OpenAI  (GPT-4o, o3)"),
    ("gemini",       "Google Gemini  (Flash, Pro — free tier available)"),
    ("groq",         "Groq  (fast open-source inference — free tier available)"),
    ("mistral",      "Mistral AI  (EU-hosted, GDPR-friendly)"),
    ("deepseek",     "DeepSeek  (V3 + R1 reasoning — very low cost)"),
    ("xai",          "xAI / Grok"),
    ("cohere",       "Cohere  (Command R — best for RAG — free tier)"),
    ("together",     "Together.ai  (50+ open models)"),
    ("fireworks",    "Fireworks.ai"),
    ("perplexity",   "Perplexity  (web search baked in)"),
    ("huggingface",  "Hugging Face Inference  (free tier)"),
    ("cerebras",     "Cerebras  (wafer-chip, very fast)"),
]

CLOUD_INFRA_PROVIDERS = [
    ("bedrock",      "Amazon Bedrock  (AWS — Claude + Llama + Mistral under one bill)"),
    ("azure_openai", "Azure OpenAI  (enterprise, data residency)"),
    ("vertex_ai",    "Google Vertex AI  (GCP)"),
]

LOCAL_PROVIDERS = [
    ("ollama",       "Ollama  (free, runs on your machine — recommended)"),
    ("lm_studio",    "LM Studio  (GUI model manager + local API)"),
    ("vllm",         "vLLM  (self-hosted, GPU server)"),
]

# Key used in __init__ for subscription vs API claude
_CLAUDE_SUB_KEY  = "claude_code"   # subscription path → ClaudeCodeProvider
_CLAUDE_API_KEY  = "anthropic"     # API key path → AnthropicProvider

# Remap the display keys to provider type_ids
_DISPLAY_TO_TYPE = {
    "anthropic":    _CLAUDE_SUB_KEY,   # "Anthropic — subscription" → claude_code
    "claude_code":  _CLAUDE_API_KEY,   # "Anthropic — API key" → anthropic
}


# ─────────────────────────────────────────────
# Per-provider configuration steps
# ─────────────────────────────────────────────

async def _configure_claude_subscription() -> Optional[dict]:
    """Claude Code CLI path."""
    header("Anthropic / Claude — Subscription (CLI)")
    cli = shutil.which("claude")
    if cli:
        print(f"  {check_mark(True)} Claude Code CLI found at {dim(cli)}")
        try:
            result = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=5)
            print(f"  {dim(result.stdout.strip())}")
        except Exception:
            pass
    else:
        print(f"  {check_mark(False)} Claude Code CLI not found on PATH.")
        print(f"  Install at: {cyan('https://claude.ai/code')}")
        if not ask_yn("  Continue anyway (you can install it later)?", default=False):
            return None

    model = ask(
        "  Model",
        default="claude-sonnet-4-6",
    )
    return {
        "type": "claude_code",
        "model": model,
        "timeout": 600,
    }


async def _configure_claude_api() -> Optional[dict]:
    """Anthropic direct API path."""
    header("Anthropic / Claude — Direct API")
    print(f"  Get a key at: {cyan('https://console.anthropic.com')}")
    api_key = ask_secret("  ANTHROPIC_API_KEY")
    if not api_key:
        print(red("  No key entered — skipping."))
        return None

    # Test the key
    print("  Testing key...", end=" ", flush=True)
    ok = await _test_anthropic(api_key)
    print(check_mark(ok))
    if not ok:
        if not ask_yn("  Key test failed. Continue anyway?", default=False):
            return None

    model = ask("  Model", default="claude-sonnet-4-6")
    return {
        "type": "anthropic",
        "model": model,
        "api_key": f"${{ANTHROPIC_API_KEY}}",
        "prompt_caching": True,
        "_env": {"ANTHROPIC_API_KEY": api_key},
    }


async def _configure_openai() -> Optional[dict]:
    header("OpenAI")
    sub = ask_choice(
        "OpenAI access type:",
        ["Standard OpenAI API", "Azure OpenAI"],
    )
    if sub == "Azure OpenAI":
        return await _configure_azure_openai()

    print(f"  Get a key at: {cyan('https://platform.openai.com/api-keys')}")
    print(f"  {dim('Note: ChatGPT Plus subscription ≠ API access.')}")
    api_key = ask_secret("  OPENAI_API_KEY")
    if not api_key:
        return None

    print("  Testing key and fetching models...", end=" ", flush=True)
    models = await _query_openai_models(api_key, "https://api.openai.com/v1")
    print(check_mark(bool(models)))

    model = _pick_model(models, "openai", default="gpt-4o")
    return {
        "type": "openai",
        "model": model,
        "api_key": "${OPENAI_API_KEY}",
        "_env": {"OPENAI_API_KEY": api_key},
    }


async def _configure_azure_openai() -> Optional[dict]:
    header("Azure OpenAI")
    endpoint = ask("  Azure endpoint (e.g. https://YOUR-RESOURCE.openai.azure.com)")
    api_key = ask_secret("  Azure API key")
    deployment = ask("  Deployment name", default="gpt-4o")
    api_version = ask("  API version", default="2024-02-01")
    if not api_key or not endpoint:
        return None
    return {
        "type": "openai",
        "model": deployment,
        "api_key": "${AZURE_OPENAI_API_KEY}",
        "base_url": endpoint,
        "api_version": api_version,
        "_env": {"AZURE_OPENAI_API_KEY": api_key, "AZURE_OPENAI_ENDPOINT": endpoint},
    }


async def _configure_gemini() -> Optional[dict]:
    header("Google Gemini")
    sub = ask_choice(
        "Google access type:",
        ["AI Studio  (free tier, personal key)", "Vertex AI  (GCP project)"],
    )
    if "Vertex" in sub:
        return await _configure_vertex_ai()

    print(f"  Get a free key at: {cyan('https://aistudio.google.com/app/apikey')}")
    api_key = ask_secret("  GOOGLE_API_KEY")
    if not api_key:
        return None

    print("  Testing key and fetching models...", end=" ", flush=True)
    models = await _query_gemini_models(api_key)
    print(check_mark(bool(models)))

    model = _pick_model(models, "gemini", default="gemini-2.0-flash")
    return {
        "type": "gemini",
        "model": model,
        "api_key": "${GOOGLE_API_KEY}",
        "_env": {"GOOGLE_API_KEY": api_key},
    }


async def _configure_vertex_ai() -> Optional[dict]:
    header("Google Vertex AI")
    project = ask("  GCP Project ID")
    region = ask("  Region", default="us-central1")
    if not project:
        return None
    return {
        "type": "vertex_ai",
        "model": "gemini-1.5-pro",
        "project": project,
        "region": region,
        "_env": {"GOOGLE_CLOUD_PROJECT": project, "GOOGLE_CLOUD_REGION": region},
    }


async def _configure_openai_compatible(
    provider_key: str,
    display_name: str,
    env_var: str,
    base_url: str,
    key_url: str,
    default_model: str,
    free_tier_note: str = "",
) -> Optional[dict]:
    header(display_name)
    if free_tier_note:
        print(f"  {dim(free_tier_note)}")
    print(f"  Get a key at: {cyan(key_url)}")
    api_key = ask_secret(f"  {env_var}")
    if not api_key:
        return None

    print("  Testing connection and fetching models...", end=" ", flush=True)
    models = await _query_openai_models(api_key, base_url)
    print(check_mark(bool(models)))

    model = _pick_model(models, provider_key, default=default_model)
    return {
        "type": "openai",
        "model": model,
        "api_key": f"${{{env_var}}}",
        "base_url": base_url,
        "_env": {env_var: api_key},
    }


async def _configure_groq() -> Optional[dict]:
    return await _configure_openai_compatible(
        "groq", "Groq", "GROQ_API_KEY",
        "https://api.groq.com/openai/v1",
        "https://console.groq.com",
        "llama-3.1-8b-instant",
        "Free tier available — generous rate limits.",
    )


async def _configure_mistral() -> Optional[dict]:
    return await _configure_openai_compatible(
        "mistral", "Mistral AI", "MISTRAL_API_KEY",
        "https://api.mistral.ai/v1",
        "https://console.mistral.ai",
        "mistral-small-latest",
        "EU-hosted. Suitable for GDPR / data residency requirements.",
    )


async def _configure_deepseek() -> Optional[dict]:
    return await _configure_openai_compatible(
        "deepseek", "DeepSeek", "DEEPSEEK_API_KEY",
        "https://api.deepseek.com/v1",
        "https://platform.deepseek.com",
        "deepseek-chat",
        "deepseek-chat (V3) is extremely low cost. deepseek-reasoner is R1 chain-of-thought.",
    )


async def _configure_xai() -> Optional[dict]:
    return await _configure_openai_compatible(
        "xai", "xAI / Grok", "XAI_API_KEY",
        "https://api.x.ai/v1",
        "https://console.x.ai",
        "grok-2",
        "Note: X/Twitter subscription does not include API access.",
    )


async def _configure_together() -> Optional[dict]:
    return await _configure_openai_compatible(
        "together", "Together.ai", "TOGETHER_API_KEY",
        "https://api.together.xyz/v1",
        "https://api.together.ai",
        "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    )


async def _configure_fireworks() -> Optional[dict]:
    return await _configure_openai_compatible(
        "fireworks", "Fireworks.ai", "FIREWORKS_API_KEY",
        "https://api.fireworks.ai/inference/v1",
        "https://fireworks.ai",
        "accounts/fireworks/models/llama-v3p1-8b-instruct",
    )


async def _configure_perplexity() -> Optional[dict]:
    return await _configure_openai_compatible(
        "perplexity", "Perplexity", "PERPLEXITY_API_KEY",
        "https://api.perplexity.ai",
        "https://www.perplexity.ai/settings/api",
        "llama-3.1-sonar-large-128k-online",
        "Models have live web search built in. Best as a research specialist.",
    )


async def _configure_huggingface() -> Optional[dict]:
    return await _configure_openai_compatible(
        "huggingface", "Hugging Face Inference", "HF_TOKEN",
        "https://api-inference.huggingface.co/v1",
        "https://huggingface.co/settings/tokens",
        "meta-llama/Llama-3.1-8B-Instruct",
        "Free tier available. Cold-start latency possible — not for triage.",
    )


async def _configure_cerebras() -> Optional[dict]:
    return await _configure_openai_compatible(
        "cerebras", "Cerebras", "CEREBRAS_API_KEY",
        "https://api.cerebras.ai/v1",
        "https://cloud.cerebras.ai",
        "llama3.1-8b",
    )


async def _configure_cohere() -> Optional[dict]:
    header("Cohere")
    print(f"  {dim('Best for RAG / retrieval workflows. Free tier available.')}")
    print(f"  Get a key at: {cyan('https://dashboard.cohere.com/api-keys')}")
    api_key = ask_secret("  COHERE_API_KEY")
    if not api_key:
        return None
    model = ask("  Model", default="command-r-plus")
    return {
        "type": "cohere",
        "model": model,
        "api_key": "${COHERE_API_KEY}",
        "_env": {"COHERE_API_KEY": api_key},
    }


async def _configure_bedrock() -> Optional[dict]:
    header("Amazon Bedrock")
    print(f"  {dim('Hosts Claude, Llama, Mistral, Titan under your AWS account.')}")
    region = ask("  AWS Region", default="us-east-1")
    auth = ask_choice(
        "  Auth method:",
        [
            "Environment variables / IAM role  (already configured)",
            "Named profile  (~/.aws/credentials)",
            "Access key + secret  (enter now)",
        ],
    )
    env: dict = {"AWS_REGION": region}
    config: dict = {"type": "bedrock", "region": region}

    if "profile" in auth.lower():
        profile = ask("  Profile name", default="default")
        config["profile"] = profile
    elif "access key" in auth.lower():
        ak = ask_secret("  AWS Access Key ID")
        sk = ask_secret("  AWS Secret Access Key")
        env["AWS_ACCESS_KEY_ID"] = ak
        env["AWS_SECRET_ACCESS_KEY"] = sk
        config["access_key"] = "${AWS_ACCESS_KEY_ID}"
        config["secret_key"] = "${AWS_SECRET_ACCESS_KEY}"

    print("  Fetching available Bedrock models...", end=" ", flush=True)
    models = await _query_bedrock_models(region, env)
    print(check_mark(bool(models)))

    default_model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    model = _pick_model(models, "bedrock", default=default_model)
    config["model"] = model
    config["_env"] = env
    return config


async def _configure_ollama() -> Optional[dict]:
    header("Ollama  (local)")
    endpoint = ask("  Ollama endpoint", default="http://localhost:11434")

    print(f"  Checking connection...", end=" ", flush=True)
    models = await _query_ollama_models(endpoint)
    print(check_mark(bool(models)))

    if not models:
        print(f"  {yellow('No models found. Install Ollama and pull a model first:')}")
        print(f"  {dim('ollama pull llama3.2:3b')}")
        if not ask_yn("  Continue anyway?", default=False):
            return None
        model = ask("  Model name to use later", default="llama3.2:3b")
    else:
        print(f"\n  {green('Found models:')}")
        for m in models:
            tier = infer_tier(m)
            print(f"    {dim('•')} {m}  {dim(f'→ {tier} tier')}")
        model = _pick_model(models, "ollama", default=models[0])

    return {
        "type": "ollama",
        "model": model,
        "endpoint": endpoint,
        "_models": models,
    }


async def _configure_lm_studio() -> Optional[dict]:
    header("LM Studio  (local)")
    base_url = ask("  LM Studio API endpoint", default="http://localhost:1234/v1")
    print("  Checking connection...", end=" ", flush=True)
    models = await _query_openai_models("no-key", base_url)
    print(check_mark(bool(models)))
    model = _pick_model(models, "lm_studio", default=models[0] if models else "local-model")
    return {
        "type": "openai",
        "model": model,
        "api_key": "no-key",
        "base_url": base_url,
    }


async def _configure_vllm() -> Optional[dict]:
    header("vLLM  (self-hosted)")
    base_url = ask("  vLLM API endpoint", default="http://localhost:8000/v1")
    models = await _query_openai_models("no-key", base_url)
    model = _pick_model(models, "vllm", default=models[0] if models else "")
    return {
        "type": "openai",
        "model": model,
        "api_key": "no-key",
        "base_url": base_url,
    }


# Dispatch table — maps provider key → configure function
_CONFIGURATORS = {
    "anthropic":    _configure_claude_subscription,
    "claude_code":  _configure_claude_api,
    "openai":       _configure_openai,
    "gemini":       _configure_gemini,
    "vertex_ai":    _configure_vertex_ai,
    "groq":         _configure_groq,
    "mistral":      _configure_mistral,
    "deepseek":     _configure_deepseek,
    "xai":          _configure_xai,
    "cohere":       _configure_cohere,
    "together":     _configure_together,
    "fireworks":    _configure_fireworks,
    "perplexity":   _configure_perplexity,
    "huggingface":  _configure_huggingface,
    "cerebras":     _configure_cerebras,
    "bedrock":      _configure_bedrock,
    "azure_openai": _configure_azure_openai,
    "ollama":       _configure_ollama,
    "lm_studio":    _configure_lm_studio,
    "vllm":         _configure_vllm,
}


# ─────────────────────────────────────────────
# Live API queries
# ─────────────────────────────────────────────

async def _test_anthropic(api_key: str) -> bool:
    try:
        import anthropic as sdk
        client = sdk.AsyncAnthropic(api_key=api_key)
        await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "hi"}],
        )
        return True
    except Exception:
        return False


async def _query_openai_models(api_key: str, base_url: str) -> list[str]:
    try:
        from openai import AsyncOpenAI
        kwargs: dict = {"api_key": api_key or "no-key"}
        if base_url and "openai.com" not in base_url:
            kwargs["base_url"] = base_url
        client = AsyncOpenAI(**kwargs)
        result = await client.models.list()
        return sorted([m.id for m in result.data])
    except Exception:
        return []


async def _query_gemini_models(api_key: str) -> list[str]:
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return sorted([
            m.name.replace("models/", "")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ])
    except Exception:
        return []


async def _query_ollama_models(endpoint: str) -> list[str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{endpoint}/api/tags")
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


async def _query_bedrock_models(region: str, env: dict) -> list[str]:
    try:
        import boto3
        session = boto3.Session()
        client = session.client("bedrock", region_name=region)
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None, lambda: client.list_foundation_models(byOutputModality="TEXT")
        )
        return [m["modelId"] for m in resp.get("modelSummaries", [])]
    except Exception:
        return []


# ─────────────────────────────────────────────
# Model picker
# ─────────────────────────────────────────────

def _pick_model(models: list[str], provider_key: str, default: str) -> str:
    """
    Let the operator choose a model from the discovered list,
    with tier annotations. Falls back to default if list is empty.
    """
    if not models:
        return ask("  Model name", default=default)

    # Annotate with tier
    annotated = []
    for m in models:
        tier = get_tier(provider_key, m)
        annotated.append(f"{m}  {dim(f'[{tier}]')}")

    print(f"\n  Available models ({len(models)} found):")
    for i, label in enumerate(annotated[:30]):   # cap display at 30
        print(f"    ({i+1:2d}) {label}")
    if len(models) > 30:
        print(f"    {dim(f'... and {len(models)-30} more (type the name directly)')}")

    raw = ask("  Model", default=default)
    # If they typed a number, resolve it
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(models):
            return models[idx]
    except ValueError:
        pass
    return raw


# ─────────────────────────────────────────────
# Role assignment
# ─────────────────────────────────────────────

def _assign_roles(configured: dict[str, dict]) -> dict:
    """
    Ask operator to assign providers to roles.
    Returns routing config dict.
    """
    if not configured:
        return {}

    header("Role Assignment")
    names = list(configured.keys())

    def pick_role(role: str, hint: str, default_idx: int = 0) -> Optional[str]:
        opts = names + ["(skip — use fallback)"]
        print(f"\n  {bold(role)}: {dim(hint)}")
        chosen = ask_choice("", opts, default=min(default_idx, len(opts)-1))
        return None if "skip" in chosen else chosen

    # Recommend triage = first local/nano-capable provider found
    triage_default = 0
    for i, name in enumerate(names):
        cfg = configured[name]
        if cfg.get("type") in ("ollama",) or "groq" in name.lower():
            triage_default = i
            break

    primary_name  = pick_role("Primary",  "handles most conversations",        default_idx=0)
    triage_name   = pick_role("Triage",   "classifies every message — keep fast/cheap", default_idx=triage_default)
    code_name     = pick_role("Code specialist (optional)", "routes code/debug tasks here")
    privacy_name  = pick_role("Privacy specialist (optional)", "local-only for sensitive messages")
    deep_name     = pick_role("Deep reasoning (optional)",  "escalation target for complex tasks")

    routing: dict = {
        "default": primary_name or names[0],
        "triage": triage_name or primary_name or names[0],
        "patterns": [],
    }
    if code_name:
        routing["patterns"].append({"match": r"code|debug|fix|script|implement|refactor", "provider": code_name})
    if privacy_name:
        routing["patterns"].append({"match": r"private|sensitive|local only|confidential", "provider": privacy_name})
    if deep_name:
        routing["patterns"].append({"match": r"think carefully|complex|reason through|step by step", "provider": deep_name})

    return routing


# ─────────────────────────────────────────────
# Config writers
# ─────────────────────────────────────────────

NOTIFY_ADAPTERS = [
    ("mattermost", "Mattermost  (self-hosted team chat)"),
    ("slack",      "Slack"),
    ("discord",    "Discord  (webhook — push-only)"),
    ("telegram",   "Telegram  (bot API)"),
    ("none",       "None — disable internal notifications"),
]


def _configure_notify() -> dict:
    """
    Ask the operator which adapter Nexus should use for out-of-band
    notifications (watchers, health alerts, scheduled task output).
    This becomes the default_protocol for the Notifier.
    """
    header("Step 3b — Notification Adapter")
    print("  Nexus sends internal alerts (watcher events, health checks,")
    print("  cron output) via your chosen protocol. You can change this")
    print("  at any time by editing config/nexus.yaml.")
    print()

    proto = ask_choice("Which protocol should Nexus use for notifications?",
                       [label for _, label in NOTIFY_ADAPTERS])
    key = dict(zip([label for _, label in NOTIFY_ADAPTERS],
                   [k for k, _ in NOTIFY_ADAPTERS]))[proto]

    if key == "none":
        print(dim("  Notifications disabled."))
        return {}

    dest = ask_choice("Default destination?", ["dm", "channel"])
    proto_cfg: dict = {}

    if key == "mattermost":
        url = ask("  Mattermost URL", "http://localhost:8065")
        token = ask_secret("  Bot token")
        team = ask("  Team name", "nexus")
        dm_id = ask("  DM channel ID (leave blank to resolve at runtime)", "")
        proto_cfg = {"url": url, "bot_token": token, "team": team}
        if dm_id:
            proto_cfg["dm_channel_id"] = dm_id

    elif key == "slack":
        token = ask_secret("  Slack bot token (xoxb-...)")
        default_channel = ask("  Default channel (e.g. #alerts)", "#alerts")
        proto_cfg = {"bot_token": token, "default_channel": default_channel}

    elif key == "discord":
        webhook = ask_secret("  Webhook URL")
        proto_cfg = {"webhook_url": webhook}

    elif key == "telegram":
        bot_token = ask_secret("  Bot token")
        chat_id = ask("  Chat/Group ID")
        proto_cfg = {"bot_token": bot_token, "chat_id": chat_id}

    print(f"  {check_mark(True)} Notifications → {bold(key)} ({dest})")

    return {
        "default_protocol": key,
        "default_destination": dest,
        "protocols": {key: proto_cfg},
    }


def _write_providers_yaml(configured: dict[str, dict], routing: dict, notify: dict = None) -> Path:
    out: dict = {"providers": {}, "routing": routing}
    for name, cfg in configured.items():
        clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
        out["providers"][name] = clean
    if notify:
        out["notify"] = notify

    path = CONFIG_DIR / "providers.yaml"
    with open(path, "w") as f:
        yaml.dump(out, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return path


def _write_env(configured: dict[str, dict]) -> Path:
    lines: list[str] = ["# Nexus — API keys (generated by setup wizard)\n"]
    seen: set[str] = set()
    for cfg in configured.values():
        for k, v in cfg.get("_env", {}).items():
            if k not in seen:
                lines.append(f"{k}={v}\n")
                seen.add(k)

    # Preserve any existing entries not overwritten
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                existing[key.strip()] = val.strip()
    for k, v in existing.items():
        if k not in seen:
            lines.append(f"{k}={v}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(lines)
    return ENV_FILE


# ─────────────────────────────────────────────
# Main wizard flow
# ─────────────────────────────────────────────

async def run() -> None:
    print()
    print(bold("  ╔══════════════════════════════════════╗"))
    print(bold("  ║       Multi-LLM Nexus — Setup        ║"))
    print(bold("  ║   Your AI platform. Your providers.  ║"))
    print(bold("  ╚══════════════════════════════════════╝"))
    print()
    print("  This wizard will help you configure which AI providers")
    print("  Nexus uses and how to route tasks between them.")
    print()
    print(dim("  Press Ctrl+C at any time to exit."))

    # ── Step 1: Provider selection ──────────────────────────────────────
    header("Step 1 — Select Your Providers")
    print(dim("  Select all providers you have access to."))

    cloud_keys = ask_multiselect("Cloud providers:", CLOUD_PROVIDERS)
    infra_keys = ask_multiselect("Cloud infrastructure (AWS/Azure/GCP):", CLOUD_INFRA_PROVIDERS)
    local_keys = ask_multiselect("Local / self-hosted:", LOCAL_PROVIDERS)

    all_keys = cloud_keys + infra_keys + local_keys
    if not all_keys:
        print(yellow("\n  No providers selected. Exiting."))
        return

    # ── Step 2: Configure each provider ─────────────────────────────────
    header("Step 2 — Configure Providers")
    configured: dict[str, dict] = {}

    for key in all_keys:
        configurator = _CONFIGURATORS.get(key)
        if not configurator:
            print(yellow(f"  No configurator for {key} — skipping."))
            continue
        result = await configurator()
        if result:
            # Use a friendly role name as the key in providers.yaml
            role_name = key
            configured[role_name] = result
            print(green(f"  ✓ {key} configured as '{role_name}'"))
        else:
            print(yellow(f"  Skipped {key}."))

    if not configured:
        print(red("\n  No providers successfully configured. Exiting."))
        return

    # ── Step 3: Role assignment ──────────────────────────────────────────
    routing = _assign_roles(configured)

    # ── Step 3b: Notification adapter ───────────────────────────────────
    notify_cfg = _configure_notify()

    # ── Step 4: Write config ─────────────────────────────────────────────
    header("Step 4 — Writing Configuration")
    yaml_path = _write_providers_yaml(configured, routing, notify_cfg)
    env_path  = _write_env(configured)

    print(f"  {check_mark(True)} {yaml_path.relative_to(PROJECT_ROOT)}")
    print(f"  {check_mark(True)} {env_path.relative_to(PROJECT_ROOT)}")

    # ── Summary ──────────────────────────────────────────────────────────
    header("Setup Complete")
    print(f"  Providers configured: {bold(str(len(configured)))}")
    print(f"  Primary:  {bold(routing.get('default', '—'))}")
    print(f"  Triage:   {bold(routing.get('triage', '—'))}")
    print()
    print("  Start Nexus:")
    print(f"  {cyan('source .venv/bin/activate && python -m src.main')}")
    print()
    print("  Re-run this wizard anytime:")
    print(f"  {cyan('python -m src.setup.wizard')}")
    print()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
