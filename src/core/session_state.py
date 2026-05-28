"""
SessionState — shared typed knowledge pool for parallel specialist agents.

Each orchestration round populates this object; it persists across rounds within
the same session so specialists never start cold on already-resolved questions.

Flow:
  dispatch() → create/load SessionState
  _invoke_one() → specialist runs, claims extracted from response
  detect_conflicts() → cross-reference claims, flag disagreements
  render_for_synthesis() → conflict/consensus summary injected into synthesis prompt
  OrchestratorResult carries updated state back for persistence

Ported from claude-brain (mattermost-daemon/src/session_state.py).
Provider-agnostic — no platform or model-specific code.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SpecialistClaim:
    id: str
    specialist_id: str
    claim_type: str    # "fact" | "recommendation" | "concern" | "gap" | "decision"
    claim_text: str
    confidence: str    # "high" | "medium" | "low"
    supports: list[str] = field(default_factory=list)        # claim IDs this supports
    conflicts_with: list[str] = field(default_factory=list)  # claim IDs this contradicts

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "specialist_id": self.specialist_id,
            "claim_type": self.claim_type,
            "claim_text": self.claim_text,
            "confidence": self.confidence,
            "supports": self.supports,
            "conflicts_with": self.conflicts_with,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpecialistClaim:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            specialist_id=data["specialist_id"],
            claim_type=data["claim_type"],
            claim_text=data["claim_text"],
            confidence=data.get("confidence", "medium"),
            supports=data.get("supports", []),
            conflicts_with=data.get("conflicts_with", []),
        )


# Patterns to extract structured claims from specialist markdown responses.
# Each tuple: (regex, claim_type, confidence). Ordered most→least specific.
# Operators can extend these patterns in their specialist profiles.
CLAIM_PATTERNS: list[tuple[str, str, str]] = [
    (r"\*\*Risk(?:\s+Level)?:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "concern", "high"),
    (r"\*\*Recommendation:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "recommendation", "high"),
    (r"\*\*Action Required:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "recommendation", "high"),
    (r"\*\*Finding:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "fact", "high"),
    (r"\*\*Deadline:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "fact", "high"),
    (r"\*\*Gap:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "gap", "medium"),
    (r"\*\*Decision:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "decision", "high"),
    (r"\*\*Confirmed:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "fact", "high"),
    (r"\*\*Conflict:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "concern", "high"),
    (r"\*\*Concern:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "concern", "medium"),
    (r"\*\*Note:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", "fact", "medium"),
]

# Keywords that trigger numeric conflict detection across specialists.
# Operators with domain-specific terms should extend via specialist profile config.
CONFLICT_KEYWORDS: list[str] = [
    "threshold", "limit", "cap", "deadline", "budget", "cost", "rate",
    "annual", "monthly", "quarterly", "daily", "total", "max", "min",
]


def extract_claims(specialist_id: str, text: str) -> list[SpecialistClaim]:
    """Extract structured claims from a specialist's markdown response."""
    claims = []
    seen_texts: set[str] = set()

    for pattern, claim_type, confidence in CLAIM_PATTERNS:
        for match in re.finditer(pattern, text, re.DOTALL):
            claim_text = match.group(1).strip()[:300]
            if claim_text and claim_text not in seen_texts:
                seen_texts.add(claim_text)
                claims.append(SpecialistClaim(
                    id=str(uuid.uuid4())[:8],
                    specialist_id=specialist_id,
                    claim_type=claim_type,
                    claim_text=claim_text,
                    confidence=confidence,
                ))

    return claims


