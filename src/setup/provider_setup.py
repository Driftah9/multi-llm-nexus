"""
Standalone provider setup functions — called by wizard during install and by
DM handlers post-install. Each provider type (subscription, API key, local) has
a setup function that prompts for credentials, tests connection, returns config.
"""
import os
import subprocess
import sys
from typing import Optional

from ..providers.registry import PROVIDERS, ProviderDef


async def setup_subscription_cli(pdef: ProviderDef) -> Optional[dict]:
    """
    Setup subscription-based CLI provider (e.g., Claude Code CLI).
    Runs the provider's auth command (e.g., 'claude auth login').
    Returns: {provider_name: config} or None if auth failed.
    """
    from .wizard import check_mark, _wlog, ask_yn

    print(f"\n  {pdef.display_name}")

    # Check if CLI is installed
    result = subprocess.run(f"command -v {pdef.type_id.split('_')[0]}", shell=True, capture_output=True)
    cli_ready = result.returncode == 0

    if not cli_ready:
        print(f"    ✗ {pdef.type_id} CLI not found. Installing...")
        print(f"    → Running installer (https://claude.ai/code)...")
        subprocess.run(
            "curl -fsSL https://claude.ai/install.sh | bash",
            shell=True, check=False
        )
        # Reload PATH
        new_path = subprocess.run(
            'echo "$HOME/.local/bin:$HOME/.claude/local/bin:$PATH"',
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        os.environ["PATH"] = new_path
        cli_ready = subprocess.run(f"command -v {pdef.type_id.split('_')[0]}", shell=True, capture_output=True).returncode == 0

        if cli_ready:
            print(f"    {check_mark(True)} CLI installed and ready")
        else:
            print(f"    {check_mark(False)} Install script ran but CLI not on PATH.")
            print(f"    → Open a new shell and run: claude auth login")
            _wlog(f"{pdef.type_id}: install failed — CLI not on PATH after install")
            return None

    # Run auth
    if cli_ready:
        print(f"    → Launching: {pdef.type_id} auth")
        _wlog(f"{pdef.type_id}: launching auth via sys.stdin/stdout")
        result = subprocess.run(
            ["claude", "auth", "login"],
            stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
            check=False
        )
        _wlog(f"{pdef.type_id}: subprocess returned code {result.returncode}")

        # Test connection
        result = subprocess.run("claude -p 'ping' --output-format text", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"    {check_mark(True)} {pdef.display_name} configured")
            _wlog(f"{pdef.type_id}: configured and tested")
            return {pdef.type_id: {}}
        else:
            print(f"    {check_mark(False)} Connection test failed — run: claude auth login")
            _wlog(f"{pdef.type_id}: connection test failed")
            return None

    return None


async def setup_api_key(pdef: ProviderDef, system_ip: str = "localhost") -> Optional[dict]:
    """
    Setup API key provider (OpenAI, Gemini, Groq, etc.).
    Prompts for the API key, tests connection if possible, writes to env.
    Returns: {provider_name: config} or None if skipped.
    """
    from .wizard import check_mark, ask_secret, _wlog

    print(f"\n  {pdef.display_name}")

    if pdef.notes:
        print(f"    {pdef.notes}\n")

    env_var_name = pdef.env_vars[0] if pdef.env_vars else f"{pdef.type_id.upper()}_API_KEY"

    api_key = ask_secret(f"  {env_var_name}")

    if not api_key:
        print(f"    Skipping {pdef.type_id}")
        _wlog(f"{pdef.type_id}: skipped (no key provided)")
        return None

    # Test connection if possible
    test_ok = True
    if pdef.type_id == "gemini":
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            _ = genai.list_models()
            print(f"    {check_mark(True)} Connection test passed")
        except Exception as e:
            print(f"    {check_mark(False)} Connection test failed: {e}")
            test_ok = False
    elif pdef.type_id == "openai":
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.models.list()
            print(f"    {check_mark(True)} Connection test passed")
        except Exception as e:
            print(f"    {check_mark(False)} Connection test failed: {e}")
            test_ok = False
    else:
        # For other API providers, we'll just accept the key
        print(f"    {check_mark(True)} {pdef.display_name} ready (no live test available)")

    _wlog(f"{pdef.type_id}: configured with {env_var_name}, test_ok={test_ok}")

    # Return config dict with the key (to be written to .env later)
    return {
        pdef.type_id: {
            "api_key": api_key,
            "env_var": env_var_name,
        }
    }


async def setup_local(pdef: ProviderDef, system_ip: str = "localhost") -> Optional[dict]:
    """
    Setup local provider (Ollama, LM Studio, vLLM).
    Checks if service is running, optionally installs, returns endpoint.
    Returns: {provider_name: config} or None if skipped.
    """
    from .wizard import check_mark, ask_yn, _wlog

    print(f"\n  {pdef.display_name}")

    if pdef.type_id == "ollama":
        endpoint = f"http://{system_ip}:11434"
        print(f"    Endpoint: {endpoint}")

        # Check if running
        result = subprocess.run(
            f"curl -s {endpoint}/api/tags",
            shell=True, capture_output=True, timeout=2
        )
        if result.returncode != 0:
            if ask_yn("    Ollama not running. Install it?"):
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
                return None

        # Pull model in background
        model = "llama3.2:3b"
        print(f"    Pulling model: {model}")
        subprocess.Popen(
            f"ollama pull {model}",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"    {check_mark(True)} Ollama configured (model pulling in background)")
        _wlog("ollama: configured")
        return {pdef.type_id: {"endpoint": endpoint}}

    elif pdef.type_id == "lm_studio":
        endpoint = f"http://{system_ip}:1234/v1"
        print(f"    Endpoint: {endpoint}")
        result = subprocess.run(
            f"curl -s {endpoint}/models",
            shell=True, capture_output=True, timeout=2
        )
        if result.returncode == 0:
            print(f"    {check_mark(True)} LM Studio detected and running")
            _wlog("lm_studio: configured")
            return {pdef.type_id: {"endpoint": endpoint}}
        else:
            if ask_yn("    LM Studio not found. Continue anyway?"):
                print(f"    {check_mark(True)} LM Studio staged (not currently running)")
                _wlog("lm_studio: staged but not running")
                return {pdef.type_id: {"endpoint": endpoint}}
            else:
                _wlog("lm_studio: skipped")
                return None

    elif pdef.type_id == "vllm":
        endpoint = f"http://{system_ip}:8000/v1"
        print(f"    Endpoint: {endpoint}")
        result = subprocess.run(
            f"curl -s {endpoint}/models",
            shell=True, capture_output=True, timeout=2
        )
        if result.returncode == 0:
            print(f"    {check_mark(True)} vLLM detected and running")
            _wlog("vllm: configured")
            return {pdef.type_id: {"endpoint": endpoint}}
        else:
            if ask_yn("    vLLM not found. Continue anyway?"):
                print(f"    {check_mark(True)} vLLM staged (not currently running)")
                _wlog("vllm: staged but not running")
                return {pdef.type_id: {"endpoint": endpoint}}
            else:
                _wlog("vllm: skipped")
                return None

    return None


async def setup_provider(provider_type: str, system_ip: str = "localhost") -> Optional[dict]:
    """
    Dispatch to the appropriate setup function based on provider access mode.
    Returns: {provider_name: config} or None if setup failed/was skipped.
    """
    pdef = PROVIDERS.get(provider_type)
    if not pdef:
        return None

    # Route by access mode
    if "subscription_cli" in pdef.access_modes:
        return await setup_subscription_cli(pdef)
    elif "api_key" in pdef.access_modes:
        return await setup_api_key(pdef, system_ip)
    elif "local" in pdef.access_modes or provider_type in ["ollama", "lm_studio", "vllm"]:
        return await setup_local(pdef, system_ip)
    else:
        # Cloud account or other — not yet implemented
        from .wizard import _wlog
        _wlog(f"{provider_type}: access mode {pdef.access_modes} not yet supported in wizard")
        return None
