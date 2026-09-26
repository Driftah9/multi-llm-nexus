"""
Hardware Fit Checker — periodic discovery of *new* models, proposed as candidates.

Distinct from ModelLifecycleManager (manager.py): that class tracks version
drift on models you already run. This checks whether a *different* model —
one that didn't exist, or wasn't in llmfit's catalog, when Nexus was set up —
is now available and worth testing.

⚠️  ADVISORY TOOL: llmfit estimates are not trustworthy verdicts.
Measured on RX 480 (2026-08-14): llmfit predicted 70% utilization; real was 89%
with 3.5× throughput collapse. It scores generation only (not prompt-eval —
the binding constraint for long-context work), omits compute buffers from
memory estimates, and recommends runtimes the hardware can't execute.

USE: Generate candidates for operator measurement. DO NOT: Gate on fit verdicts.

Flow (mirrors ModelLifecycleManager.run()):
  1. Load config/model_sources.yaml -> fit_check: section
  2. Load data/llmfit_fit_state.json (previous run state)
  3. Skip if checked within interval_days (unless --force)
  4. `llmfit update` — refresh llmfit's own catalog from HuggingFace
  5. `llmfit --json recommend` — top fits for this hardware (up to limit)
  6. Propose the top-N as candidates (no quality/speed gate)
  7. If new candidates exist and haven't already been notified: DM the
     operator via Notifier, including llmfit's own HF→Ollama mapping.
     Never pulls or reconfigures anything — the operator tests and decides.

Zero LLM tokens spent — everything above is mechanical subprocess + JSON
comparison, consistent with the project's "watchers are mechanical, the LLM
wakes only on signal" design (see src/core/watchers.py).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.lifecycle.fit_check")

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "model_sources.yaml"
STATE_PATH = Path(__file__).parent.parent.parent / "data" / "llmfit_fit_state.json"

_COMMON_LLMFIT_PATHS = (
    Path.home() / ".local/bin/llmfit",
    Path.home() / ".cargo/bin/llmfit",
    Path("/usr/local/bin/llmfit"),
)


class HardwareFitChecker:

    def __init__(self, config_path: Path = CONFIG_PATH, state_path: Path = STATE_PATH):
        self.config_path = config_path
        self.state_path = state_path
        self._config: dict = {}
        self._state: dict = {}

    def run(self, force: bool = False, dry_run: bool = False) -> dict:
        """
        Execute the fit check.

        Returns a summary dict with keys:
          skipped: bool
          candidate: dict | None   (present only if a qualifying model was found)
          errors: list of str
        """
        self._load_config()
        self._load_state()

        cfg = self._config.get("fit_check", {})
        if not cfg.get("enabled", True):
            return {"skipped": True, "candidate": None, "errors": ["fit_check disabled in config"]}

        if not force and self._checked_recently(cfg):
            logger.info("Fit check ran within the configured interval — skipping (use --force to override)")
            return {"skipped": True, "candidate": None, "errors": []}

        errors: list[str] = []
        exe = self._find_llmfit()
        if not exe:
            return {"skipped": False, "candidate": None, "errors": ["llmfit binary not found — install it first"]}

        try:
            subprocess.run(
                [exe, "update", "--trending", "100", "--downloads", "50"],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as e:
            errors.append(f"llmfit update failed (non-fatal, catalog may be stale): {e}")

        current_model = cfg.get("current_model") or self._heuristic_baseline_model()
        if not current_model:
            errors.append("no current_model configured and heuristic baseline unavailable — cannot compare")
            self._state["last_check"] = datetime.now(timezone.utc).isoformat()
            if not dry_run:
                self._save_state()
            return {"skipped": False, "candidate": None, "errors": errors}

        baseline = self._llmfit_info(exe, current_model)
        recs = self._llmfit_recommend(exe, limit=cfg.get("recommend_limit", 5))

        candidates = []
        if baseline is None:
            errors.append(f"could not score current model '{current_model}' via llmfit info — skipping comparison")
        elif not recs:
            errors.append("llmfit returned no recommendations")
        else:
            # Return all recommendations (no quality/speed gate — those estimates are unreliable).
            # Operator measures and decides which to test.
            candidates = self._get_candidate_list(recs, current_model)

        candidate = candidates[0] if candidates else None

        self._state["last_check"] = datetime.now(timezone.utc).isoformat()

        if candidate and candidate["name"] == self._state.get("last_notified"):
            # Already told the operator about this exact candidate — don't repeat.
            candidate = None

        if not dry_run:
            if candidate:
                self._state["last_notified"] = candidate["name"]
                self._notify(candidate, current_model, cfg)
            self._save_state()

        return {"skipped": False, "candidate": candidate, "errors": errors}

    # ── llmfit shellouts ────────────────────────────────────────────────────

    def _find_llmfit(self) -> Optional[str]:
        exe = shutil.which("llmfit")
        if exe:
            return exe
        for p in _COMMON_LLMFIT_PATHS:
            if p.exists():
                return str(p)
        return None

    def _llmfit_info(self, exe: str, model: str) -> Optional[dict]:
        """Verified against a real run (2026-08-12): `llmfit --json info` wraps its
        result in the same {"models": [...]} envelope as `recommend` — it is NOT a
        flat ModelFit object. Returns the single model dict, unwrapped."""
        try:
            r = subprocess.run([exe, "--json", "info", model], capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or not r.stdout.strip():
                return None
            data = json.loads(r.stdout)
            if not isinstance(data, dict):
                return None
            models = data.get("models") or data.get("recommendations") or []
            return models[0] if models and isinstance(models[0], dict) else None
        except Exception as e:
            logger.debug(f"llmfit info({model}) failed: {e}")
            return None

    def _llmfit_recommend(self, exe: str, limit: int) -> list[dict]:
        try:
            r = subprocess.run(
                [exe, "--json", "recommend", "--limit", str(limit)],
                capture_output=True, text=True, timeout=90,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return []
            data = json.loads(r.stdout)
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict)]
            if isinstance(data, dict):
                recs = data.get("models") or data.get("recommendations") or []
                return [m for m in recs if isinstance(m, dict)]
            return []
        except Exception as e:
            logger.debug(f"llmfit recommend failed: {e}")
            return []

    def _heuristic_baseline_model(self) -> Optional[str]:
        """Fall back to Nexus's own VRAM-tiered pick when no current_model is configured."""
        try:
            import asyncio
            from src.setup.hardware_detect import detect_hardware
            hw = asyncio.run(detect_hardware())
            return hw.recommended_model
        except Exception as e:
            logger.debug(f"heuristic baseline unavailable: {e}")
            return None

    # ── Candidate proposal (advisory, no gating on estimates) ──────────────────

    def _get_candidate_list(self, recs: list[dict], current_model: str) -> list[dict]:
        """Return all recommendations as candidates (no quality/speed gate).

        llmfit estimates are not trustworthy verdicts — they omit prompt-eval,
        compute buffers, and runtime constraints. Propose all top-N and let the
        operator measure and decide which to test.

        See the module docstring for why: measured 70% predicted → 89% real
        utilization with 3.5× throughput collapse on RX 480 (2026-08-14).
        """
        candidates = []
        for r in recs:
            name = r.get("name")
            if not name or name == current_model:
                continue
            candidates.append({
                "name": name,
                # llmfit's own HF->Ollama mapping — closes the phase-2 name-mapping
                # gap. Confirmed present on a real run (2026-08-12), not documented
                # in llmfit's own docs.
                "ollama_name": r.get("ollama_name"),
                "score": r.get("score"),
                "score_components": r.get("score_components", {}),
                "estimated_tps": r.get("estimated_tps"),
                "best_quant": r.get("best_quant"),
                "note": "⚠️  llmfit estimate — measure before trusting",
            })
        return candidates

    @staticmethod
    def _num(v) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # ── Config / state I/O (mirrors ModelLifecycleManager) ────────────────

    def _load_config(self) -> None:
        if not self.config_path.exists():
            self._config = {}
            return
        import yaml
        self._config = yaml.safe_load(self.config_path.read_text()) or {}

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {}
        else:
            self._state = {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2))

    def _checked_recently(self, cfg: dict) -> bool:
        last = self._state.get("last_check", "")
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
            interval = timedelta(days=cfg.get("interval_days", 7))
            return datetime.now(timezone.utc) - last_dt < interval
        except ValueError:
            return False

    def _notify(self, candidate: dict, current_model: str, cfg: dict) -> None:
        try:
            from src.core.notify import Notifier
            notifier = Notifier.from_config()

            score_components = candidate.get("score_components", {})
            tps = candidate.get("estimated_tps")
            score_line = ""
            if score_components:
                parts = [f"{k}={v:.0f}" for k, v in score_components.items() if v]
                score_line = " (" + ", ".join(parts) + ")" if parts else ""

            ollama_name = candidate.get("ollama_name")
            lines = [
                "**New model candidate available** (llmfit discovery)\n",
                f"• Currently running: `{current_model}`",
                f"• Candidate: **{candidate['name']}**" + (f" [{candidate['best_quant']}]" if candidate.get("best_quant") else ""),
                f"  llmfit score {candidate['score']:.1f}{score_line}" if candidate.get("score") else "  (llmfit score unknown)",
                "",
                "⚠️  **llmfit estimates are advisory only — always measure before deciding:**",
                "  Measured bias: predicts 70% utilization where real is 89% (3.5× throughput collapse possible).",
                "  Reasons: scores generation not prompt-eval, omits compute buffers, recommends unsupported runtimes.",
                "",
                "To test this candidate:",
                f"  `ollama pull {ollama_name}`" if ollama_name else "",
                "  Then run your typical workload and measure prompt-eval latency, throughput, and memory.",
                "",
                f"  `llmfit info \"{candidate['name']}\"` (for reference)",
            ]
            lines = [l for l in lines if l is not None and l != ""]  # Remove empty strings

            notify_cfg = cfg.get("notify", {})
            dest = notify_cfg.get("destination", "dm")
            channel = notify_cfg.get("channel") if dest == "channel" else None
            notifier.send("\n".join(lines), destination=dest, channel=channel)
        except Exception as e:
            logger.error(f"Fit-check notify failed: {e}")