@dataclass
class SessionState:
    """
    Shared knowledge pool for a single orchestration session.

    Persists across multiple orchestration rounds (follow-up messages in the
    same channel session). Specialists write claims into this pool; the
    orchestrator reads it to detect conflicts and inject context into the
    next round.
    """
    session_key: str
    channel_name: str
    workspace_name: str

    claims: dict[str, list[SpecialistClaim]] = field(default_factory=dict)

    consensus_facts: list[str] = field(default_factory=list)

    # Detected disagreements: list of {topic, specialist_a, claim_a, specialist_b, claim_b}
    conflicts: list[dict] = field(default_factory=list)

    locked_decisions: list[str] = field(default_factory=list)

    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ── Mutation ────────────────────────────────────────────────────────────

    def add_claims(self, specialist_id: str, claims: list[SpecialistClaim]) -> None:
        if specialist_id not in self.claims:
            self.claims[specialist_id] = []
        self.claims[specialist_id].extend(claims)
        self.updated_at = _now()

    def lock_decision(self, decision_text: str) -> None:
        if decision_text not in self.locked_decisions:
            self.locked_decisions.append(decision_text)
        self.updated_at = _now()

    # ── Analysis ────────────────────────────────────────────────────────────

    def detect_conflicts(self) -> None:
        """
        Cross-reference claims across specialists.
        Flags when two specialists make claims about the same keyword group
        with different numeric values — the most common conflict type.
        """
        self.conflicts = []
        self.consensus_facts = []

        all_claims: list[SpecialistClaim] = []
        for claims in self.claims.values():
            all_claims.extend(claims)

        specialists = list(self.claims.keys())
        if len(specialists) < 2:
            return

        number_pattern = re.compile(
            r"\$[\d,]+|\d[\d,]*(?:\.\d+)?%|\d+\s+(?:months?|years?|hours?|days?)"
        )

        keyword_groups: dict[str, list[tuple[str, str, list[str]]]] = {}

        for claim in all_claims:
            if claim.claim_type not in ("fact", "recommendation"):
                continue
            text_lower = claim.claim_text.lower()
            nums = number_pattern.findall(claim.claim_text)
            for kw in CONFLICT_KEYWORDS:
                if kw in text_lower:
                    if kw not in keyword_groups:
                        keyword_groups[kw] = []
                    keyword_groups[kw].append((claim.specialist_id, claim.claim_text, nums))

        seen_conflicts: set[frozenset] = set()
        for kw, entries in keyword_groups.items():
            if len(entries) < 2:
                continue
            for i, (spec_a, text_a, nums_a) in enumerate(entries):
                for spec_b, text_b, nums_b in entries[i + 1:]:
                    if spec_a == spec_b:
                        continue
                    if nums_a and nums_b and set(nums_a) != set(nums_b):
                        key = frozenset([spec_a + text_a[:40], spec_b + text_b[:40]])
                        if key not in seen_conflicts:
                            seen_conflicts.add(key)
                            self.conflicts.append({
                                "topic": kw,
                                "specialist_a": spec_a,
                                "claim_a": text_a[:200],
                                "specialist_b": spec_b,
                                "claim_b": text_b[:200],
                            })

        for kw, entries in keyword_groups.items():
            if len(entries) >= 2:
                all_nums = [frozenset(nums) for _, _, nums in entries if nums]
                if len(all_nums) >= 2 and len(set(all_nums)) == 1:
                    sample_text = entries[0][1][:150]
                    if sample_text not in self.consensus_facts:
                        self.consensus_facts.append(sample_text)

        self.updated_at = _now()

    # ── Rendering ───────────────────────────────────────────────────────────

    def render_for_specialists(self) -> str:
        """
        Compact summary injected into each specialist's context at spawn time.
        Shows what's already established so they don't re-research settled facts.
        """
        if not self.claims and not self.locked_decisions:
            return ""

        parts = []

        if self.locked_decisions:
            decisions = "\n".join(f"- {d}" for d in self.locked_decisions)
            parts.append(f"## Locked Decisions (do not re-open)\n\n{decisions}")

        if self.consensus_facts:
            facts = "\n".join(f"- {f}" for f in self.consensus_facts)
            parts.append(f"## Established Facts (confirmed by multiple specialists)\n\n{facts}")

        if self.conflicts:
            conflict_lines = [
                f"- **{c['topic'].upper()}**: {c['specialist_a']} said \"{c['claim_a'][:100]}\" "
                f"vs {c['specialist_b']} said \"{c['claim_b'][:100]}\""
                for c in self.conflicts
            ]
            parts.append(
                "## Known Conflicts (needs resolution — do not pick a side silently)\n\n"
                + "\n".join(conflict_lines)
            )

        prior_claims: list[str] = []
        for spec_id, claims in self.claims.items():
            for c in claims:
                if c.claim_type in ("fact", "decision"):
                    prior_claims.append(f"- [{spec_id}] {c.claim_text[:150]}")
        if prior_claims:
            parts.append(
                "## Prior Specialist Findings (this session)\n\n"
                + "\n".join(prior_claims[:20])
            )

        return "\n\n".join(parts)

    def render_for_synthesis(self) -> str:
        """
        Conflict and consensus block injected into synthesis prompt.
        Synthesis surfaces conflicts; it does not silently resolve them.
        """
        if not self.conflicts and not self.consensus_facts:
            return ""

        parts = []

        if self.consensus_facts:
            facts = "\n".join(f"- {f}" for f in self.consensus_facts)
            parts.append(f"## Confirmed Consensus\n\n{facts}")

        if self.conflicts:
            conflict_lines = [
                f"- **{c['topic'].upper()}** — "
                f"{c['specialist_a']}: \"{c['claim_a'][:120]}\" | "
                f"{c['specialist_b']}: \"{c['claim_b'][:120]}\""
                for c in self.conflicts
            ]
            parts.append(
                "## Detected Conflicts (surface these to the operator — do not silently blend)\n\n"
                + "\n".join(conflict_lines)
            )

        return "\n\n".join(parts)

    def total_claims(self) -> int:
        return sum(len(v) for v in self.claims.values())

    # ── Serialization ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "session_key": self.session_key,
            "channel_name": self.channel_name,
            "workspace_name": self.workspace_name,
            "claims": {
                spec_id: [c.to_dict() for c in claims]
                for spec_id, claims in self.claims.items()
            },
            "consensus_facts": self.consensus_facts,
            "conflicts": self.conflicts,
            "locked_decisions": self.locked_decisions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionState:
        obj = cls(
            session_key=data["session_key"],
            channel_name=data.get("channel_name", ""),
            workspace_name=data.get("workspace_name", ""),
        )
        obj.claims = {
            spec_id: [SpecialistClaim.from_dict(c) for c in claims]
            for spec_id, claims in data.get("claims", {}).items()
        }
        obj.consensus_facts = data.get("consensus_facts", [])
        obj.conflicts = data.get("conflicts", [])
        obj.locked_decisions = data.get("locked_decisions", [])
        obj.created_at = data.get("created_at", _now())
        obj.updated_at = data.get("updated_at", _now())
        return obj
