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
        # stream-json (not buffered json) so we can surface incremental output AND capture
        # the session_id the bridge needs to persist for --resume continuity.
        args = [self.cli_path, "--print", "--output-format", "stream-json", "--verbose"]
        if self.model:
            args += ["--model", self.model]
        if self.effort:
            args += ["--effort", self.effort]
        args += ["--max-turns", str(self.max_turns)]
        # Session resumption: the bridge stashes the prior session_id in config.
        resume = self.config.get("resume_session")
        if resume:
            args += ["--resume", str(resume)]
        on_output = self.config.get("on_output")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024,  # 1MB line buffer — stream-json lines can be long
        )
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except Exception:
            pass

        try:
            return await asyncio.wait_for(self._read_stream(proc, on_output), timeout=self.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ProviderResponse(content="[timeout: no response within limit]")

    async def _read_stream(self, proc, on_output) -> ProviderResponse:
        """Parse the CLI's stream-json events: system→session_id, assistant→incremental
        text (fed to on_output), result→final text + usage + session_id. raw is the event
        list (plus a session_id marker the bridge reads to persist the session)."""
        events: list = []
        final_text = ""
        session_id = ""
        usage: dict = {}
        model = self.model
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(ev)
            etype = ev.get("type")
            if etype == "system":
                if ev.get("session_id"):
                    session_id = ev["session_id"]
            elif etype == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        final_text = block.get("text", "")
                        if on_output and final_text:
                            try:
                                r = on_output(final_text)
                                if asyncio.iscoroutine(r):
                                    await r
                            except Exception:
                                pass
            elif etype == "result":
                final_text = ev.get("result", final_text)
                usage = ev.get("usage", {}) or usage
                if ev.get("session_id"):
                    session_id = ev["session_id"]
                if ev.get("model"):
                    model = ev["model"]

        await proc.wait()
        if proc.returncode not in (0, None) and not final_text:
            stderr = (await proc.stderr.read()).decode(errors="replace").strip()
            return ProviderResponse(content=f"[error: {stderr[:300]}]", raw=events)
        # ensure the session_id is discoverable in raw (the bridge scans raw for it)
        if session_id:
            events.append({"session_id": session_id})
        return ProviderResponse(content=final_text, raw=events, model=model, usage=usage)

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
