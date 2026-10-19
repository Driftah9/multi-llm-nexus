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

# ── Capability graduation ladder ──────────────────────────────────────────────
# A model qualifies for a DEMANDING role (planner/reviewer/builder) in a domain
# only when it has PROVEN it: enough graded samples AND a score at/above a bar
# representing trusted-baseline competence. Unproven models (no samples) are NOT
# qualified — cold-start-low: a demanding role is earned, not granted. Near-qualified
# models (probe band) get occasional graded probes to earn their way over the bar.
# NEXUS:PORTABLE — the ladder is generic; each install tunes the bar via env.
import os as _os
QUALIFY_BAR = float(_os.environ.get("CAP_QUALIFY_BAR", "0.6"))
QUALIFY_MIN_SAMPLES = int(_os.environ.get("CAP_QUALIFY_MIN_SAMPLES", "5"))
DEMANDING_DOMAINS = ("coding", "reasoning", "analysis")
PROBE_BAND = float(_os.environ.get("CAP_PROBE_BAND", "0.15"))
PROBE_RATE = float(_os.environ.get("CAP_PROBE_RATE", "0.15"))
# Flap guard (hysteresis) on the EARNED bar crossing. ALPHA=0.2 lets a short noisy run drag
# a near-bar model in/out of qualification — two good grades can lift a 0.56 over 0.6 and one
# bad grade drops it back. So the earned side of is_qualified reads a LATCH, not the live
# score: the latch flips only after QUALIFY_CONFIRM consecutive updates land on the other side
# of the bar. Exception: a score that falls below (bar - PROBE_BAND) demotes at once — that is
# a collapse, not noise. score() is NOT affected. Separate from QUALIFY_MIN_SAMPLES (a volume
# floor, not a flap guard). <=1 disables. (Ported from live claude-brain, 2026-10-19.)
QUALIFY_CONFIRM = int(_os.environ.get("CAP_QUALIFY_CONFIRM", "3"))
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
            "grades": {},           # domain -> {provider: count} per-(domain,provider) observation count;
                                    # confidence/solidity signal for the graduation-ladder qualification gate
            "qual_latch": {},       # domain -> {provider: {"above", "streak", "bar"}} flap-guarded bar
                                    # crossing (see QUALIFY_CONFIRM); absent row => live score vs bar
        }
        if self.path.exists():
            self.load()

    def load(self) -> None:
        with open(self.path) as f:
            loaded = json.load(f)
        for k in ("research", "orchestration", "pair_affinity", "profiles", "grades", "qual_latch"):
            if k in loaded:
                self.data[k] = loaded[k]
        # Load research findings from model_intel.json if available
        self.load_research_findings()

    def load_research_findings(self) -> None:
        """Load benchmarking results from model_intel.json into pre-seed scores.

        model_intel.json is written by capability_research_job.py and contains
        benchmark results for newly discovered models. Scores are loaded as
        pre-seed values (they can be overwritten by graded evidence).
        """
        intel_path = self.path.parent / "model_intel.json"
        if not intel_path.exists():
            return

        try:
            with open(intel_path) as f:
                intel = json.load(f)
            models = intel.get("models", {})

            for model_key, model_data in models.items():
                # model_key format: "provider_id::model_id"
                if "::" not in model_key:
                    continue
                provider_id = model_key.split("::")[0]
                domains = model_data.get("domains", {})

                for domain, domain_result in domains.items():
                    if isinstance(domain_result, dict) and "score" in domain_result:
                        score = domain_result["score"]
                        if 0.0 <= score <= 1.0:
                            # Seed this domain/provider score
                            self.data["research"].setdefault(domain, {})[provider_id] = score
        except Exception as e:
            import sys
            print(f"Warning: could not load research findings: {e}", file=sys.stderr)

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
        # Count this observation per (domain, provider) — the single honest count
        # point every real grade/ranking flows through; read by the graduation gate.
        grow = self.data.setdefault("grades", {}).setdefault(domain, {})
        grow[provider] = int(grow.get(provider, 0)) + 1
        self._advance_latch(domain, provider, prev, row[provider])
        return row[provider]

    def _advance_latch(self, domain: str, provider: str, prev: float, new: float) -> None:
        """Step the flap-guarded bar crossing for one observation (see QUALIFY_CONFIRM).
        A missing or stale latch (bar changed via env) starts from where the PREVIOUS score
        sat, so legacy rows keep their standing and the new grade counts toward the streak."""
        bar = QUALIFY_BAR
        lrow = self.data.setdefault("qual_latch", {}).setdefault(domain, {})
        latch = lrow.get(provider)
        if not isinstance(latch, dict) or latch.get("bar") != bar:
            latch = {"above": prev >= bar, "streak": 0, "bar": bar}
        raw = new >= bar
        if raw == latch["above"]:
            latch["streak"] = 0
        elif not raw and new < bar - PROBE_BAND:
            latch["above"], latch["streak"] = False, 0      # collapse, not noise
        else:
            latch["streak"] = int(latch.get("streak", 0)) + 1
            if latch["streak"] >= max(1, QUALIFY_CONFIRM):
                latch["above"], latch["streak"] = raw, 0
        lrow[provider] = latch

    def _latched_above(self, domain: str, provider: str) -> bool:
        """Earned-side bar test through the flap guard. No latch yet (or one recorded against
        a different bar) => live score vs bar, i.e. the pre-latch behaviour."""
        latch = self.data.get("qual_latch", {}).get(domain, {}).get(provider)
        if isinstance(latch, dict) and latch.get("bar") == QUALIFY_BAR:
            return bool(latch.get("above"))
        return self.score(domain, provider) >= QUALIFY_BAR

    def qualification_state(self, domain: str, provider: str) -> Dict[str, object]:
        """Flap-guard view for dashboards: latched side, and how many consecutive
        observations are pushing the other way (streak / confirm)."""
        latch = self.data.get("qual_latch", {}).get(domain, {}).get(provider)
        live = isinstance(latch, dict) and latch.get("bar") == QUALIFY_BAR
        return {
            "above": self._latched_above(domain, provider),
            "streak": int(latch.get("streak", 0)) if live else 0,
            "confirm": max(1, QUALIFY_CONFIRM),
        }

    def rank(self, domain: str) -> List[Dict[str, float]]:
        """Providers for a domain, best→worst."""
        row = self.data["research"].get(domain, {})
        out = [{"provider": p, "score": s} for p, s in row.items()]
        out.sort(key=lambda d: d["score"], reverse=True)
        return out

    # ── Graduation-ladder qualification (demanding-role gating) ───────────────
    def grade_count(self, domain: str, provider: str) -> int:
        """How many graded observations a (domain, provider) has — confidence signal."""
        return int(self.data.get("grades", {}).get(domain, {}).get(provider, 0))

    def is_qualified(self, domain: str, provider: str,
                     bar: float = QUALIFY_BAR, min_samples: int = QUALIFY_MIN_SAMPLES) -> bool:
        """EARNED (>= min_samples graded observations AND over the bar, through the flap
        guard) OR SEEDED (the operator's capability baseline vouches for it at/above the bar).
        Unknown/unseeded with no grades => False (cold-start-low: earned, not granted). The
        seed is a t=0 prior that real grades override — not a permanent grant. No baseline
        file => earned-only (backward-compatible)."""
        if self.earned_qualified(domain, provider, bar, min_samples):
            return True
        try:
            from . import capability_baseline
            return capability_baseline.is_seeded_qualified(domain, provider, bar)
        except Exception:
            return False

    def earned_qualified(self, domain: str, provider: str,
                         bar: float = QUALIFY_BAR, min_samples: int = QUALIFY_MIN_SAMPLES) -> bool:
        """EARNED half of is_qualified: volume floor AND (flap-guarded) bar. A caller-supplied
        custom bar is compared to the live score."""
        if self.grade_count(domain, provider) < min_samples:
            return False
        if bar == QUALIFY_BAR:
            return self._latched_above(domain, provider)
        return self.score(domain, provider) >= bar

    def _effective_score(self, domain: str, provider: str) -> float:
        """Ranking score: the earned EWMA once graded, else the operator's baseline prior, else
        score() (a research prior, or NEUTRAL). Lets seeded providers rank by their prior until
        real evidence arrives — without ever writing the prior into the earned table."""
        if self.grade_count(domain, provider) > 0:
            return self.score(domain, provider)
        try:
            from . import capability_baseline
            b = capability_baseline.baseline_score(domain, provider)
            if b is not None:
                return b
        except Exception:
            pass
        return self.score(domain, provider)

    def qualified(self, domain: str, candidates: List[str],
                  bar: float = QUALIFY_BAR, min_samples: int = QUALIFY_MIN_SAMPLES) -> List[str]:
        """Candidates trusted for a demanding role in this domain (earned OR seeded),
        best-first. Ranked by effective score (earned once graded, else the seeded prior)."""
        ok = [c for c in candidates if self.is_qualified(domain, c, bar, min_samples)]
        ok.sort(key=lambda p: self._effective_score(domain, p), reverse=True)
        return ok

    def probe_candidates(self, domain: str, candidates: List[str],
                         bar: float = QUALIFY_BAR, band: float = PROBE_BAND,
                         min_samples: int = QUALIFY_MIN_SAMPLES) -> List[str]:
        """Near-qualified models (score >= bar-band, >= min_samples, not earned-qualified) —
        the CANDIDATE zone, handed occasional graded probes on demanding steps to earn up.
        Includes a model already over the bar whose promotion the flap guard hasn't confirmed
        yet, since probing is how it collects the streak."""
        out = []
        for c in candidates:
            if self.grade_count(domain, c) < min_samples:
                continue
            if self.earned_qualified(domain, c, bar, min_samples):
                continue
            if self.score(domain, c) >= bar - band:
                out.append(c)
        out.sort(key=lambda p: self.score(domain, p), reverse=True)
        return out

    def choose(self, domain: str, candidates: List[str]) -> Dict[str, object]:
        """Bandit pick for a single-provider route.

        Exploit the best-scoring candidate; EXPLORE_RATE of the time, pick a
        random other candidate to keep learning. Candidates rank by effective score:
        earned EWMA once graded, else the baseline prior (so a fresh install routes
        sensibly on day one), else NEUTRAL so unknowns still get a fair early shot.

        Returns {"provider", "mode": "exploit"|"explore", "score"}.
        """
        if not candidates:
            raise ValueError("no candidates to choose from")
        eff = {p: self._effective_score(domain, p) for p in candidates}
        scored = sorted(candidates, key=lambda p: eff[p], reverse=True)
        best = scored[0]
        if len(scored) > 1 and self.rng.random() < EXPLORE_RATE:
            pick = self.rng.choice(scored[1:])
            return {"provider": pick, "mode": "explore", "score": eff[pick]}
        return {"provider": best, "mode": "exploit", "score": eff[best]}

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
