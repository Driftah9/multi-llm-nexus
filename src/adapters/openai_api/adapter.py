"""
OpenAI-compatible HTTP adapter for Nexus.

Exposes /v1/chat/completions and /v1/models so any OpenAI-compatible
IDE extension (Continue.dev, Cursor, Copilot alternatives) can treat
Nexus as their AI provider. Routing, failover, and specialist dispatch
happen transparently inside the bridge — the IDE sees one endpoint.

Model names map to Nexus tiers:
  nexus           → auto-routed (triage decides)
  nexus-nano      → TIER_NANO   (fast, cheap)
  nexus-standard  → TIER_STANDARD
  nexus-deep      → TIER_DEEP   (most capable)

Streaming note: v1 sends the complete response as a single SSE content
chunk. Token-by-token streaming requires provider-level on_chunk passthrough
and can be added when needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)

_TIER_MAP = {
    "nexus-nano": "nano",
    "nexus-standard": "standard",
    "nexus-deep": "deep",
}

_MODELS = ["nexus", "nexus-nano", "nexus-standard", "nexus-deep"]


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "nexus"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None


class OpenAIApiAdapter:
    """
    HTTP adapter exposing an OpenAI-compatible /v1 API surface.

    Unlike messaging adapters this bypasses the Engine queue — HTTP is
    synchronous request/response so we call the bridge directly.
    """

    def __init__(self, bridge, config: dict):
        self.bridge = bridge
        self.host: str = config.get("host", "127.0.0.1")
        self.port: int = int(config.get("port", 8080))
        self.api_key: str = config.get("api_key", "")
        self._app = FastAPI(title="Nexus", docs_url=None, redoc_url=None)
        self._server: Optional[uvicorn.Server] = None
        self._register_routes()

    # ── Routes ────────────────────────────────────────────────────────────────

    def _register_routes(self) -> None:
        adapter = self

        @self._app.get("/v1/models")
        async def list_models():
            ts = int(time.time())
            return {
                "object": "list",
                "data": [
                    {"id": m, "object": "model", "created": ts, "owned_by": "nexus"}
                    for m in _MODELS
                ],
            }

        @self._app.post("/v1/chat/completions")
        async def chat_completions(request: Request, body: ChatCompletionRequest):
            adapter._check_auth(request)
            if body.stream:
                return StreamingResponse(
                    adapter._stream(body, request),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            return await adapter._complete(body, request)

        @self._app.get("/health")
        async def health():
            return {"status": "ok", "service": "nexus"}

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _check_auth(self, request: Request) -> None:
        if not self.api_key:
            return
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != self.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _session_key(self, body: ChatCompletionRequest, request: Request) -> str:
        user = body.user
        if not user and request.client:
            user = request.client.host
        return f"openai_api:{user or 'default'}"

    def _parse_messages(self, messages: list[ChatMessage]) -> tuple[str, str]:
        """Return (system_prompt, last_user_message)."""
        system = ""
        last_user = ""
        for msg in messages:
            if msg.role == "system":
                system = msg.content
            elif msg.role == "user":
                last_user = msg.content
        return system, last_user

    # ── Response builders ─────────────────────────────────────────────────────

    async def _complete(self, body: ChatCompletionRequest, request: Request) -> dict:
        system, prompt = self._parse_messages(body.messages)
        session_key = self._session_key(body, request)
        tier = _TIER_MAP.get(body.model)

        result = await self.bridge.invoke(
            prompt=prompt,
            session_key=session_key,
            tier=tier,
            system_prompt=system or None,
        )

        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        return {
            "id": cid,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
        }

    async def _stream(
        self, body: ChatCompletionRequest, request: Request
    ) -> AsyncGenerator[str, None]:
        """SSE stream — role header → full content chunk → stop → [DONE]."""
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        ts = int(time.time())
        system, prompt = self._parse_messages(body.messages)
        session_key = self._session_key(body, request)
        tier = _TIER_MAP.get(body.model)

        def _chunk(delta: dict, finish: Optional[str] = None) -> str:
            payload = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": ts,
                "model": body.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload)}\n\n"

        yield _chunk({"role": "assistant"})

        try:
            result = await self.bridge.invoke(
                prompt=prompt,
                session_key=session_key,
                tier=tier,
                system_prompt=system or None,
            )
            content = result.text
        except Exception as e:
            logger.error(f"OpenAI API bridge error: {e}")
            content = f"_(error: {e})_"

        yield _chunk({"content": content})
        yield _chunk({}, finish="stop")
        yield "data: [DONE]\n\n"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        pass

    async def run(self) -> None:
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        logger.info(f"OpenAI API adapter listening on http://{self.host}:{self.port}/v1")
        await self._server.serve()

    async def disconnect(self) -> None:
        if self._server:
            self._server.should_exit = True
