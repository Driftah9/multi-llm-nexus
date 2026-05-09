"""
Google Gemini provider — AI Studio and Vertex AI.

Two config paths:
  AI Studio  — GOOGLE_API_KEY, free tier available at aistudio.google.com
  Vertex AI  — GOOGLE_CLOUD_PROJECT + region, uses Application Default Credentials

Install: pip install google-generativeai
"""
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


# Gemini role mapping — Gemini uses "user"/"model" not "user"/"assistant"
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
                "google-generativeai package required: pip install google-generativeai"
            )

        self.max_tokens = config.get("max_tokens", 8192)
        self.temperature = config.get("temperature", 0.7)

        api_key: Optional[str] = config.get("api_key") or None
        self._project: Optional[str] = config.get("project") or None
        self._region: str = config.get("region", "us-central1")
        self._use_vertex = bool(self._project and not api_key)

        if self._use_vertex:
            # Vertex AI — uses Application Default Credentials
            import vertexai
            vertexai.init(project=self._project, location=self._region)
            from vertexai.generative_models import GenerativeModel
            self._model_cls = GenerativeModel
        else:
            if api_key:
                genai.configure(api_key=api_key)
            self._model_cls = genai.GenerativeModel

        self._safety = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

    def _build_model(self, system: str):
        kwargs = {}
        if system:
            kwargs["system_instruction"] = system
        return self._model_cls(self.model, **kwargs)

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role == "system":
                continue  # system handled via system_instruction
            role = _ROLE_MAP.get(msg.role, "user")
            result.append({"role": role, "parts": [msg.content]})
        return result

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        try:
            model = self._build_model(system)
            history = self._convert_messages(messages[:-1]) if len(messages) > 1 else []
            last = messages[-1].content if messages else ""

            generation_config = {
                "max_output_tokens": self.max_tokens,
                "temperature": self.temperature,
            }

            chat = model.start_chat(history=history)
            response = await chat.send_message_async(
                last,
                generation_config=generation_config,
                safety_settings=self._safety,
            )

            content = response.text or ""
            tool_calls = []

            # Extract function calls if present
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        name=fc.name,
                        arguments=dict(fc.args),
                        call_id=None,
                    ))

            usage = {}
            if hasattr(response, "usage_metadata"):
                usage = {
                    "input_tokens": getattr(response.usage_metadata, "prompt_token_count", 0),
                    "output_tokens": getattr(response.usage_metadata, "candidates_token_count", 0),
                }

            return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)

        except Exception as e:
            return ProviderResponse(content=f"[error: {e}]")

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            model = self._build_model("")
            response = await model.generate_content_async("ping")
            return bool(response.text)
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return available Gemini models for this API key."""
        try:
            return [m.name.replace("models/", "") for m in genai.list_models()
                    if "generateContent" in m.supported_generation_methods]
        except Exception:
            return []
