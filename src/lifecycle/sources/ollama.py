"""
Ollama source adapter.

Polls the local Ollama REST API to get the installed digest for a model.
Ollama doesn't expose a "latest upstream digest" endpoint — instead we call
`ollama pull --dry-run` equivalent by checking the registry manifest digest
returned by the show endpoint, which reflects what the server last pulled.

Detection strategy:
  1. Record digest at install time (first run).
  2. On each monthly check, fetch the current digest via /api/show.
  3. If digest changed → Ollama updated the model in the background (or user
     pulled manually). Update our state and note it.
  4. If digest unchanged → run `ollama pull <model>` to check for upstream
     changes. Compare the post-pull digest. If different → update available.

Because Ollama's pull is the only reliable way to detect registry changes,
we report the pull_command and let the operator decide rather than pulling
automatically.
"""

import json
import logging
import urllib.request
from typing import Optional

from .base import ModelSource, VersionInfo

logger = logging.getLogger("nexus.lifecycle.ollama")


class OllamaSource(ModelSource):

    def fetch_version(self, model_id: str, quant: Optional[str] = None) -> Optional[VersionInfo]:
        try:
            payload = json.dumps({"name": model_id}).encode()
            req = urllib.request.Request(
                f"{self.endpoint}/api/show",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            digest = data.get("details", {}).get("digest", "") or data.get("digest", "")
            if not digest:
                # Fallback: scan tags list
                digest = self._digest_from_tags(model_id)

            if not digest:
                return None

            return VersionInfo(
                digest=digest,
                quant_confirmed=True,  # Ollama manages quants internally
                metadata={"family": data.get("details", {}).get("family", "")},
            )

        except Exception as e:
            logger.debug(f"OllamaSource.fetch_version({model_id}): {e}")
            return None

    def _digest_from_tags(self, model_id: str) -> str:
        try:
            req = urllib.request.Request(f"{self.endpoint}/api/tags")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            for m in data.get("models", []):
                name = m.get("name", "")
                if name == model_id or name.startswith(model_id + ":"):
                    return m.get("digest", "")
        except Exception:
            pass
        return ""

    def pull_command(self, model_id: str, quant: Optional[str] = None) -> str:
        return f"ollama pull {model_id}"
