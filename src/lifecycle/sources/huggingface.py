"""
HuggingFace Hub source adapter.

Uses the public HF Hub REST API (no auth required for public models).
Gated models require HF_TOKEN in config.

Version fingerprint: the model's git commit SHA from the API response.
Quant confirmation: checks the siblings file list for the requested .gguf filename pattern.
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Optional

from .base import ModelSource, VersionInfo

logger = logging.getLogger("nexus.lifecycle.huggingface")

HF_API = "https://huggingface.co/api"


class HuggingFaceSource(ModelSource):

    def __init__(self, config: dict):
        super().__init__(config)
        self._token = config.get("hf_token", "")

    def fetch_version(self, model_id: str, quant: Optional[str] = None) -> Optional[VersionInfo]:
        try:
            url = f"{HF_API}/models/{urllib.parse.quote(model_id, safe='/')}"
            headers = {"User-Agent": "nexus-lifecycle/1.0"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            sha = data.get("sha", "")
            last_modified = data.get("lastModified", "")
            siblings = [s.get("rfilename", "") for s in data.get("siblings", [])]

            quant_confirmed = True
            if quant:
                quant_confirmed = self._quant_exists(siblings, quant)
                if not quant_confirmed:
                    logger.info(
                        f"HF: {model_id} updated but quant {quant} not found in new version siblings"
                    )

            return VersionInfo(
                digest=sha,
                quant_confirmed=quant_confirmed,
                last_modified=last_modified,
                url=f"https://huggingface.co/{model_id}",
                metadata={"siblings_count": len(siblings)},
            )

        except Exception as e:
            logger.debug(f"HuggingFaceSource.fetch_version({model_id}): {e}")
            return None

    def _quant_exists(self, siblings: list[str], quant: str) -> bool:
        quant_lower = quant.lower()
        return any(quant_lower in f.lower() and f.endswith(".gguf") for f in siblings)

    def pull_command(self, model_id: str, quant: Optional[str] = None) -> str:
        if quant:
            return (
                f"huggingface-cli download {model_id} "
                f"--include '*{quant}*.gguf' --local-dir ./models/{model_id.split('/')[-1]}"
            )
        return f"huggingface-cli download {model_id} --local-dir ./models/{model_id.split('/')[-1]}"
