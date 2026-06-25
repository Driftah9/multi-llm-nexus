"""Capability gate — activate / degrade / DEFER a feature based on what the deployment can do.

The platform must run on the floor (1 provider + the smallest local LLM) and grow from
there. A feature is not simply on/off: it ACTIVATES (full), runs DEGRADED (configured down
for what the box can handle), or is DEFERRED — dark until hardware *or* providers meet its
requirement, then it auto-lights-up on the next evaluation. A feature defers only if
*nothing available* can meet its bar; a strong primary provider makes most features active.

Each feature declares a CapabilityRequirement (its bar). The gate evaluates it against a
SystemCapabilities snapshot (the right half — fed from the provider registry, hardware
scan, and infra config). This is the activation/deferral half of the execution model;
the offload/routing half lives in provider_chain + the (future) calibrated capability map.

Example — council failover declares it needs ≥2 capable executors and a shared store:

    REQ = CapabilityRequirement("council_failover", min_capable_executors=2, needs_shared_state=True)
    if evaluate(REQ, system_snapshot()).active:
        ... enable council ...
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityRequirement:
    """What a feature/tool needs to activate. Unmet requirements → the feature is DEFERRED
    (dark) until the deployment grows to meet them. Zero/False fields = no requirement."""
    name: str
    min_capable_executors: int = 1     # distinct providers/models that can do real work
    needs_structured_output: bool = False
    needs_shared_state: bool = False   # a shared coordination store (e.g. Redis-compatible)
    min_ram_gb: float = 0.0
    needs_gpu: bool = False
    notes: str = ""


@dataclass
class SystemCapabilities:
    """Snapshot of what THIS deployment can currently do — the right half of the gate.
    Built from the provider registry (how many capable executors), the hardware scan
    (ram/gpu), and infra config (is a shared store wired)."""
    capable_executors: int = 1
    structured_output: bool = True
    shared_state: bool = False
    ram_gb: float = 0.0
    gpu: bool = False


@dataclass
class GateResult:
    active: bool
    reason: str = ""
    unmet: tuple = ()

    def __bool__(self) -> bool:
        return self.active


def evaluate(req: CapabilityRequirement, system: SystemCapabilities) -> GateResult:
    """Decide whether `req` is met by `system`. Returns active=True when every requirement
    is satisfied, else active=False with the human-readable reasons it's deferred."""
    unmet = []
    if system.capable_executors < req.min_capable_executors:
        unmet.append(
            f"needs {req.min_capable_executors} capable executors, have {system.capable_executors}"
        )
    if req.needs_structured_output and not system.structured_output:
        unmet.append("needs a structured-output-capable executor")
    if req.needs_shared_state and not system.shared_state:
        unmet.append("needs a shared coordination store")
    if req.min_ram_gb and system.ram_gb < req.min_ram_gb:
        unmet.append(f"needs {req.min_ram_gb:g}GB RAM, have {system.ram_gb:g}")
    if req.needs_gpu and not system.gpu:
        unmet.append("needs a GPU")

    if unmet:
        return GateResult(False, f"{req.name} deferred — " + "; ".join(unmet), tuple(unmet))
    return GateResult(True, f"{req.name} active", ())
