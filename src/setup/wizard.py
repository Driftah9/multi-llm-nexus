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
import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
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
    # ── Subscription CLI (no API key needed, authenticate via provider's own tooling) ──
    ("anthropic",       "Anthropic / Claude — subscription CLI (Claude Code — Pro/Teams plan)"),
    # ── API key ──
    ("claude_code",     "Anthropic / Claude — API key  (no CLI required)"),
    ("openai",          "OpenAI  (GPT-4o, o3)  — API key, not ChatGPT Plus"),
    ("github_models",   "GitHub Models  (GPT-4o, Llama, Mistral + more — free GitHub account or Copilot)"),
    ("openrouter",      "OpenRouter  (100+ models, 30+ providers — one API key)"),
    ("gemini",          "Google Gemini  (Flash, Pro — free tier available)"),
    ("groq",            "Groq  (fast open-source inference — free tier available)"),
    ("mistral",         "Mistral AI  (EU-hosted, GDPR-friendly)"),
    ("deepseek",        "DeepSeek  (V3 + R1 reasoning — very low cost)"),
    ("xai",             "xAI / Grok  — API key, not X/Twitter Premium"),
    ("cohere",          "Cohere  (Command R — best for RAG — free tier)"),
    ("together",        "Together.ai  (50+ open models)"),
    ("fireworks",       "Fireworks.ai"),
    ("perplexity",      "Perplexity  (web search baked in)"),
    ("huggingface",     "Hugging Face Inference  (free tier)"),
    ("cerebras",        "Cerebras  (wafer-chip, very fast)"),
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
# Dependency maps
# ─────────────────────────────────────────────

# pip package name → Python import name (where they differ)
_PIP_TO_IMPORT: dict[str, str] = {
    "google-generativeai":     "google.generativeai",
    "google-cloud-aiplatform": "google.cloud.aiplatform",
    "python-telegram-bot":     "telegram",
    "pyyaml":                  "yaml",
    "python-dotenv":           "dotenv",
}

# Which pip packages each provider requires
PROVIDER_DEPS: dict[str, list[str]] = {
    "anthropic":      ["anthropic"],
    "claude_code":    [],                        # needs claude CLI, not a pip package
    "openai":         ["openai"],
    "github_models":  ["openai"],                # OpenAI-compat endpoint, uses openai package
    "openrouter":     ["openai"],                # OpenAI-compat aggregator
    "gemini":         ["google-generativeai"],
    "vertex_ai":      ["google-cloud-aiplatform"],
    "groq":           ["openai"],
    "mistral":        ["openai"],
    "deepseek":       ["openai"],
    "xai":            ["openai"],
    "cohere":         ["cohere"],
    "together":       ["openai"],
    "fireworks":      ["openai"],
    "perplexity":     ["openai"],
    "huggingface":    ["openai"],
    "cerebras":       ["openai"],
    "bedrock":        ["boto3"],
    "azure_openai":   ["openai"],
    "ollama":         [],                        # uses httpx (already a core dep)
    "lm_studio":      ["openai"],
    "vllm":           ["openai"],
}

# Which pip packages each platform adapter requires
ADAPTER_DEPS: dict[str, list[str]] = {
    "mattermost": ["aiohttp"],
    "telegram":   ["python-telegram-bot"],
    "discord":    [],
    "slack":      ["slack-sdk"],
}

# Known API key env vars and the provider they belong to
_ENV_KEY_MAP: dict[str, str] = {
    "ANTHROPIC_API_KEY":    "Anthropic",
    "OPENAI_API_KEY":       "OpenAI",
    "GOOGLE_API_KEY":       "Google Gemini",
    "GROQ_API_KEY":         "Groq",
    "MISTRAL_API_KEY":      "Mistral",
    "DEEPSEEK_API_KEY":     "DeepSeek",
    "XAI_API_KEY":          "xAI",
    "COHERE_API_KEY":       "Cohere",
    "TOGETHER_API_KEY":     "Together.ai",
    "FIREWORKS_API_KEY":    "Fireworks",
    "PERPLEXITY_API_KEY":   "Perplexity",
    "HF_TOKEN":             "Hugging Face",
    "CEREBRAS_API_KEY":     "Cerebras",
    "AWS_ACCESS_KEY_ID":    "AWS / Bedrock",
    "AZURE_OPENAI_API_KEY": "Azure OpenAI",
    "GITHUB_TOKEN":         "GitHub Models",
    "OPENROUTER_API_KEY":   "OpenRouter",
}

