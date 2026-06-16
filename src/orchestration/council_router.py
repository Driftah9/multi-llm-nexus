"""Council router — routing decision layer, downstream of triage.

Given triage output + system state + budget, decide: solo or council?
Which provider(s)? This is PURE DECISION LOGIC — no network calls, no LLM.
Returns a RoutePlan; the council_executor acts on it.

Council fires only when BOTH axes cross threshold:
  - complexity  (derived from triage effort)
  - stakes      (caller-supplied: "is being wrong expensive?")
...AND it's affordable AND not manually locked off.

Manual override can force council on or off regardless of the gate.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Triage effort → complexity score
_EFFORT_COMPLEXITY = {"low": 0.1, "medium": 0.4, "high": 0.7, "max": 1.0}

# Both must be met for the council gate to open
COMPLEXITY_THRESHOLD = 0.6
STAKES_THRESHOLD = 0.6

# Minimum council size worth convening
MIN_COUNCIL = 2


@dataclass
class SystemState:
    """Snapshot of what providers are available right now."""
    online_providers: List[str] = field(default_factory=list)   # excludes rate-limited/offline
    primary: str = "primary"                                     # current orchestrator/chairman
    local_llm_up: bool = True


@dataclass
class RoutePlan:
    mode: str                        # "solo" | "council"
    chairman: str                    # who synthesizes (primary orchestrator)
    members: List[str] = field(default_factory=list)   # council members (council mode)
    provider: Optional[str] = None   # chosen provider (solo mode)
    reason: str = ""


def decide(
    *,
    effort: str,
    stakes: float,
    state: SystemState,
    domain: str = "general",
    capability_map=None,            # optional CapabilityMap for solo provider pick
    budget_ok: bool = True,         # can we afford a council call right now?
    force: Optional[str] = None,    # "council" | "solo" | None  (manual override)
) -> RoutePlan:
    """Return a routing plan. Pure logic — no I/O."""
    complexity = _EFFORT_COMPLEXITY.get(effort, 0.4)
    chairman = state.primary

    if force == "solo":
        return RoutePlan(
            mode="solo", chairman=chairman,
            provider=_pick_solo(domain, state, capability_map),
            reason="forced solo (manual override)",
        )

    council_members = [p for p in state.online_providers if p != chairman]

    if force == "council":
        if len(council_members) + 1 >= MIN_COUNCIL and budget_ok:
            return RoutePlan(mode="council", chairman=chairman, members=council_members,
                             reason="forced council (manual override)")
        return RoutePlan(
            mode="solo", chairman=chairman,
            provider=_pick_solo(domain, state, capability_map),
            reason="council forced but unavailable (members/budget) -> solo",
        )

    meets_gate = complexity >= COMPLEXITY_THRESHOLD and stakes >= STAKES_THRESHOLD
    can_convene = (len(council_members) + 1) >= MIN_COUNCIL and budget_ok

    if meets_gate and can_convene:
        return RoutePlan(
            mode="council", chairman=chairman, members=council_members,
            reason=(f"gate met (complexity={complexity:.2f}>={COMPLEXITY_THRESHOLD}, "
                    f"stakes={stakes:.2f}>={STAKES_THRESHOLD}) and convenable"),
        )

    if meets_gate and not can_convene:
        why = "members/budget unavailable"
    else:
        why = f"gate not met (complexity={complexity:.2f}, stakes={stakes:.2f})"
    return RoutePlan(
        mode="solo", chairman=chairman,
        provider=_pick_solo(domain, state, capability_map),
        reason=f"solo: {why}",
    )


def _pick_solo(domain: str, state: SystemState, capability_map) -> str:
    """Choose the single best provider for a solo route."""
    candidates = state.online_providers or [state.primary]
    if capability_map is not None:
        try:
            return capability_map.choose(domain, candidates)["provider"]
        except Exception:
            pass
    return state.primary if state.primary in candidates else candidates[0]
