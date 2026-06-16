"""Capability map — learns which provider is good at what, over time.

A multi-armed bandit over (domain, provider) pairs. Two maps live here:
  - research map:       domain → provider → score   (who's good at finance/IT/...)
  - orchestration map:  provider → score             (who's good at being orchestrator)

Key behaviors:
  - EXPLOIT the proven-best, but EXPLORE ~12% of the time to catch providers
    that improved after an update.
  - SEED with priors (never cold-start from zero).
  - Domains grow dynamically as the system notices them.
  - Exploration randomness is INJECTABLE (rng) so tests are deterministic.

Storage: $NEXUS_DATA_DIR/capability_map.json  (default: data/capability_map.json)
Scoring updates are EWMA so recent performance counts more than ancient history.
"""

import json
import os
import random
from pathlib import Path
from typing import Dict, List, Optional

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
DEFAULT_PATH = _DATA_DIR / "capability_map.json"

# EWMA weight for a new observation. 0.2 => ~last 5 results dominate.
ALPHA = 0.2
# Fraction of routing decisions that explore instead of exploit.
EXPLORE_RATE = 0.12
# Neutral starting score for an unseen (domain, provider).
NEUTRAL = 0.5


class CapabilityMap:
    def __init__(self, path: Path = DEFAULT_PATH, rng: Optional[random.Random] = None):
        self.path = Path(path)
        self.rng = rng or random.Random()
        self.data: Dict[str, dict] = {
            "research": {},         # domain -> {provider: score}
            "orchestration": {},    # provider -> score
            "pair_affinity": {},    # "provA+provB" -> delta
            "profiles": {},         # provider -> capability profile
        }
        if self.path.exists():
            self.load()

    def load(self) -> None:
        with open(self.path) as f:
            loaded = json.load(f)
        for k in ("research", "orchestration", "pair_affinity", "profiles"):
            if k in loaded:
                self.data[k] = loaded[k]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)

    # ── Research map ──────────────────────────────────────────────────────────

    def score(self, domain: str, provider: str) -> float:
        return self.data["research"].get(domain, {}).get(provider, NEUTRAL)

    def update(self, domain: str, provider: str, result: float) -> float:
        """EWMA-update a (domain, provider) score with result in [0,1].
        Returns the new score. Auto-creates the domain row."""
        result = max(0.0, min(1.0, result))
        row = self.data["research"].setdefault(domain, {})
        prev = row.get(provider, NEUTRAL)
        row[provider] = round((1 - ALPHA) * prev + ALPHA * result, 4)
        return row[provider]

    def rank(self, domain: str) -> List[Dict[str, float]]:
        """Providers for a domain, best→worst."""
        row = self.data["research"].get(domain, {})
        out = [{"provider": p, "score": s} for p, s in row.items()]
        out.sort(key=lambda d: d["score"], reverse=True)
        return out

    def choose(self, domain: str, candidates: List[str]) -> Dict[str, object]:
        """Bandit pick for a single-provider route.

        Exploit the best-scoring candidate; EXPLORE_RATE of the time, pick a
        random other candidate to keep learning. Unseen providers default to
        NEUTRAL so they get a fair early shot.

        Returns {"provider", "mode": "exploit"|"explore", "score"}.
        """
        if not candidates:
            raise ValueError("no candidates to choose from")
        scored = sorted(candidates, key=lambda p: self.score(domain, p), reverse=True)
        best = scored[0]
        if len(scored) > 1 and self.rng.random() < EXPLORE_RATE:
            pick = self.rng.choice(scored[1:])
            return {"provider": pick, "mode": "explore", "score": self.score(domain, pick)}
        return {"provider": best, "mode": "exploit", "score": self.score(domain, best)}

    # ── Orchestration map ─────────────────────────────────────────────────────

    def orch_score(self, provider: str) -> float:
        return self.data["orchestration"].get(provider, NEUTRAL)

    def orch_update(self, provider: str, result: float) -> float:
        result = max(0.0, min(1.0, result))
        prev = self.data["orchestration"].get(provider, NEUTRAL)
        self.data["orchestration"][provider] = round((1 - ALPHA) * prev + ALPHA * result, 4)
        return self.data["orchestration"][provider]

    # ── Pair affinity ─────────────────────────────────────────────────────────

    @staticmethod
    def _pair_key(a: str, b: str) -> str:
        return "+".join(sorted([a, b]))

    def pair_affinity(self, a: str, b: str) -> float:
        return self.data["pair_affinity"].get(self._pair_key(a, b), 0.0)

    def update_pair(self, a: str, b: str, delta: float) -> float:
        key = self._pair_key(a, b)
        prev = self.data["pair_affinity"].get(key, 0.0)
        self.data["pair_affinity"][key] = round((1 - ALPHA) * prev + ALPHA * delta, 4)
        return self.data["pair_affinity"][key]

    # ── Capability profiles (the "company record") ────────────────────────────
    #
    # What each LLM can actually DO, learned over time:
    #   research_capable : can it reach live/external info?
    #   rag_dependent    : does it need retrieved context fed to answer well?
    #   hallucination_rate: EWMA share of graded answers that fabricated facts
    #   samples          : how many grades recorded (confidence indicator)
    #   last_score       : most recent task-quality grade

    _DEFAULT_PROFILE = {
        "research_capable": None,    # None = unknown until declared or graded
        "rag_dependent": None,
        "hallucination_rate": 0.0,
        "samples": 0,
        "last_score": None,
    }

    def get_profile(self, provider: str) -> Dict[str, object]:
        prof = dict(self._DEFAULT_PROFILE)
        prof.update(self.data["profiles"].get(provider, {}))
        return prof

    def declare(self, provider: str, **caps) -> Dict[str, object]:
        """Set STATIC capabilities (research_capable, rag_dependent, etc.).
        These are priors/known facts; the grading loop fills the learned fields."""
        prof = self.data["profiles"].setdefault(provider, {})
        prof.update({k: v for k, v in caps.items() if v is not None})
        return prof

    def record_grade(self, provider: str, domain: str, score: float,
                     hallucinated: bool = False) -> Dict[str, object]:
        """Record one graded answer: updates per-domain task strength AND
        the provider's hallucination EWMA + sample count."""
        score = max(0.0, min(1.0, float(score)))
        self.update(domain, provider, score)
        prof = self.data["profiles"].setdefault(provider, {})
        prev_h = prof.get("hallucination_rate", 0.0)
        prof["hallucination_rate"] = round(
            (1 - ALPHA) * prev_h + ALPHA * (1.0 if hallucinated else 0.0), 4
        )
        prof["samples"] = int(prof.get("samples", 0)) + 1
        prof["last_score"] = score
        return self.get_profile(provider)


def seed_priors(path: Path = DEFAULT_PATH, overwrite: bool = False) -> CapabilityMap:
    """Seed the map with reasonable priors so we never cold-start from zero.
    These are STARTING GUESSES; real graded data corrects them via update()."""
    if Path(path).exists() and not overwrite:
        return CapabilityMap(path)

    cm = CapabilityMap(path)
    priors = {
        "IT/code":   {"claude": 0.72, "gemini": 0.6, "groq": 0.55, "cerebras": 0.58},
        "research":  {"gemini": 0.68, "claude": 0.66, "cerebras": 0.6, "groq": 0.55},
        "finance":   {"claude": 0.64, "gemini": 0.62, "cerebras": 0.55, "groq": 0.52},
        "marketing": {"gemini": 0.64, "claude": 0.62, "groq": 0.5},
    }
    for domain, providers in priors.items():
        for prov, sc in providers.items():
            cm.data["research"].setdefault(domain, {})[prov] = sc

    cm.data["orchestration"] = {"claude": 0.7, "gemini": 0.55, "cerebras": 0.5, "groq": 0.48}
    cm.save()
    return cm