# Local services to probe
_LOCAL_SERVICES: dict[str, str] = {
    "Ollama":    "http://localhost:11434",
    "vLLM":      "http://localhost:8000/v1/models",
    "LM Studio": "http://localhost:1234/v1/models",
}


# ─────────────────────────────────────────────
# System scan
# ─────────────────────────────────────────────

@dataclass
class ScanResult:
    packages: dict[str, Optional[str]]  = field(default_factory=dict)   # pip_name → version|None
    cli_tools: dict[str, Optional[str]] = field(default_factory=dict)   # tool → path|None
    services: dict[str, bool]           = field(default_factory=dict)   # name → reachable
    env_keys: dict[str, bool]           = field(default_factory=dict)   # var → present


def _pkg_version(pip_name: str) -> Optional[str]:
    import_name = _PIP_TO_IMPORT.get(pip_name, pip_name)
    top = import_name.split(".")[0]
    if importlib.util.find_spec(top) is None:
        return None
    try:
        return importlib.metadata.version(pip_name)
    except Exception:
        return "installed"


async def _probe_service(url: str) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(url)
            return r.status_code < 500
    except Exception:
        return False


def _load_dotenv_map() -> dict[str, str]:
    result: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result


async def _run_scan() -> ScanResult:
    # Collect all unique packages across providers + adapters
    all_pkgs = sorted(set(
        p
        for deps in list(PROVIDER_DEPS.values()) + list(ADAPTER_DEPS.values())
        for p in deps
    ))

    packages  = {pkg: _pkg_version(pkg) for pkg in all_pkgs}
    cli_tools = {
        "claude": shutil.which("claude"),
        "ollama": shutil.which("ollama"),
        "docker": shutil.which("docker"),
        "git":    shutil.which("git"),
    }

    probe_tasks = {name: asyncio.create_task(_probe_service(url))
                   for name, url in _LOCAL_SERVICES.items()}
    services = {name: await task for name, task in probe_tasks.items()}

    env_map = {**_load_dotenv_map(), **os.environ}
    env_keys = {var: bool(env_map.get(var)) for var in _ENV_KEY_MAP}

    return ScanResult(packages=packages, cli_tools=cli_tools,
                      services=services, env_keys=env_keys)


def _show_scan(result: ScanResult) -> None:
    header("System Scan")

    print(f"  {bold('CLI Tools')}")
    for tool, path in result.cli_tools.items():
        status = check_mark(bool(path))
        detail = dim(path) if path else dim("not found")
        print(f"    {status} {tool:<10} {detail}")

    print()
    print(f"  {bold('Python Packages')}")
    for pkg, ver in sorted(result.packages.items()):
        status = check_mark(bool(ver))
        detail = dim(ver) if ver else dim("not installed")
        print(f"    {status} {pkg:<30} {detail}")

    print()
    print(f"  {bold('Local Services')}")
    for name, ok in result.services.items():
        status = check_mark(ok)
        detail = dim("responding") if ok else dim("not reached")
        print(f"    {status} {name:<12} {detail}")

    print()
    print(f"  {bold('API Keys')}")
    found = [(var, label) for var, label in _ENV_KEY_MAP.items()
             if result.env_keys.get(var)]
    if found:
        for _, label in found:
            print(f"    {check_mark(True)} {label}")
    else:
        print(f"    {dim('None found in .env — keys will be entered during setup.')}")


