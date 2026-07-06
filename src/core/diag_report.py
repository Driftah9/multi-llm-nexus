"""Self-diagnostic report generator.

Produces a single Markdown document describing this deployment's own state — versions,
a hardware/capability snapshot, which features are **active vs deferred**, the configured
providers (by type/tier/role, never by key), provider health, and local-service
reachability — so an operator can attach it to a GitHub issue, email, or anywhere.

Design contract:
  * ONE generator, many doors. The ops-board Diag tab, a `nexus doctor` CLI, and an
    adapter `diag` command all call generate_markdown(); none re-implement collection
    or redaction.
  * NO phone-home. This module only *produces a string and a file*. It never sends
    anything anywhere. The operator is the sole transmitter — they download/copy/paste.
  * Fail-closed redaction. Collectors emit only safe primitives (booleans, counts,
    types, tiers, roles). A final scrub pass masks anything secret-shaped
    (keys/tokens/passwords/emails) and, by default, home paths + LAN IPs. A value that
    looks sensitive is masked, not passed.

Everything is read from the operator's own config and a live hardware scan — there is no
baked-in provider roster and no operator data in this file.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"


def _config_dir() -> Path:
    return Path(os.environ.get("NEXUS_CONFIG_DIR", str(_PROJECT_ROOT / "config")))


def _data_dir() -> Path:
    return Path(os.environ.get("NEXUS_DATA_DIR", str(_PROJECT_ROOT / "data")))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_env_ref(value) -> tuple[str | None, str | None]:
    """For an `api_key` field that is `${VAR}`, return (var_name, current_value).
    For a literal, return (None, the_literal). Values never leave this function as
    anything but a presence boolean upstream."""
    if not isinstance(value, str):
        return None, None
    m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value.strip())
    if m:
        return m.group(1), os.environ.get(m.group(1))
    return None, value


# ── Collectors (emit only safe primitives) ─────────────────────────────────────

def _collect_build() -> dict:
    ver = "unknown"
    if _PYPROJECT.exists():
        m = re.search(r'version\s*=\s*"([^"]+)"', _safe(_PYPROJECT.read_text, "") or "")
        if m:
            ver = m.group(1)
    return {
        "nexus_version": ver,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
    }


def _collect_system() -> dict:
    mem_total_gb = mem_avail_gb = None
    meminfo = _safe(lambda: Path("/proc/meminfo").read_text(), "") or ""
    tot = re.search(r"MemTotal:\s+(\d+) kB", meminfo)
    avail = re.search(r"MemAvailable:\s+(\d+) kB", meminfo)
    if tot:
        mem_total_gb = round(int(tot.group(1)) / 1024 / 1024, 1)
    if avail:
        mem_avail_gb = round(int(avail.group(1)) / 1024 / 1024, 1)

    gpu = bool(shutil.which("nvidia-smi"))
    gpu_names = []
    if gpu:
        out = _safe(lambda: subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5).stdout.strip(), "") or ""
        gpu_names = [g.strip() for g in out.splitlines() if g.strip()]

    return {
        "cpu_count": os.cpu_count(),
        "mem_total_gb": mem_total_gb,
        "mem_avail_gb": mem_avail_gb,
        "gpu_present": gpu,
        "gpu_names": gpu_names,
        "disk_free_gb": _safe(lambda: round(shutil.disk_usage("/").free / 1e9, 1)),
    }


def _load_providers_config() -> dict:
    """The operator's runtime providers.yaml (fall back to the .example for a fresh
    checkout so a never-configured install still reports something sensible)."""
    for name in ("providers.yaml", "providers.yaml.example"):
        path = _config_dir() / name
        if path.exists():
            return _safe(lambda: yaml.safe_load(path.read_text()) or {}, {}) or {}
    return {}


def _collect_providers() -> list[dict]:
    """Providers by type/tier/role — NEVER keys. key_present is a boolean only."""
    cfg = _load_providers_config()
    providers = cfg.get("providers", {}) or {}
    rows = []
    for name, pc in providers.items():
        if not isinstance(pc, dict):
            continue
        cost_class = pc.get("cost_class", "")
        is_local = cost_class == "local" or pc.get("type") == "ollama"
        key_present = None
        if is_local:
            key_present = True  # no key needed
        elif "api_key" in pc:
            var, val = _resolve_env_ref(pc.get("api_key"))
            key_present = bool(val) and val not in ("no-key", "")
        rows.append({
            "name": name,
            "type": pc.get("type", "?"),
            "tier": pc.get("tier", "—"),
            "role": pc.get("role", "worker"),
            "cost_class": cost_class or "—",
            "key_present": key_present,
        })
    return rows


def _usable_executor_count(providers: list[dict]) -> int:
    return sum(1 for p in providers if p.get("key_present") in (True, None))


def _shared_store_configured() -> bool:
    return any(os.environ.get(k) for k in
               ("NEXUS_COORD_REDIS_URL", "NEXUS_COORD_REDIS_HOST"))


def _collect_feature_gates(system: dict, providers: list[dict]) -> list[dict]:
    """Use the real capability_gate + each feature's declared CapabilityRequirement.
    A feature defers only when nothing available meets its bar."""
    try:
        from . import capability_gate as cg
    except Exception:
        from core import capability_gate as cg  # type: ignore

    snapshot = cg.SystemCapabilities(
        capable_executors=_usable_executor_count(providers),
        structured_output=bool(providers),
        shared_state=_shared_store_configured(),
        ram_gb=system.get("mem_total_gb") or 0.0,
        gpu=bool(system.get("gpu_present")),
    )

    reqs = []
    reqs.append(cg.CapabilityRequirement("Provider failover", min_capable_executors=2))
    # Pull the council requirement straight from the feature so the report can never
    # drift from what the feature actually gates on.
    council_req = _safe(lambda: __import__(
        "src.core.council_lease", fromlist=["REQUIREMENT"]).REQUIREMENT)
    if council_req is None:
        council_req = _safe(lambda: __import__(
            "core.council_lease", fromlist=["REQUIREMENT"]).REQUIREMENT)
    if council_req is not None:
        reqs.append(council_req)
    else:
        reqs.append(cg.CapabilityRequirement(
            "Council failover", min_capable_executors=2, needs_shared_state=True))
    reqs.append(cg.CapabilityRequirement("Local LLM offload", min_ram_gb=8.0))

    gates = []
    for req in reqs:
        res = cg.evaluate(req, snapshot)
        gates.append({
            "feature": getattr(req, "name", "?"),
            "status": "active" if res.active else "deferred",
            "reason": res.reason,
        })
    return gates


def _collect_provider_health() -> list[dict]:
    """Read the optional persisted provider-health snapshot (provider_chain health_path).
    Schema: {name: {failures, cooldown_until, ...}}. Absent → empty (all healthy)."""
    path = os.environ.get("NEXUS_PROVIDER_HEALTH_PATH") or str(_data_dir() / "provider_health.json")
    p = Path(path)
    if not p.exists():
        return []
    data = _safe(lambda: __import__("json").loads(p.read_text()), {}) or {}
    now = _safe(lambda: __import__("time").time(), 0) or 0
    down = []
    for name, rec in (data.items() if isinstance(data, dict) else []):
        if not isinstance(rec, dict):
            continue
        cd = rec.get("cooldown_until", 0) or 0
        if cd > now or rec.get("failures"):
            down.append({
                "provider": name,
                "failures": rec.get("failures", "—"),
                "cooldown_remaining": (f"{int(cd - now)}s" if cd > now else "—"),
            })
    return down


def _collect_services(providers_cfg: dict) -> list[dict]:
    """Probe local endpoints DISCOVERED from the operator's config — not a fixed list.
    Any localhost endpoint/base_url in providers.yaml, plus a coordination store if one
    is configured. Nothing operator-specific is assumed."""
    targets: dict[tuple, str] = {}
    for name, pc in (providers_cfg.get("providers", {}) or {}).items():
        if not isinstance(pc, dict):
            continue
        url = pc.get("endpoint") or pc.get("base_url") or ""
        m = re.match(r"https?://([^:/]+):(\d+)", url)
        if m and m.group(1) in ("localhost", "127.0.0.1", "0.0.0.0"):
            targets[(m.group(1), int(m.group(2)))] = f"{pc.get('type', name)} ({name})"
    # Coordination store, if configured
    rhost = os.environ.get("NEXUS_COORD_REDIS_HOST")
    if rhost in ("localhost", "127.0.0.1"):
        rport = int(os.environ.get("NEXUS_COORD_REDIS_PORT", "6379") or 6379)
        targets[(rhost, rport)] = "coordination store (redis)"
    return [
        {"label": label, "port": port, "reachable": _port_open(host, port)}
        for (host, port), label in sorted(targets.items(), key=lambda kv: kv[0][1])
    ]


def _collect_memory() -> dict:
    # RAG store presence — config-driven dir, never its contents.
    rag_dir = Path(os.environ.get("NEXUS_RAG_DB", str(_data_dir() / "rag")))
    rag_present = rag_dir.exists()
    rag_size_mb = None
    if rag_present:
        rag_size_mb = _safe(lambda: round(
            sum(f.stat().st_size for f in rag_dir.rglob("*") if f.is_file()) / 1e6, 1))
    # Identity registry — COUNT ONLY, never names/ids.
    people_count = None
    data = _safe(lambda: (__import__("src.core.identity", fromlist=["load"]).load()))
    if data is None:
        data = _safe(lambda: (__import__("core.identity", fromlist=["load"]).load()))
    if isinstance(data, dict):
        people_count = (1 if data.get("owner") else 0) + len(data.get("people") or {})
    return {
        "rag_db_present": rag_present,
        "rag_db_size_mb": rag_size_mb,
        "people_registered": people_count,
    }


# ── Redaction (fail-closed) ─────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r'(?i)\b([a-z0-9_]*(?:api[_-]?key|secret|token|password|passwd|auth)[a-z0-9_]*)\s*[:=]\s*\S+'),
     r'\1=‹redacted›'),
    (re.compile(r'\bsk-[A-Za-z0-9_\-]{8,}'), '‹redacted-key›'),
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}'), 'Bearer ‹redacted›'),
    (re.compile(r'\b[A-Fa-f0-9]{40,}\b'), '‹redacted-hash›'),
    (re.compile(r'\b[A-Za-z0-9+/]{48,}={0,2}\b'), '‹redacted-blob›'),
    (re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b'), '‹redacted-email›'),
]

_PATH_IP_PATTERNS = [
    (re.compile(r'/home/[A-Za-z0-9_.-]+'), '~'),
    (re.compile(r'/Users/[A-Za-z0-9_.-]+'), '~'),
    (re.compile(r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '‹lan-ip›'),
    (re.compile(r'\b192\.168\.\d{1,3}\.\d{1,3}\b'), '‹lan-ip›'),
    (re.compile(r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b'), '‹lan-ip›'),
]


def scrub(text: str, redact_paths: bool = True) -> str:
    """Final belt-and-suspenders pass. Secrets are ALWAYS masked; home paths + LAN IPs
    are masked by default (a GitHub-bound report shouldn't carry them)."""
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    if redact_paths:
        for pat, repl in _PATH_IP_PATTERNS:
            text = pat.sub(repl, text)
    return text


# ── Rendering ───────────────────────────────────────────────────────────────────

def _yn(v) -> str:
    if v is True:
        return "✅ yes"
    if v is False:
        return "❌ no"
    return "—"


def generate_markdown(redact_paths: bool = True) -> str:
    ts = _now_utc()
    build = _safe(_collect_build, {}) or {}
    system = _safe(_collect_system, {}) or {}
    providers_cfg = _safe(_load_providers_config, {}) or {}
    providers = _safe(_collect_providers, []) or []
    gates = _safe(lambda: _collect_feature_gates(system, providers), []) or []
    health = _safe(_collect_provider_health, []) or []
    services = _safe(lambda: _collect_services(providers_cfg), []) or []
    memory = _safe(_collect_memory, {}) or {}

    L = []
    L.append("# Nexus Diagnostic Report")
    L.append("")
    L.append(f"_Generated {ts.strftime('%Y-%m-%d %H:%M:%S UTC')} · paste into a GitHub issue, "
             "email, or anywhere you like._")
    L.append("")
    L.append("> **What this contains:** versions, a hardware/capability snapshot, which "
             "features are active vs deferred, configured providers **by type/tier/role**, "
             "provider health, and local-service reachability.")
    L.append("> **What it never contains:** API keys, tokens, passwords, `.env` values, "
             "memory content, conversation history, or other users' identities. "
             "Secret-shaped strings are masked automatically"
             + (" and home paths / LAN IPs are redacted." if redact_paths else ".") +
             " Review before you share.")
    L.append("")

    L.append("## Build")
    L.append(f"- **Nexus version:** {build.get('nexus_version', '?')}")
    L.append(f"- **Python:** {build.get('python', '?')}")
    L.append(f"- **Platform:** {build.get('platform', '?')}")
    L.append("")

    L.append("## System")
    L.append(f"- **CPU cores:** {system.get('cpu_count', '?')}")
    L.append(f"- **RAM:** {system.get('mem_total_gb', '?')} GB total"
             f" ({system.get('mem_avail_gb', '?')} GB available)")
    gpu_line = "yes" if system.get("gpu_present") else "no"
    if system.get("gpu_names"):
        gpu_line += " — " + ", ".join(system["gpu_names"])
    L.append(f"- **GPU:** {gpu_line}")
    L.append(f"- **Disk free (/):** {system.get('disk_free_gb', '?')} GB")
    L.append("")

    L.append("## Feature gates")
    L.append("_A feature is **deferred** when nothing in this deployment meets its bar; "
             "it lights up automatically as the deployment grows (more providers, a bigger "
             "local model, a coordination store)._")
    L.append("")
    if gates:
        L.append("| Feature | Status | Reason |")
        L.append("|---|---|---|")
        for g in gates:
            icon = "🟢 active" if g["status"] == "active" else "⚪ deferred"
            L.append(f"| {g['feature']} | {icon} | {g['reason']} |")
    else:
        L.append("_unavailable_")
    L.append("")

    L.append("## Providers")
    L.append("_By type, tier, and role. Keys are reported only as present/absent — "
             "never the value._")
    L.append("")
    if providers:
        L.append("| Provider | Type | Tier | Role | Cost class | Key |")
        L.append("|---|---|---|---|---|---|")
        for p in providers:
            L.append(f"| {p['name']} | {p['type']} | {p['tier']} | {p['role']} | "
                     f"{p['cost_class']} | {_yn(p['key_present'])} |")
    else:
        L.append("_no providers configured — copy `config/providers.yaml.example` to "
                 "`config/providers.yaml` and add at least one provider_")
    L.append("")

    L.append("## Provider health")
    if health:
        L.append("| Provider | Failures | Cooldown remaining |")
        L.append("|---|---|---|")
        for h in health:
            L.append(f"| {h['provider']} | {h['failures']} | {h['cooldown_remaining']} |")
    else:
        L.append("All providers healthy (none benched), or health persistence is not enabled.")
    L.append("")

    L.append("## Local services")
    if services:
        L.append("| Service | Port | Reachable |")
        L.append("|---|---|---|")
        for s in services:
            L.append(f"| {s['label']} | {s['port']} | {_yn(s['reachable'])} |")
    else:
        L.append("_no local services discovered in provider config_")
    L.append("")

    L.append("## Memory backends")
    L.append(f"- **RAG store present:** {_yn(memory.get('rag_db_present'))}"
             + (f" ({memory.get('rag_db_size_mb')} MB)" if memory.get("rag_db_size_mb") else ""))
    pc = memory.get("people_registered")
    L.append(f"- **People registered:** {pc if pc is not None else '—'} "
             "_(count only; no identities included)_")
    L.append("")

    L.append("---")
    L.append("_Generated locally by Nexus. Nothing was transmitted — you are sharing this "
             "by choice. No phone-home, no telemetry._")

    return scrub("\n".join(L), redact_paths=redact_paths)


def write_report(redact_paths: bool = True, out_dir: Path | None = None) -> Path:
    """Render and persist a timestamped .md. Returns the file path."""
    md = generate_markdown(redact_paths=redact_paths)
    out_dir = out_dir or (_data_dir() / "diag-reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / report_filename()
    path.write_text(md)
    return path


def report_filename() -> str:
    return f"nexus-diag-{_now_utc().strftime('%Y%m%dT%H%M%SZ')}.md"


if __name__ == "__main__":
    print(generate_markdown(redact_paths="--no-redact-paths" not in sys.argv))
