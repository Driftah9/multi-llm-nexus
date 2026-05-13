"""
Google Gemini provider — AI Studio and Vertex AI.

Two config paths:
  AI Studio  — GOOGLE_API_KEY, free tier available at aistudio.google.com
  Vertex AI  — GOOGLE_CLOUD_PROJECT + region, uses Application Default Credentials

Install: pip install google-genai
"""
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


_ROLE_MAP = {"user": "user", "assistant": "model", "system": "user"}


class GeminiProvider(BaseProvider):
    """
    Google Gemini provider. Supports AI Studio (API key) and Vertex AI (GCP).

    Config keys:
      model          — e.g. "gemini-2.0-flash", "gemini-1.5-pro"
      api_key        — Google AI Studio key (leave unset for Vertex AI)
      project        — GCP project ID (Vertex AI only)
      region         — GCP region, default "us-central1" (Vertex AI only)
      max_tokens     — default 8192
      temperature    — default 0.7
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not GEMINI_AVAILABLE:
            raise ImportError(
                "google-genai package required: pip install google-genai"
            )

        self.max_tokens = config.get("max_tokens", 8192)
        self.temperature = config.get("temperature", 0.7)

        api_key: Optional[str] = config.get("api_key") or None
        self._project: Optional[str] = config.get("project") or None
        self._region: str = config.get("region", "us-central1")
        self._use_vertex = bool(self._project and not api_key)

        if self._use_vertex:
            self._client = genai.Client(
                vertexai=True,
                project=self._project,
                location=self._region,
            )
        else:
            self._client = genai.Client(api_key=api_key)

        self._safety = [
            genai_types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="BLOCK_NONE",
            ),
            genai_types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_NONE",
            ),
        ]

    def _convert_messages(self, messages: list[Message]) -> list[genai_types.Content]:
        result = []
        for msg in messages:
            if msg.role == "system":
                continue
            role = _ROLE_MAP.get(msg.role, "user")
            result.append(
                genai_types.Content(role=role, parts=[genai_types.Part(text=msg.content)])
            )
        return result

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        contents = self._convert_messages(messages)

        config = genai_types.GenerateContentConfig(
            max_output_tokens=self.max_tokens,
            temperature=self.temperature,
            safety_settings=self._safety,
        )
        if system:
            config.system_instruction = system

        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        content = response.text or ""
        tool_calls = []

        for candidate in (response.candidates or []):
            for part in (candidate.content.parts or []):
                if hasattr(part, "function_call") and part.function_call and part.function_call.name:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                        call_id=None,
                    ))

        usage = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = {
                "input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                "output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
            }

        return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents="ping",
            )
            return bool(response.text)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            models = []
            async for m in self._client.aio.models.list():
                if "generateContent" in (m.supported_actions or []):
                    models.append(m.name.replace("models/", ""))
            return models
        except Exception:
            return []
