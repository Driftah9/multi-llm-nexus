"""
Nexus Setup Wizard v2 — Interactive first-time configuration.

Guides the operator through:
  [A] System Scan
  [B] System Identity (orchestrator + machine name)
  [C] Hardware Detection → Local LLM recommendation
  [D] Provider Selection (flat merged list, whiptail checklist)
  [E] Adapter Selection (Mattermost, Discord, Telegram, etc.)
  [F] Provider Configuration (CLI install, API keys, connection tests)
  [G] Role Assignment (auto-derived; asks only if >1 provider)
  [H] Platform Setup (docker-compose generation, token deferral)
  [I] Service Install
  [J] Summary + Next Steps

Phase 2 (future): LLM-assisted use-case selection, role reasoning.

Run via: python -m src.setup.wizard
Or via: cd ~/nexus && source .venv/bin/activate && python -m src.setup.wizard
"""
from __future__ import annotations

import asyncio
import datetime as dt
import getpass
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"
SYSTEM_ROOT = Path.home()

# ─ Install log ─────────────────────────────────────────────────────────────────
_LOG_FILE = os.environ.get("NEXUS_LOG_FILE", "")
_log_fh = open(_LOG_FILE, "a") if _LOG_FILE else None

def _wlog(msg: str) -> None:
    """Write timestamped line to install log if active."""
    if _log_fh:
        ts = dt.datetime.now().strftime("%H:%M:%S")
        _log_fh.write(f"[{ts}] WIZARD: {msg}\n")
        _log_fh.flush()

from ..providers.registry import (
    PROVIDERS, TIER_NANO, TIER_STANDARD, TIER_DEEP,
    get_models_for_tier, infer_tier,
)
from .hardware_detect import detect_hardware, hardware_report


# ─ Terminal helpers ───────────────────────────────────────────────────────────

def _c(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

bold = lambda t: _c(t, "1")
green = lambda t: _c(t, "32")
yellow = lambda t: _c(t, "33")
red = lambda t: _c(t, "31")
dim = lambda t: _c(t, "2")
cyan = lambda t: _c(t, "36")

def header(title: str) -> None:
    print()
    print("─" * 60)
    print(f"  {bold(title)}")
    print("─" * 60)
    _wlog(f"═══ SECTION: {title} ═══")

def check_mark(ok: bool) -> str:
    return green("✓") if ok else red("✗")

def ask(prompt: str, default: str = "") -> str:
    """Prompt for input with optional default."""
    display = f"  {prompt}"
    if default:
        display += f" {dim(f'[{default}]')}"
    display += ": "
    _wlog(f"PROMPT: {prompt} [default: {default or 'none'}]")
    try:
        val = input(display).strip()
        result = val or default
        _wlog(f"ANSWER: {result}")
        return result
    except (KeyboardInterrupt, EOFError):
        _wlog("(interrupted)")
        print()
        sys.exit(0)

def ask_secret(prompt: str) -> str:
    """Prompt for hidden input (API key, password)."""
    _wlog(f"PROMPT_SECRET: {prompt}")
    try:
        val = getpass.getpass(f"  {prompt}: ").strip()
        _wlog(f"ANSWER_SECRET: {'(provided)' if val else '(empty)'}")
        return val
    except (KeyboardInterrupt, EOFError):
        _wlog("(interrupted)")
        print()
        sys.exit(0)

def ask_yn(prompt: str, default: bool = True) -> bool:
    """Prompt for yes/no."""
    hint = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({hint})", "y" if default else "n")
    _wlog(f"ANSWER_YN: {ans}")
    return ans.lower().startswith("y")

def whiptail_checklist(title: str, items: list[tuple[str, str, bool]]) -> list[str]:
    """
    Multi-select via whiptail --checklist.
    items: [(key, label, default_selected), ...]
    Returns: list of selected keys
    """
    list_height = min(len(items), 15)
    height = list_height + 8  # dialog chrome
    # whiptail syntax: --checklist text height width list-height [tag item status ...]
    args = ["whiptail", "--separate-output", "--checklist", title,
            str(height), "78", str(list_height)]
    for key, label, selected in items:
        args.extend([key, label, "on" if selected else "off"])
    try:
        # whiptail draws its dialog on stderr; selected output goes to stdout.
        # Open /dev/tty directly so the dialog renders in the terminal even when
        # stdout is redirected (e.g. inside script(1) or a subprocess chain).
        with open("/dev/tty", "r+") as tty:
            result = subprocess.run(
                args, stdin=tty, stdout=subprocess.PIPE, stderr=tty,
                text=True, check=False
            )
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split("\n") if l]
            return lines
        return []
    except (FileNotFoundError, OSError):
        # Fallback: numbered list
        print(f"\n{title}")
        for i, (key, label, _) in enumerate(items, 1):
            print(f"  ({i}) {label}")
        raw = input("  Select (comma-separated numbers): ").strip()
        sel: list[str] = []
        for part in raw.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(items):
                    sel.append(items[idx][0])
            except ValueError:
                pass
        return sel