def _install_packages(packages: list[str]) -> bool:
    """Install pip packages into the running Python environment."""
    if not packages:
        return True
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages
    print()
    for pkg in packages:
        print(f"    {dim('→')} {pkg}")
    print()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"  {check_mark(True)} Packages installed.")
            return True
        print(red(f"  Install error:\n    {proc.stderr.strip()[:600]}"))
        return False
    except Exception as e:
        print(red(f"  Install failed: {e}"))
        return False


def _missing_deps(selected_providers: list[str],
                  selected_adapters: list[str],
                  scan: ScanResult) -> list[str]:
    required: set[str] = set()
    for key in selected_providers:
        required.update(PROVIDER_DEPS.get(key, []))
    for key in selected_adapters:
        required.update(ADAPTER_DEPS.get(key, []))
    return sorted(pkg for pkg in required if not scan.packages.get(pkg))


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


# Declarative specs for OpenAI-compatible providers.
# Each entry: (key, display_name, env_var, base_url, key_url, default_model, note)
_OPENAI_COMPATIBLE_SPECS: list[tuple] = [
    # ── Subscription / free-token paths ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    ("github_models", "GitHub Models",         "GITHUB_TOKEN",       "https://models.inference.ai.azure.com",       "https://github.com/settings/tokens",         "gpt-4o",                                        "Free GitHub account: limited daily quota. Copilot subscription: higher limits. Serves GPT-4o, Llama, Mistral, Phi under one token."),
    # ── Aggregators ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    ("openrouter",  "OpenRouter",              "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",                "https://openrouter.ai/keys",                 "openai/gpt-4o",                                 "100+ models, 30+ providers, one key. Model IDs use provider/model format: anthropic/claude-sonnet-4-6, openai/gpt-4o, etc."),
    # ── Standard API-key providers ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    ("groq",        "Groq",                   "GROQ_API_KEY",       "https://api.groq.com/openai/v1",              "https://console.groq.com",                   "llama-3.1-8b-instant",                          "Free tier available — generous rate limits."),
    ("mistral",     "Mistral AI",              "MISTRAL_API_KEY",    "https://api.mistral.ai/v1",                   "https://console.mistral.ai",                 "mistral-small-latest",                          "EU-hosted. Suitable for GDPR / data residency requirements."),
    ("deepseek",    "DeepSeek",                "DEEPSEEK_API_KEY",   "https://api.deepseek.com/v1",                 "https://platform.deepseek.com",              "deepseek-chat",                                 "deepseek-chat (V3) is extremely low cost. deepseek-reasoner is R1 chain-of-thought."),
    ("xai",         "xAI / Grok",              "XAI_API_KEY",        "https://api.x.ai/v1",                         "https://console.x.ai",                       "grok-2",                                        "Note: X/Twitter subscription does not include API access."),
    ("together",    "Together.ai",             "TOGETHER_API_KEY",   "https://api.together.xyz/v1",                 "https://api.together.ai",                    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",   ""),
    ("fireworks",   "Fireworks.ai",            "FIREWORKS_API_KEY",  "https://api.fireworks.ai/inference/v1",       "https://fireworks.ai",                       "accounts/fireworks/models/llama-v3p1-8b-instruct", ""),
    ("perplexity",  "Perplexity",              "PERPLEXITY_API_KEY", "https://api.perplexity.ai",                   "https://www.perplexity.ai/settings/api",     "llama-3.1-sonar-large-128k-online",             "Models have live web search built in. Best as a research specialist."),
    ("huggingface", "Hugging Face Inference",  "HF_TOKEN",           "https://api-inference.huggingface.co/v1",    "https://huggingface.co/settings/tokens",     "meta-llama/Llama-3.1-8B-Instruct",              "Free tier available. Cold-start latency possible — not for triage."),
    ("cerebras",    "Cerebras",                "CEREBRAS_API_KEY",   "https://api.cerebras.ai/v1",                  "https://cloud.cerebras.ai",                  "llama3.1-8b",                                   ""),
]


def _make_openai_compatible_configurator(spec: tuple):
    """Generate a configurator coroutine from a declarative provider spec."""
    key, display, env_var, base_url, key_url, default_model, note = spec
    async def _configurator() -> Optional[dict]:
        return await _configure_openai_compatible(
            key, display, env_var, base_url, key_url, default_model, note
        )
    _configurator.__name__ = f"_configure_{key}"
    return _configurator


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


# Dispatch table — maps provider key → configure function.
# OpenAI-compatible providers are generated from _OPENAI_COMPATIBLE_SPECS.
_CONFIGURATORS = {
    "anthropic":    _configure_claude_subscription,
    "claude_code":  _configure_claude_api,
    "openai":       _configure_openai,
    "gemini":       _configure_gemini,
    "vertex_ai":    _configure_vertex_ai,
    "cohere":       _configure_cohere,
    "bedrock":      _configure_bedrock,
    "azure_openai": _configure_azure_openai,
    "ollama":       _configure_ollama,
    "lm_studio":    _configure_lm_studio,
    "vllm":         _configure_vllm,
    **{spec[0]: _make_openai_compatible_configurator(spec) for spec in _OPENAI_COMPATIBLE_SPECS},
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
        loop = asyncio.get_running_loop()
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
    os.chmod(ENV_FILE, 0o600)
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

    # ── Scan ─────────────────────────────────────────────────────────────
    print(f"\n  {dim('Scanning system...')}", end=" ", flush=True)
    scan = await _run_scan()
    print(green("done"))
    _show_scan(scan)

    if not ask_yn("\n  Continue to provider setup?"):
        return

    # ── Step 1: Provider selection ──────────────────────────────────────
    header("Step 1 — Select Your Providers")
    print(dim("  Select all providers you have access to."))
    if any(scan.env_keys.values()):
        print(dim("  (Tip: keys already detected in your .env are pre-verified.)"))

    cloud_keys = ask_multiselect("Cloud providers:", CLOUD_PROVIDERS)
    infra_keys = ask_multiselect("Cloud infrastructure (AWS/Azure/GCP):", CLOUD_INFRA_PROVIDERS)
    local_keys = ask_multiselect("Local / self-hosted:", LOCAL_PROVIDERS)

    all_keys = cloud_keys + infra_keys + local_keys
    if not all_keys:
        print(yellow("\n  No providers selected. Exiting."))
        return

    # ── Step 1b: Adapter selection ───────────────────────────────────────
    header("Step 1b — Select Platform Adapters")
    print(dim("  Which platforms will users send messages from?"))
    ADAPTER_OPTIONS = [
        ("mattermost", "Mattermost  (self-hosted team chat — WebSocket)"),
        ("discord",    "Discord  (REST polling)"),
        ("telegram",   "Telegram  (Bot API)"),
    ]
    selected_adapters = ask_multiselect("Platform adapters:", ADAPTER_OPTIONS)

    # ── Step 1c: Install dependencies ────────────────────────────────────
    header("Step 1c — Installing Dependencies")
    missing = _missing_deps(all_keys, selected_adapters, scan)

    if not missing:
        print(f"  {check_mark(True)} All required packages already installed.")
    else:
        print(f"  {len(missing)} package(s) needed for your selections:")
        if ask_yn("  Install now?"):
            ok = _install_packages(missing)
            if not ok:
                if not ask_yn("  Install had errors. Continue anyway?", default=False):
                    return
            # Refresh scan result for newly installed packages
            for pkg in missing:
                scan.packages[pkg] = _pkg_version(pkg)
        else:
            print(yellow("  Skipping — some connection tests may fail."))

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
    if selected_adapters:
        print(f"  Adapters: {bold(', '.join(selected_adapters))}")
        print()
        print(dim("  Configure adapter credentials in config/adapters.yaml"))
        print(dim("  (copy config/adapters.yaml.example as a starting point)"))
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
