"""
Amazon Bedrock provider — Claude, Llama, Mistral, Titan, Cohere under one AWS bill.

Uses the Bedrock Converse API (unified interface across all hosted models).
Auth via standard AWS credential chain: env vars, ~/.aws/credentials, or IAM role.

Install: pip install boto3
"""
import json
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import boto3
    import botocore.exceptions
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class BedrockProvider(BaseProvider):
    """
    Amazon Bedrock provider using the Converse API.

    Config keys:
      model        — Bedrock model ID (e.g. "anthropic.claude-3-5-sonnet-20241022-v2:0")
      region       — AWS region, default "us-east-1"
      profile      — AWS named profile (optional, uses default chain if unset)
      access_key   — AWS_ACCESS_KEY_ID (optional, uses env/profile if unset)
      secret_key   — AWS_SECRET_ACCESS_KEY (optional)
      max_tokens   — default 4096
      temperature  — default 0.7

    Auth priority: config keys → env vars → ~/.aws/credentials profile → IAM role
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 package required: pip install boto3")

        region = config.get("region", "us-east-1")
        profile = config.get("profile") or None
        access_key = config.get("access_key") or None
        secret_key = config.get("secret_key") or None
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 0.7)

        session_kwargs: dict = {}
        if profile:
            session_kwargs["profile_name"] = profile

        session = boto3.Session(**session_kwargs)

        client_kwargs: dict = {"region_name": region}
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key

        self._client = session.client("bedrock-runtime", **client_kwargs)
        self._mgmt_client = session.client("bedrock", **client_kwargs)

    def _convert_messages(self, messages: list[Message]) -> tuple[list[dict], Optional[str]]:
        """Returns (converse_messages, system_prompt)."""
        system_prompt: Optional[str] = None
        converse_msgs = []
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
                continue
            converse_msgs.append({
                "role": msg.role,
                "content": [{"text": msg.content}],
            })
        return converse_msgs, system_prompt

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        import asyncio
        converse_msgs, inline_system = self._convert_messages(messages)
        effective_system = system or inline_system

        kwargs: dict = {
            "modelId": self.model,
            "messages": converse_msgs,
            "inferenceConfig": {
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if effective_system:
            kwargs["system"] = [{"text": effective_system}]

        # boto3 is sync — run in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.converse(**kwargs)
        )

        output = response.get("output", {}).get("message", {})
        content = ""
        tool_calls = []

        for block in output.get("content", []):
            if block.get("type") == "text" or "text" in block:
                content += block.get("text", "")
            elif block.get("type") == "toolUse" or "toolUse" in block:
                tu = block.get("toolUse", block)
                tool_calls.append(ToolCall(
                    name=tu.get("name", ""),
                    arguments=tu.get("input", {}),
                    call_id=tu.get("toolUseId"),
                ))

        usage_raw = response.get("usage", {})
        usage = {
            "input_tokens": usage_raw.get("inputTokens", 0),
            "output_tokens": usage_raw.get("outputTokens", 0),
        }

        return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self._mgmt_client.get_foundation_model(modelIdentifier=self.model)
            )
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Return available Bedrock foundation model IDs."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, lambda: self._mgmt_client.list_foundation_models(
                    byOutputModality="TEXT"
                )
            )
            return [
                m["modelId"]
                for m in response.get("modelSummaries", [])
                if m.get("responseStreamingSupported", True)
            ]
        except Exception:
            return []