def whiptail_radiolist(title: str, items: list[tuple[str, str, bool]]) -> str:
    """
    Single select via whiptail --radiolist.
    items: [(key, label, default_selected), ...]
    Returns: selected key or empty string
    """
    list_height = min(len(items), 10)
    height = list_height + 8
    # whiptail syntax: --radiolist text height width list-height [tag item status ...]
    args = ["whiptail", "--radiolist", title, str(height), "78", str(list_height)]
    for key, label, selected in items:
        args.extend([key, label, "on" if selected else "off"])
    try:
        with open("/dev/tty", "r+") as tty:
            result = subprocess.run(
                args, stdin=tty, stdout=subprocess.PIPE, stderr=tty,
                text=True, check=False
            )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except (FileNotFoundError, OSError):
        # Fallback: numbered list
        print(f"\n{title}")
        for i, (key, label, _) in enumerate(items, 1):
            print(f"  ({i}) {label}")
        raw = input("  Select (number): ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                return items[idx][0]
        except ValueError:
            pass
        return ""


# ─ System utilities ───────────────────────────────────────────────────────────

def get_system_ip() -> str:
    """Detect primary system IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            out = subprocess.run(
                "hostname -I | awk '{print $1}'",
                shell=True, capture_output=True, text=True, check=False
            )
            if out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return "localhost"

def get_hostname() -> str:
    """Get machine hostname."""
    try:
        return socket.gethostname()
    except Exception:
        return "nexus-system"

def check_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Check if a port is available."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False


# ─ System Scan [A] ────────────────────────────────────────────────────────────

def system_scan() -> dict:
    """Scan for CLI tools, Python packages, local services, API keys."""
    scan = {
        "tools": {},
        "packages": {},
        "services": {},
        "env_keys": {},
    }

    # CLI tools
    for tool in ["claude", "ollama", "docker"]:
        result = subprocess.run(
            f"command -v {tool}", shell=True, capture_output=True, text=True
        )
        scan["tools"][tool] = result.stdout.strip() if result.returncode == 0 else None

    # Python packages
    for pkg in ["aiohttp", "anthropic", "openai", "google-generativeai", "cohere"]:
        try:
            scan["packages"][pkg] = importlib.util.find_spec(pkg) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            scan["packages"][pkg] = False

    # Local services
    try:
        subprocess.run(
            "curl -s http://localhost:11434/api/tags",
            shell=True, capture_output=True, timeout=2, check=False
        )
        scan["services"]["ollama"] = True
    except Exception:
        scan["services"]["ollama"] = False

    # API keys from .env
    if ENV_FILE.exists():
        content = ENV_FILE.read_text()
        for line in content.splitlines():
            for key in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"]:
                if line.startswith(f"{key}=") and len(line.split("=", 1)[1].strip()) > 0:
                    scan["env_keys"][key] = True

    _wlog(f"system_scan: tools={scan['tools']}, services={scan['services']}")
    return scan


def print_scan(scan: dict) -> None:
    """Pretty-print system scan results."""
    header("System Scan")

    print("\n  CLI Tools")
    for tool in ["claude", "ollama", "docker"]:
        path = scan["tools"].get(tool)
        if path:
            print(f"    {check_mark(True)} {tool:15} {dim(path)}")
        else:
            print(f"    {check_mark(False)} {tool:15} not found")

    print("\n  Python Packages")
    for pkg in ["aiohttp", "anthropic", "openai", "google-generativeai", "cohere"]:
        ok = scan["packages"].get(pkg, False)
        print(f"    {check_mark(ok)} {pkg:30} {'installed' if ok else 'not installed'}")

    print("\n  Local Services")
    print(f"    {check_mark(scan['services'].get('ollama', False))} Ollama  " +
          ("reachable" if scan["services"].get("ollama") else "not reached"))

    print("\n  API Keys")
    if scan["env_keys"]:
        for key in scan["env_keys"]:
            print(f"    {check_mark(True)} {key}")
    else:
        print(f"    {check_mark(False)} None found in .env")

    print()


# ─ System Identity [B] ────────────────────────────────────────────────────────

def system_identity() -> tuple[str, str]:
    """Prompt for orchestrator name and system hostname."""
    header("System Identity")

    print("  Give your system an identity — a name and a short description.")
    print("  This becomes the orchestrator's soul: who it is, what it does.")
    print("  You can change these later by editing SOUL.md and AI_CONTEXT.md\n")

    agent_name = ask("Orchestrator name (what users will call your AI)")
    system_name = ask("System name (what this installation is called)", get_hostname())

    print()
    print(f"  {check_mark(True)} Orchestrator: {bold(agent_name)}")
    print(f"  {check_mark(True)} System name:  {bold(system_name)}")
    print()

    _wlog(f"system_identity: agent={agent_name}, system={system_name}")
    return agent_name, system_name


# ─ Hardware Detection [C] ──────────────────────────────────────────────────────

async def hardware_detection() -> dict:
    """Scan hardware and recommend local LLM."""
    header("Hardware Detection & Local LLM")

    print("  Detecting hardware... ", end="", flush=True)
    hw = await detect_hardware()
    print("done\n")

    print(f"  CPU: {hw.cpu_cores} cores")
    print(f"  RAM: {hw.ram_gb:.1f} GB")
    print(f"  GPU: {hw.gpu_type or 'None (CPU-only)'}\n")

    _wlog(f"hardware: {hw}")

    # Recommend local LLM if viable
    recommended_model = None
    if hw.ram_gb >= 8:
        if hw.ram_gb < 16:
            recommended_model = "llama3.2:3b"
        elif hw.ram_gb < 32:
            recommended_model = "llama3.1:8b"
        else:
            recommended_model = "llama3.1:70b" if hw.gpu_type else "llama3.1:8b"

        print(f"{check_mark(True)} Local LLM recommended")
        print(f"  Provider: ollama")
        print(f"  Model: {recommended_model}")
        if not hw.gpu_type:
            print(f"  No GPU detected. CPU-only inference via Ollama.\n")

    return hw


# ─ Provider Selection [D] ──────────────────────────────────────────────────────

CLOUD_PROVIDERS = [
    ("anthropic_cli", "Anthropic / Claude — subscription CLI (Claude Code — Pro/Teams plan)"),
    ("anthropic_api", "Anthropic / Claude — API key  (no CLI required)"),
    ("openai", "OpenAI  (GPT-4o, o3)  — API key, not ChatGPT Plus"),
    ("github_models", "GitHub Models  (GPT-4o, Llama, Mistral + more — free)"),
    ("openrouter", "OpenRouter  (100+ models, 30+ providers — one API key)"),
    ("gemini", "Google Gemini  (Flash, Pro — free tier available)"),
    ("groq", "Groq  (fast open-source inference — free tier available)"),
    ("mistral", "Mistral AI  (EU-hosted, GDPR-friendly)"),
    ("deepseek", "DeepSeek  (V3 + R1 reasoning — very low cost)"),
    ("xai", "xAI / Grok  — API key, not X/Twitter Premium"),
    ("cohere", "Cohere  (Command R — best for RAG — free tier)"),
    ("together", "Together.ai  (50+ open models)"),
    ("fireworks", "Fireworks.ai"),
    ("perplexity", "Perplexity  (web search baked in)"),
    ("huggingface", "Hugging Face Inference  (free tier)"),
    ("cerebras", "Cerebras  (wafer-chip, very fast)"),
    ("bedrock", "Amazon Bedrock  [Enterprise] (AWS — Claude + Llama + Mistral)"),
    ("azure_openai", "Azure OpenAI  [Enterprise] (enterprise, data residency)"),
    ("vertex", "Google Vertex AI  [Enterprise] (GCP)"),
]

LOCAL_PROVIDERS = [
    ("ollama", "Ollama  (free, runs on your machine — recommended)"),
    ("lm_studio", "LM Studio  (GUI model manager + local API)"),
    ("vllm", "vLLM  (self-hosted, GPU server)"),
]

def provider_selection(hw: dict) -> tuple[list[str], list[str]]:
    """Multi-select providers (cloud + infra + local merged)."""
    header("Step 1 — Select Your Providers")
    print("  Select all providers you have access to.\n")

    # Pre-select ollama if hardware supports it
    local_items = []
    for key, label in LOCAL_PROVIDERS:
        selected = (key == "ollama" and hw.ram_gb >= 8)
        local_items.append((key, label, selected))

    # Combine all providers
    all_items = []
    for key, label in CLOUD_PROVIDERS:
        all_items.append((key, label, False))
    all_items.extend(local_items)

    selected_keys = whiptail_checklist(
        "Select providers (SPACE to select, ENTER when done):",
        all_items
    )

    cloud_selected = [k for k in selected_keys if any(k == cp[0] for cp in CLOUD_PROVIDERS)]
    local_selected = [k for k in selected_keys if any(k == lp[0] for lp in LOCAL_PROVIDERS)]

    print(f"\n  {check_mark(True)} Providers selected: {len(selected_keys)}")
    _wlog(f"provider_selection: cloud={cloud_selected}, local={local_selected}")

    return cloud_selected, local_selected


# ─ Adapter Selection [E] ───────────────────────────────────────────────────────

ADAPTERS = [
    ("mattermost", "Mattermost  (self-hosted team chat — WebSocket)"),
    ("discord", "Discord  (REST polling)"),
    ("telegram", "Telegram  (Bot API)"),
]

def adapter_selection() -> list[str]:
    """Multi-select platform adapters."""
    header("Step 1b — Select Platform Adapters")
    print("  Which platforms will users send messages from?\n")

    items = [(key, label, False) for key, label in ADAPTERS]
    selected = whiptail_checklist(
        "Select adapters (SPACE to select, ENTER when done):",
        items
    )

    print(f"\n  {check_mark(True)} Adapters selected: {len(selected)}")
    _wlog(f"adapter_selection: {selected}")

    # Follow-up: local vs remote for each
    adapter_config = {}
    for adapter in selected:
        if adapter in ["mattermost", "discord"]:
            print()
            ans = ask_yn(f"Set up {adapter} locally (Docker) or use existing server?", True)
            adapter_config[adapter] = "local" if ans else "remote"

    _wlog(f"adapter_setup_choices: {adapter_config}")
    return selected, adapter_config


# ─ Provider Configuration [F] ──────────────────────────────────────────────────

async def configure_providers(
    cloud_selected: list[str],
    local_selected: list[str],
    system_ip: str
) -> dict:
    """Configure selected providers (CLI install, API keys, connection test)."""
    header("Step 2 — Configure Providers")

    configured = {}

    # Anthropic CLI
    if "anthropic_cli" in cloud_selected:
        print("\n  Anthropic / Claude — Subscription (CLI)")
        result = subprocess.run("command -v claude", shell=True, capture_output=True)
        cli_ready = result.returncode == 0
        if not cli_ready:
            print("    ✗ Claude Code CLI not found. Installing...")
            print("    → Running installer (https://claude.ai/code)...")
            install_result = subprocess.run(
                "curl -fsSL https://claude.ai/install.sh | bash",
                shell=True, check=False
            )
            # Reload PATH so claude is visible without a new shell
            new_path = subprocess.run(
                'echo "$HOME/.local/bin:$HOME/.claude/local/bin:$PATH"',
                shell=True, capture_output=True, text=True
            ).stdout.strip()
            os.environ["PATH"] = new_path
            cli_ready = subprocess.run("command -v claude", shell=True, capture_output=True).returncode == 0
            if cli_ready:
                print(f"    {check_mark(True)} Claude CLI installed and ready")
            else:
                print(f"    {check_mark(False)} Install script ran but 'claude' still not on PATH.")
                print("    → Open a new shell and run: claude auth login")
                print("    → Then re-run: python -m src.setup.wizard")
                _wlog("anthropic_cli: install failed — claude not on PATH after install")
                cloud_selected = [p for p in cloud_selected if p != "anthropic_cli"]

        if cli_ready:
            # Auth — run claude auth login with TTY attached so user can interact
            print("    → Launching: claude auth login")
            _wlog("anthropic_cli: attempting auth with /dev/tty subprocess")
            auth_success = False
            try:
                with open("/dev/tty", "r+") as tty:
                    _wlog("anthropic_cli: /dev/tty opened successfully")
                    result = subprocess.run(
                        ["claude", "auth", "login"],
                        stdin=tty, stdout=tty, stderr=tty, check=False
                    )
                    _wlog(f"anthropic_cli: subprocess returned code {result.returncode}")
                    auth_success = (result.returncode == 0)
            except FileNotFoundError as e:
                _wlog(f"anthropic_cli: /dev/tty not found: {e}")
                print("    (No TTY available; run manually: claude auth login)")
                input("    Press Enter when authentication is complete...")
            except OSError as e:
                _wlog(f"anthropic_cli: /dev/tty OSError: {e}")
                print("    (Cannot access TTY; run manually: claude auth login)")
                input("    Press Enter when authentication is complete...")

            # Test connection
            result = subprocess.run("claude -p 'ping' --output-format text", shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                configured["anthropic"] = "anthropic_cli"
                print(f"    {check_mark(True)} Anthropic (CLI) configured")
                _wlog("anthropic_cli: configured")
            else:
                print(f"    {check_mark(False)} Connection test failed — run: claude auth login")
                _wlog("anthropic_cli: connection test failed")

    # Ollama
    if "ollama" in local_selected:
        print("\n  Ollama  (local)")
        endpoint = f"http://{system_ip}:11434"
        print(f"    Endpoint: {endpoint}")

        # Check if running
        result = subprocess.run(
            f"curl -s {endpoint}/api/tags",
            shell=True, capture_output=True, timeout=2
        )
        if result.returncode != 0:
            if ask_yn("    Ollama not found. Install it?"):
                print("    → Running Ollama installer...")
                subprocess.run(
                    "curl -fsSL https://ollama.com/install.sh | sh",
                    shell=True, check=False
                )
                print("    → Start Ollama: ollama serve")
                input("    Press Enter after Ollama is running...")
            else:
                print("    Skipping Ollama.")
                _wlog("ollama: skipped")
                local_selected = [p for p in local_selected if p != "ollama"]

        # Pull model in background
        model = "llama3.2:3b"  # TODO: from hardware detection
        print(f"    Pulling model: {model}")
        subprocess.Popen(
            f"ollama pull {model}",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        configured["ollama"] = "ollama"
        print(f"    {check_mark(True)} Ollama configured (model pulling in background)")
        _wlog("ollama: configured")

    return configured


# ─ Role Assignment [G] ────────────────────────────────────────────────────────

def role_assignment(configured: dict) -> dict:
    """Assign roles (orchestrator, triage, workers)."""
    header("Step 3 — Role Assignment")

    routing = {}

    if len(configured) == 1:
        # Auto-assign the only provider as orchestrator
        provider = list(configured.keys())[0]
        routing["default"] = provider
        print(f"  Orchestrator: {bold(provider)} (only provider)")
    else:
        # Ask which is the orchestrator
        items = [(p, p, i == 0) for i, p in enumerate(configured.keys())]
        orchestrator = whiptail_radiolist("Select orchestrator:", items)
        routing["default"] = orchestrator
        print(f"  Orchestrator: {bold(orchestrator)}")

    print(f"\n  {check_mark(True)} Triage, workers, and failover will be auto-assigned at runtime.")
    print(f"  (Phase 2: LLM-assisted role reasoning)")

    _wlog(f"role_assignment: {routing}")
    return routing


# ─ Platform Setup [H] ──────────────────────────────────────────────────────────

def platform_setup(adapters: list[str], adapter_config: dict, system_ip: str) -> dict:
    """Generate docker-compose and placeholder configs for selected adapters."""
    header("Step 3b — Platform Setup")

    notify_cfg = {}

    for adapter in adapters:
        setup_type = adapter_config.get(adapter, "remote")

        if adapter == "mattermost" and setup_type == "local":
            print(f"\n  Mattermost (local Docker)")
            docker_dir = Path.home() / "dockers" / "mattermost"
            docker_dir.mkdir(parents=True, exist_ok=True)

            # docker-compose.yml
            compose = f"""version: '3.8'
services:
  mattermost:
    image: mattermost/mattermost-team-edition:latest
    ports:
      - "8065:8080"
    environment:
      MM_SERVICESETTINGS_SITEURL: "http://{system_ip}:8065"
    volumes:
      - ./data:/mattermost/data
      - ./logs:/mattermost/logs
      - ./config:/mattermost/config
    restart: unless-stopped
"""
            (docker_dir / "docker-compose.yml").write_text(compose)

            # README
            readme = f"""# Mattermost Setup

Start the server:
  docker compose up -d

Access:
  http://{system_ip}:8065

Create admin account, then create a bot user and copy its token.

Add token to ~/.env:
  MM_BOT_TOKEN=<token>

Then restart Nexus:
  sudo systemctl restart nexus
"""
            (docker_dir / "README.txt").write_text(readme)

            print(f"    {check_mark(True)} docker-compose written to {docker_dir}/")
            print(f"    Read {docker_dir}/README.txt for next steps")
            notify_cfg["adapter"] = "mattermost"
            notify_cfg["url"] = f"http://{system_ip}:8065"
            _wlog(f"platform_setup: mattermost docker-compose written")

    return notify_cfg


# ─ Config Writing ──────────────────────────────────────────────────────────────

def write_configs(configured: dict, routing: dict, notify_cfg: dict, system_ip: str = "localhost") -> None:
    """Write providers.yaml and .env."""
    header("Step 4 — Writing Configuration")

    # providers.yaml
    providers_config = {
        "providers": {provider: {} for provider in configured},
        "routing": routing,
    }
    yaml_path = CONFIG_DIR / "providers.yaml"
    yaml_path.write_text(yaml.dump(providers_config, default_flow_style=False))
    print(f"  {check_mark(True)} {yaml_path.relative_to(PROJECT_ROOT)}")

    # .env
    env_lines = []
    for provider in configured:
        if provider == "anthropic":
            env_lines.append("# ANTHROPIC_API_KEY set via: claude auth login")
        elif provider == "ollama":
            env_lines.append(f"OLLAMA_ENDPOINT=http://{system_ip}:11434")
    env_path = ENV_FILE
    env_path.write_text("\n".join(env_lines) + "\n")
    print(f"  {check_mark(True)} {env_path.relative_to(PROJECT_ROOT)}")

    print()
    _wlog(f"write_configs: providers={configured}, routing={routing}")


# ─ Main ────────────────────────────────────────────────────────────────────────

async def run() -> None:
    """Main wizard flow."""
    print(f"\n  {bold('╔═════════════════════════════════════╗')}")
    print(f"  {bold('║  Multi-LLM Nexus — Setup Wizard v2  ║')}")
    print(f"  {bold('║  Your AI platform. Your rules.      ║')}")
    print(f"  {bold('╚═════════════════════════════════════╝')}")
    print()

    system_ip = get_system_ip()
    print(f"  System IP: {bold(system_ip)} (used as default for local service endpoints)")
    print()

    # [A] System Scan
    scan = system_scan()
    print_scan(scan)
    if not ask_yn("Continue to provider setup?"):
        print("  Cancelled.")
        return

    # [B] System Identity
    agent_name, system_name = system_identity()

    # Update identity templates
    for fname in ("SOUL.md", "OPERATING_PROCEDURES.md", "AI_CONTEXT.md"):
        fpath = SYSTEM_ROOT / fname
        if fpath.exists():
            content = fpath.read_text()
            content = content.replace("[ORCHESTRATOR_NAME]", agent_name)
            content = content.replace("[SYSTEM_NAME]", system_name)
            fpath.write_text(content)

    # [C] Hardware Detection
    hw = await hardware_detection()

    # [D] Provider Selection
    cloud_selected, local_selected = provider_selection(hw)

    # [E] Adapter Selection
    adapter_selected, adapter_config = adapter_selection()

    # [F] Provider Configuration
    configured = await configure_providers(cloud_selected, local_selected, system_ip)

    if not configured:
        print(yellow("\n  No providers configured yet — credentials can be added after install."))
        print(dim("  Re-run anytime: python -m src.setup.wizard"))
        return

    # [G] Role Assignment
    routing = role_assignment(configured)

    # [H] Platform Setup
    notify_cfg = platform_setup(adapter_selected, adapter_config, system_ip)

    # [I] Service Install — handled by bootstrap.sh

    # [J] Config Writing
    write_configs(configured, routing, notify_cfg, system_ip)

    # Summary
    header("Setup Complete")
    print(f"  Providers configured: {len(configured)}")
    print(f"  Primary:  {bold(routing.get('default', '—'))}")
    print(f"  Adapters: {', '.join(adapter_selected) if adapter_selected else '(none)'}")
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
