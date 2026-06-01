"""
Claude Code CLI provider — subprocess-based.
This is the reference implementation and the most capable provider
because it has full access to the MCP tool ecosystem.
Requires Claude Code CLI installed and authenticated.
"""
import asyncio
import json
import subprocess
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall


class ClaudeCodeProvider(BaseProvider):
    """
    Drives Claude via the Claude Code CLI subprocess.
    Full MCP tool access. Requires `claude` CLI on PATH and valid auth.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.cli_path = config.get("cli_path", "claude")
        self.timeout = config.get("timeout", 600)
        self.effort = config.get("effort", "")
        self.max_turns = config.get("max_turns", 10)

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        prompt = self._build_prompt(messages, system)
        args = [self.cli_path, "--print", "--output-format", "json"]
        if self.model:
            args += ["--model", self.model]
        if self.effort:
            args += ["--effort", self.effort]
        args += ["--max-turns", str(self.max_turns)]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ProviderResponse(content="[timeout: no response within limit]")

        if proc.returncode != 0:
            err = stderr.decode().strip()
            return ProviderResponse(content=f"[error: {err}]")

        return self._parse_output(stdout.decode())

    def _build_prompt(self, messages: list[Message], system: str) -> str:
        parts = []
        if system:
            parts.append(f"<system>\n{system}\n</system>")
        for msg in messages:
            if msg.role == "user":
                parts.append(msg.content)
            elif msg.role == "assistant":
                parts.append(f"[assistant]: {msg.content}")
        return "\n\n".join(parts)

    def _parse_output(self, raw: str) -> ProviderResponse:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                # --output-format json returns array of result objects
                for item in reversed(data):
                    if item.get("type") == "result":
                        return ProviderResponse(
                            content=item.get("result", ""),
                            raw=data,
                            model=item.get("model", ""),
                        )
            content = data.get("result", raw)
            return ProviderResponse(content=content, raw=data, model=data.get("model", ""))
        except (json.JSONDecodeError, AttributeError):
            return ProviderResponse(content=raw.strip())

    def supports_tools(self) -> bool:
        # MCP tools are invoked by the CLI process itself, not by us
        return True

    async def health_check(self) -> bool:
        try:
            result = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
