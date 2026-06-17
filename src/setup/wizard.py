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
    Multi-select numbered text menu. whiptail is not used — it writes TUI
    rendering (ANSI escape codes) to stdout when stdout is piped, making its
    output uncapturable and the dialog invisible. Plain text is reliable in all
    contexts (SSH, script(1), pipes).
    items: [(key, label, default_selected), ...]
    Returns: list of selected keys
    """
    _wlog(f"whiptail_checklist: text menu for {len(items)} items")
    print(f"\n{title}")
    for i, (key, label, selected) in enumerate(items, 1):
        marker = "*" if selected else " "
        print(f"  ({i:2}) [{marker}] {label}", flush=True)
    print(flush=True)
    raw = input("  Enter numbers separated by commas (e.g. 1,3,5): ").strip()
    sel: list[str] = []
    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(items):
                sel.append(items[idx][0])
        except ValueError:
            pass
    _wlog(f"whiptail_checklist: selected {sel}")
    return sel

def whiptail_radiolist(title: str, items: list[tuple[str, str, bool]]) -> str:
    """
    Single-select numbered text menu. See whiptail_checklist for why whiptail
    is not used.
    items: [(key, label, default_selected), ...]
    Returns: selected key or empty string
    """
    _wlog(f"whiptail_radiolist: text menu for {len(items)} items")
    print(f"\n{title}")
    for i, (key, label, selected) in enumerate(items, 1):
        marker = ">" if selected else " "
        print(f"  ({i}) [{marker}] {label}", flush=True)
    print(flush=True)
    raw = input("  Enter number: ").strip()
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            result = items[idx][0]
            _wlog(f"whiptail_radiolist: selected {result}")
            return result
    except ValueError:
        pass
    _wlog(f"whiptail_radiolist: no selection")
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
    """Scan for active providers, CLI tools, Python packages, local services, API keys."""
    scan = {
        "active_providers": {},   # from existing providers.yaml
        "tools": {},
        "packages": {},
        "services": {},
        "env_keys": {},
    }

    # Active providers from existing providers.yaml
    yaml_path = CONFIG_DIR / "providers.yaml"
    if yaml_path.exists():
        try:
            cfg = yaml.safe_load(yaml_path.read_text()) or {}
            scan["active_providers"] = cfg.get("providers", {})
        except Exception:
            pass

    # CLI tools
    for tool in ["claude", "ollama", "docker"]:
        result = subprocess.run(
            f"command -v {tool}", shell=True, capture_output=True, text=True
        )
        scan["tools"][tool] = result.stdout.strip() if result.returncode == 0 else None

    # Python packages — collected from registry (not hardcoded)
    required_packages: set[str] = set()
    for pdef in PROVIDERS.values():
        required_packages.update(pdef.packages)
    for pkg in sorted(required_packages):
        try:
            scan["packages"][pkg] = importlib.util.find_spec(pkg) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            scan["packages"][pkg] = False

    # Local services
    # Ollama: CLI must exist AND endpoint must respond — both required
    ollama_cli = subprocess.run("command -v ollama", shell=True, capture_output=True).returncode == 0
    try:
        r = subprocess.run(
            "curl -s http://localhost:11434/api/tags",
            shell=True, capture_output=True, timeout=2, check=False
        )
        ollama_http = r.returncode == 0
    except Exception:
        ollama_http = False
    scan["services"]["ollama"] = ollama_cli and ollama_http

    # API keys from .env — check all env_vars in registry
    all_env_vars: set[str] = set()
    for pdef in PROVIDERS.values():
        all_env_vars.update(pdef.env_vars)
    if ENV_FILE.exists():
        content = ENV_FILE.read_text()
        for line in content.splitlines():
            for key in all_env_vars:
                if line.startswith(f"{key}=") and len(line.split("=", 1)[1].strip()) > 0:
                    scan["env_keys"][key] = True

    _wlog(f"system_scan: active={list(scan['active_providers'].keys())}, tools={scan['tools']}")
    return scan


def print_scan(scan: dict) -> None:
    """Pretty-print system scan results."""
    header("System Scan")

    # Active providers — shown first so operator knows what's already running
    print("\n  Active Providers")
    active = scan.get("active_providers", {})
    if active:
        for pname, pcfg in active.items():
            pdef = PROVIDERS.get(pname.rsplit("_", 1)[0] if pname[-1].isdigit() else pname)
            label = pdef.display_name if pdef else pname
            print(f"    {check_mark(True)} {pname:25} {dim(label)}")
    else:
        print(f"    {dim('none configured yet')}")

    print("\n  CLI Tools")
    for tool in ["claude", "ollama", "docker"]:
        path = scan["tools"].get(tool)
        if path:
            print(f"    {check_mark(True)} {tool:15} {dim(path)}")
        else:
            print(f"    {check_mark(False)} {tool:15} not found")

    print("\n  Python Packages")
    for pkg, ok in sorted(scan["packages"].items()):
        print(f"    {check_mark(ok)} {pkg:35} {'installed' if ok else 'not installed'}")

    print("\n  Local Services")
    print(f"    {check_mark(scan['services'].get('ollama', False))} Ollama  " +
          ("reachable" if scan["services"].get("ollama") else "not reached"))

    print("\n  API Keys in .env")
    if scan["env_keys"]:
        for key in sorted(scan["env_keys"]):
            print(f"    {check_mark(True)} {key}")
    else:
        print(f"    {dim('none found in .env')}")

    print()


# ─ System Identity [B] ────────────────────────────────────────────────────────

def system_identity() -> tuple[str, str]:
    """Prompt for orchestrator name and system hostname. Skips if already configured."""
    # Detect if identity was already set (placeholder replaced = first-run already done)
    soul_path = SYSTEM_ROOT / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text()
        if "[ORCHESTRATOR_NAME]" not in content:
            # Already configured — extract name from first heading (stop at " — " subtitle)
            agent_name = ""
            for line in content.splitlines():
                if line.startswith("# "):
                    agent_name = line[2:].strip().split(" — ")[0].strip()
                    break
            system_name = get_hostname()
            print(f"\n  {check_mark(True)} Identity already configured: {bold(agent_name or 'unknown')}")
            print(f"  {dim('Edit SOUL.md and AI_CONTEXT.md to change.')}\n")
            _wlog(f"system_identity: already set, agent={agent_name}")
            return agent_name, system_name

    # First-time setup
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

def provider_selection(hw: dict, active_providers: dict = None) -> list[str]:
    """
    Multi-select providers from registry. Shows already-configured providers with
    active count. Supports selecting the same provider multiple times (multi-instance).
    """
    header("Step 1 — Add Providers")
    active = active_providers or {}

    # Count active instances per base provider type
    active_counts: dict[str, int] = {}
    for pname in active:
        # Strip numeric suffix: gemini_2 → gemini
        base = pname.rsplit("_", 1)[0] if pname[-1:].isdigit() else pname
        active_counts[base] = active_counts.get(base, 0) + 1

    if active:
        print(f"  {len(active)} provider(s) already configured — select NEW ones to add,")
        print(f"  or re-select an active provider to add another connection.\n")
    else:
        print("  Select all providers you have access to.\n")

    # Generate items from registry
    all_items = []
    for ptype, pdef in PROVIDERS.items():
        count = active_counts.get(ptype, 0)
        if count > 0:
            label = f"{pdef.display_name}  [{count} active — add another?]"
        else:
            label = pdef.display_name
        # Pre-select ollama if hardware supports it and not already active
        pre_selected = (ptype == "ollama" and hw.ram_gb >= 8 and count == 0)
        all_items.append((ptype, label, pre_selected))

    selected_keys = whiptail_checklist(
        "Select providers (SPACE to select, ENTER when done):",
        all_items
    )

    # Filter: only keep selections that are either new OR explicitly re-selected (multi-instance)
    to_configure = []
    for key in selected_keys:
        to_configure.append(key)

    print(f"\n  {check_mark(True)} Providers to configure: {len(to_configure)}")
    _wlog(f"provider_selection: {to_configure}")

    return to_configure


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
    selected_providers: list[str],
    system_ip: str
) -> dict:
    """Configure selected providers sequentially (CLI install, API keys, tests)."""
    from .provider_setup import setup_provider

    header("Step 2 — Configure Providers")

    configured = {}

    for provider_type in selected_providers:
        result = await setup_provider(provider_type, system_ip)
        if result:
            configured.update(result)

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
    """
    Merge new provider configs into existing providers.yaml + .env.
    Supports multi-instance providers: second gemini becomes gemini_2, etc.
    """
    header("Step 4 — Writing Configuration")

    yaml_path = CONFIG_DIR / "providers.yaml"
    env_path = ENV_FILE

    # Load existing configs to merge into
    existing_providers: dict = {}
    existing_routing: dict = {}
    if yaml_path.exists():
        try:
            existing_cfg = yaml.safe_load(yaml_path.read_text()) or {}
            existing_providers = existing_cfg.get("providers", {})
            existing_routing = existing_cfg.get("routing", {})
        except Exception:
            pass

    # Load existing .env keys
    existing_env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing_env[k.strip()] = v.strip()

    new_env: dict[str, str] = {}

    for provider_type, config in configured.items():
        # Strip any suffix to get the base type for registry lookup
        base_type = provider_type.rsplit("_", 1)[0] if provider_type[-1:].isdigit() else provider_type
        pdef = PROVIDERS.get(base_type) or PROVIDERS.get(provider_type)
        if not pdef:
            continue

        # Generate unique key for this instance (gemini → gemini, second → gemini_2)
        instance_key = provider_type
        if instance_key in existing_providers:
            n = 2
            while f"{provider_type}_{n}" in existing_providers:
                n += 1
            instance_key = f"{provider_type}_{n}"

        # Build provider entry
        entry: dict = {"type": pdef.type_id}
        if pdef.models:
            entry["model"] = list(pdef.models.keys())[0]
        if pdef.base_url:
            entry["base_url"] = pdef.base_url

        if config.get("api_key"):
            env_var = config.get("env_var", f"{pdef.type_id.upper()}_API_KEY")
            # Multi-instance: suffix env var to avoid collision (GEMINI_API_KEY_2)
            if env_var in existing_env or env_var in new_env:
                suffix = instance_key.split("_")[-1] if instance_key[-1:].isdigit() else "2"
                env_var = f"{env_var}_{suffix}"
            entry["api_key"] = f"${{{env_var}}}"
            new_env[env_var] = config["api_key"]

        if config.get("endpoint"):
            entry["endpoint"] = config["endpoint"]

        existing_providers[instance_key] = entry
        print(f"  {check_mark(True)} Provider: {instance_key}")

    # Merge routing (new routing wins)
    existing_routing.update(routing)

    # Write providers.yaml
    final_cfg = {"providers": existing_providers, "routing": existing_routing}
    yaml_path.write_text(yaml.dump(final_cfg, default_flow_style=False))
    print(f"  {check_mark(True)} {yaml_path.relative_to(PROJECT_ROOT)}")

    # Write .env — merge new keys with existing
    existing_env.update(new_env)
    env_lines = [f"{k}={v}" for k, v in sorted(existing_env.items())]
    env_path.write_text("\n".join(env_lines) + "\n" if env_lines else "# Provider API keys\n")
    print(f"  {check_mark(True)} {env_path.relative_to(PROJECT_ROOT)}")

    print()
    _wlog(f"write_configs: providers={list(existing_providers.keys())}, routing={existing_routing}")


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

    # [A] System Scan — always run first
    scan = system_scan()
    print_scan(scan)
    active_providers = scan.get("active_providers", {})
    is_rerun = bool(active_providers)

    if is_rerun:
        # ── Re-run mode: top-level menu ──────────────────────────────────────
        header("What would you like to do?")
        choice = whiptail_radiolist(
            "Choose an option:",
            [
                ("providers", "Add or configure AI providers", True),
                ("adapters", "Add or configure platform adapters  (Mattermost, Discord, Telegram)", False),
            ],
        )
        if not choice:
            print("  Cancelled.")
            return

        if choice == "providers":
            # Identity check (prints "already configured" and returns early)
            agent_name, system_name = system_identity()

            # Hardware scan (brief)
            hw = await hardware_detection()

            # Provider selection
            selected_providers = provider_selection(hw, active_providers)
            if not selected_providers:
                print("\n  Nothing selected. Exiting.")
                return

            # Configure selected providers
            configured = await configure_providers(selected_providers, system_ip)
            if not configured:
                print(yellow("\n  No providers were configured."))
                return

            # Role assignment across all providers (new + existing)
            all_providers = {**{k: {} for k in active_providers}, **configured}
            if len(all_providers) > 1:
                routing = role_assignment(all_providers)
            else:
                routing = {"default": list(all_providers.keys())[0]}
                header("Step 3 — Role Assignment")
                print(f"  Orchestrator: {bold(list(all_providers.keys())[0])} (only provider)")
                print(f"\n  {check_mark(True)} Triage, workers, and failover auto-assigned at runtime.")

            write_configs(configured, routing, {}, system_ip)

            header("Providers Updated")
            print(f"  Added: {', '.join(configured.keys())}")
            print(f"  Total providers: {len(all_providers)}")
            print()

        elif choice == "adapters":
            adapter_selected, adapter_config = adapter_selection()
            if not adapter_selected:
                print("\n  Nothing selected. Exiting.")
                return
            platform_setup(adapter_selected, adapter_config, system_ip)
            header("Adapters Updated")
            print(f"  Configured: {', '.join(adapter_selected)}")
            print(f"  Edit {cyan('config/adapters.yaml')} and set {cyan('enabled: true')} for each adapter,")
            print(f"  then add tokens to {cyan('.env')} and restart Nexus.")
            print()

        return  # re-run done

    # ── First-time install flow ───────────────────────────────────────────────
    if not ask_yn("Continue to provider setup?"):
        print("  Cancelled.")
        return

    # [B] System Identity — skipped if already configured
    agent_name, system_name = system_identity()

    # Update identity templates (only if placeholders still present)
    for fname in ("SOUL.md", "OPERATING_PROCEDURES.md", "AI_CONTEXT.md"):
        fpath = SYSTEM_ROOT / fname
        if fpath.exists():
            content = fpath.read_text()
            if "[ORCHESTRATOR_NAME]" in content or "[SYSTEM_NAME]" in content:
                content = content.replace("[ORCHESTRATOR_NAME]", agent_name)
                content = content.replace("[SYSTEM_NAME]", system_name)
                fpath.write_text(content)

    # [C] Hardware Detection
    hw = await hardware_detection()

    # [D] Provider Selection
    selected_providers = provider_selection(hw, {})

    # [F] Provider Configuration — done BEFORE adapter selection
    configured = await configure_providers(selected_providers, system_ip)

    routing = {}
    if configured:
        # [G] Role Assignment
        routing = role_assignment(configured)
    else:
        print(yellow("\n  No providers configured yet — credentials can be added after install."))
        print(dim("  Re-run anytime: python -m src.setup.wizard"))

    # [E] Adapter Selection — after providers so user has full context
    adapter_selected, adapter_config = adapter_selection()

    # [H] Platform Setup
    notify_cfg = platform_setup(adapter_selected, adapter_config, system_ip)

    # [I] Service Install — handled by bootstrap.sh

    # [J] Config Writing
    if configured:
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
