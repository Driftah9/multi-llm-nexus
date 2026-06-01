"""
vLLM provider — high-throughput inference server, hardware-agnostic.

Supports NVIDIA (CUDA), AMD (ROCm), and Intel (XPU/SYCL) GPUs from a
single runtime. Speaks OpenAI-compatible API at /v1/chat/completions.

Unlike ik_llama.cpp (CUDA-only, MoE-optimized), vLLM is the universal
local inference backend. When the operator's hardware isn't NVIDIA,
vLLM is the recommended provider for GPU-accelerated inference.

Install & run (NVIDIA):
  pip install vllm
  vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000

Install & run (Intel XPU):
  pip install vllm  # with oneAPI/SYCL backend
  vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000 --device xpu

Install & run (AMD ROCm):
  pip install vllm  # with ROCm backend
  vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000

Multi-GPU tensor parallelism:
  vllm serve <model> --tensor-parallel-size 4

Docs: https://docs.vllm.ai/
"""
import json as _json
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class VllmProvider(BaseProvider):
    """
    vLLM inference server provider (OpenAI-compatible endpoint).
    Supports NVIDIA CUDA, AMD ROCm, and Intel XPU backends.
    Requires vllm serve running at endpoint (default: http://localhost:8000).
    Install: pip install httpx
    """

    DEFAULT_ENDPOINT = "http://localhost:8000"

    def __init__(self, config: dict):
        super().__init__(config)
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx required: pip install httpx")
        self.endpoint = config.get("endpoint", self.DEFAULT_ENDPOINT).rstrip("/")
        self.timeout = config.get("timeout", 300)
        self.extra = config.get("options", {})

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        payload = {
            "model": self.model,
            "messages": self._build_messages(messages, system),
            **self.extra,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.endpoint}/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "")

        tool_calls = []
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            try:
                args = _json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = {}
            tool_calls.append(ToolCall(
                name=fn.get("name", ""),
                arguments=args,
                call_id=tc.get("id"),
            ))

        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
            raw=data,
        )

    def _build_messages(self, messages: list[Message], system: str) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.endpoint}/health")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Query vLLM for loaded models."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.endpoint}/v1/models")
                r.raise_for_status()
                data = r.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            return []
