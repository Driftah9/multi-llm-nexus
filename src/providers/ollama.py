"""
Ollama provider — local models, nothing leaves your network.
The zero-cost, zero-API-key option.
Ideal for: triage, privacy-sensitive tasks, fast classification.
Install Ollama: https://ollama.ai — then `ollama pull llama3.2:3b`
"""
import json
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class OllamaProvider(BaseProvider):
    """
    Ollama local model provider.
    Requires Ollama running at endpoint (default: http://localhost:11434).
    Install: pip install httpx
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx required: pip install httpx")
        self.endpoint = config.get("endpoint", "http://localhost:11434")
        self.timeout = config.get("timeout", 120)
        self.options = config.get("options", {})

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        payload = {
            "model": self.model,
            "messages": self._convert_messages(messages, system),
            "stream": False,
            "options": self.options,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.endpoint}/api/chat",
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("message", {}).get("content", "")
                return ProviderResponse(content=content, raw=data)
        except httpx.TimeoutException:
            return ProviderResponse(content="[timeout: ollama did not respond]")
        except Exception as e:
            return ProviderResponse(content=f"[error: {e}]")

    def _convert_messages(self, messages: list[Message], system: str) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def supports_tools(self) -> bool:
        # Ollama supports tools on some models — treat as best-effort
        return False

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.endpoint}/api/tags")
                models = [m["name"] for m in response.json().get("models", [])]
                return self.model in models or any(self.model in m for m in models)
        except Exception:
            return False
