"""
Tool definitions for provider function calling.

Defines tools that providers with function-calling support can invoke.
Each tool has a schema (for the LLM) and an execute function (for the runtime).

Operators can extend this by adding tools to config/tools/ as YAML files.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger("nexus.tools")


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    execute: Optional[Callable[..., Awaitable[Any]]] = None
    category: str = "general"
    requires_confirmation: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central registry of available tools."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._register_builtins()

    def _register_builtins(self):
        self.register(ToolDef(
            name="get_current_time",
            description="Get the current date and time in the operator's timezone.",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=self._exec_current_time,
            category="system",
        ))

        self.register(ToolDef(
            name="list_spaces",
            description="List all registered spaces (projects, business units, etc).",
            parameters={"type": "object", "properties": {}, "required": []},
            execute=self._exec_list_spaces,
            category="organization",
        ))

        self.register(ToolDef(
            name="search_history",
            description="Search conversation history for a keyword or phrase.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term"},
                    "space_key": {"type": "string", "description": "Limit to a specific space (optional)"},
                },
                "required": ["query"],
            },
            execute=self._exec_search_history,
            category="memory",
        ))

        self.register(ToolDef(
            name="check_service_health",
            description="Check if a service endpoint is reachable.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to check"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 5},
                },
                "required": ["url"],
            },
            execute=self._exec_health_check,
            category="system",
        ))

    def register(self, tool: ToolDef):
        self._tools[tool.name] = tool
        logger.debug(f"Tool registered: {tool.name}")

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def by_category(self, category: str) -> list[ToolDef]:
        return [t for t in self._tools.values() if t.category == category]

    def schemas(self, categories: list[str] = None) -> list[dict]:
        tools = self._tools.values()
        if categories:
            tools = [t for t in tools if t.category in categories]
        return [t.schema() for t in tools]

    async def execute(self, name: str, arguments: dict) -> Any:
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        if not tool.execute:
            return {"error": f"Tool {name} has no execute handler"}
        try:
            return await tool.execute(**arguments)
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}")
            return {"error": str(e)}

    def load_custom(self, tools_dir: Path):
        """Load operator-defined tools from YAML files in config/tools/."""
        if not tools_dir.exists():
            return
        import yaml
        for f in sorted(tools_dir.glob("*.yaml")) + sorted(tools_dir.glob("*.yml")):
            try:
                data = yaml.safe_load(f.read_text())
                if not data or "name" not in data:
                    continue
                tool = ToolDef(
                    name=data["name"],
                    description=data.get("description", ""),
                    parameters=data.get("parameters", {"type": "object", "properties": {}}),
                    category=data.get("category", "custom"),
                    requires_confirmation=data.get("requires_confirmation", False),
                )
                self.register(tool)
                logger.info(f"Custom tool loaded: {tool.name} from {f.name}")
            except Exception as e:
                logger.warning(f"Failed to load tool from {f}: {e}")

    # ── Built-in executors ──────────────────────────────────────

    @staticmethod
    async def _exec_current_time(**kwargs) -> dict:
        import datetime
        now = datetime.datetime.now()
        return {"time": now.isoformat(), "timezone": str(now.astimezone().tzinfo)}

    @staticmethod
    async def _exec_list_spaces(**kwargs) -> dict:
        return {"note": "Requires SpaceRegistry injection — not yet wired"}

    @staticmethod
    async def _exec_search_history(query: str, space_key: str = None, **kwargs) -> dict:
        return {"note": "Requires session history injection — not yet wired"}

    @staticmethod
    async def _exec_health_check(url: str, timeout: int = 5, **kwargs) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
                return {"status": resp.status_code, "ok": resp.status_code < 400, "url": url}
        except Exception as e:
            return {"status": 0, "ok": False, "url": url, "error": str(e)}
