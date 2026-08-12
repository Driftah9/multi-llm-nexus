"""
Hardware Fit Checker — periodic discovery of *new* models llmfit didn't know
about at install time, cross-checked against the current local model.

Distinct from ModelLifecycleManager (manager.py): that class tracks version
drift on models you already run. This checks whether a *different* model —
one that didn't exist, or wasn't in llmfit's catalog, when Nexus was set up —
now fits the same hardware better.

Flow (mirrors ModelLifecycleManager.run()):
  1. Load config/model_sources.yaml -> fit_check: section
  2. Load data/llmfit_fit_state.json (previous run state)
  3. Skip if checked within interval_days (unless --force)
  4. `llmfit update` — refresh llmfit's own catalog from HuggingFace
  5. `llmfit --json recommend` — top fits for this hardware
  6. `llmfit --json info "<current_model>"` — baseline score for what's running
  7. A candidate only qualifies if it beats the baseline on both quality and
     speed by the configured margins — "confidently better", not just different
  8. If a qualifying candidate exists and hasn't already been notified: DM the
     operator via Notifier. Never pulls or reconfigures anything — the operator
     authorizes the swap themselves.

Zero LLM tokens spent unless a qualifying candidate is found — everything above
is mechanical subprocess + JSON comparison, consistent with the project's
"watchers are mechanical, the LLM wakes only on signal" design (see
src/core/watchers.py).
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

        candidate = None
        if baseline is None:
            errors.append(f"could not score current model '{current_model}' via llmfit info — skipping comparison")
        elif not recs:
            errors.append("llmfit returned no recommendations")
        else:
            candidate = self._pick_qualifying_candidate(baseline, recs, cfg, current_model)

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

    # ── Comparison ──────────────────────────────────────────────────────────

    def _pick_qualifying_candidate(
        self, baseline: dict, recs: list[dict], cfg: dict, current_model: str
    ) -> Optional[dict]:
        base_quality = self._num(baseline.get("score_components", {}).get("quality"))
        base_tps = self._num(baseline.get("estimated_tps"))
        if base_quality is None:
            return None

        min_quality_gain_pct = cfg.get("min_quality_gain_pct", 0.10)
        max_speed_regression_pct = cfg.get("max_speed_regression_pct", 0.0)

        for r in recs:
            name = r.get("name")
            if not name or name == current_model:
                continue
            comp = r.get("score_components", {}) or {}
            quality = self._num(comp.get("quality"))
            tps = self._num(r.get("estimated_tps"))
            if quality is None:
                continue

            quality_gain_ok = quality >= base_quality * (1 + min_quality_gain_pct)
            speed_ok = True
            if base_tps is not None and tps is not None and base_tps > 0:
                speed_ok = tps >= base_tps * (1 - max_speed_regression_pct)

            if quality_gain_ok and speed_ok:
                return {
                    "name": name,
                    # llmfit's own HF->Ollama mapping — closes the phase-2 name-mapping
                    # gap noted in wizard.py::llmfit_probe(); confirmed present on a
                    # real run (2026-08-12), not documented in llmfit's own docs.
                    "ollama_name": r.get("ollama_name"),
                    "score": r.get("score"),
                    "quality": quality,
                    "estimated_tps": tps,
                    "best_quant": r.get("best_quant"),
                    "baseline_quality": base_quality,
                    "baseline_tps": base_tps,
                }
        return None

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

            tps = candidate.get("estimated_tps")
            base_tps = candidate.get("baseline_tps")
            speed_line = f"{tps:.1f} tok/s est." + (f" (vs {base_tps:.1f} now)" if base_tps else "") if tps else ""

            ollama_name = candidate.get("ollama_name")
            lines = [
                "**A better-fitting local model showed up** (llmfit fit check)\n",
                f"• Currently running: `{current_model}`",
                f"• Candidate: **{candidate['name']}**" + (f" [{candidate['best_quant']}]" if candidate.get("best_quant") else ""),
                f"  quality {candidate['quality']:.0f} (vs {candidate['baseline_quality']:.0f} now)"
                + (f", {speed_line}" if speed_line else ""),
                "",
                "This is a suggestion only — nothing was changed. Test it yourself with:",
                f"  `llmfit info \"{candidate['name']}\"`",
            ]
            if ollama_name:
                lines.append(f"  `ollama pull {ollama_name}` (llmfit's own HF→Ollama mapping)")
            lines.append("")

            notify_cfg = cfg.get("notify", {})
            dest = notify_cfg.get("destination", "dm")
            channel = notify_cfg.get("channel") if dest == "channel" else None
            notifier.send("\n".join(lines), destination=dest, channel=channel)
        except Exception as e:
            logger.error(f"Fit-check notify failed: {e}")
