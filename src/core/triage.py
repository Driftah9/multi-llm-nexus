"""
Message triage — fast classification before routing to primary provider.
Uses the triage_provider (typically a fast/local model) to classify intent.
Falls back to keyword heuristics if no triage provider is configured.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..providers.base import BaseProvider, Message


@dataclass
class TriageResult:
    task_type: str          # "code", "research", "support", "triage", "command", "chat"
    priority: str           # "high", "normal", "low"
    is_command: bool        # starts with /
    command: Optional[str]  # the command name if is_command
    confidence: float       # 0.0 - 1.0


KEYWORD_PATTERNS = {
    "code": r"\b(code|debug|fix|error|bug|function|script|python|bash|implement|refactor)\b",
    "research": r"\b(search|find|look up|what is|explain|summarize|research|docs)\b",
    "support": r"\b(help|broken|not working|issue|problem|failed|crash)\b",
    "system": r"\b(status|restart|stop|start|deploy|build|monitor|health)\b",
}


class Triage:
    def __init__(self, provider: Optional["BaseProvider"] = None):
        self.provider = provider
        self._compiled = {
            task: re.compile(pattern, re.IGNORECASE)
            for task, pattern in KEYWORD_PATTERNS.items()
        }

    async def classify(self, message: str) -> TriageResult:
        # Command detection is always local — no LLM needed
        if message.strip().startswith("/"):
            parts = message.strip().split()
            return TriageResult(
                task_type="command",
                priority="high",
                is_command=True,
                command=parts[0][1:],
                confidence=1.0
            )

        # Use LLM triage if available
        if self.provider:
            return await self._llm_classify(message)

        # Keyword fallback
        return self._keyword_classify(message)

    async def _llm_classify(self, message: str) -> TriageResult:
        from ..providers.base import Message as Msg
        prompt = (
            f"Classify this message into ONE category: code, research, support, system, chat\n"
            f"Also rate priority: high, normal, low\n"
            f"Reply with ONLY: category|priority\n\n"
            f"Message: {message[:500]}"
        )
        try:
            response = await self.provider.send(
                [Msg(role="user", content=prompt)],
                system="You are a message classifier. Reply with only the format: category|priority"
            )
            parts = response.content.strip().split("|")
            if len(parts) == 2:
                return TriageResult(
                    task_type=parts[0].strip().lower(),
                    priority=parts[1].strip().lower(),
                    is_command=False,
                    command=None,
                    confidence=0.85
                )
        except Exception as e:
            logger.debug(f"LLM triage failed: {e}")
        return self._keyword_classify(message)

    def _keyword_classify(self, message: str) -> TriageResult:
        for task, pattern in self._compiled.items():
            if pattern.search(message):
                return TriageResult(
                    task_type=task,
                    priority="normal",
                    is_command=False,
                    command=None,
                    confidence=0.6
                )
        return TriageResult(
            task_type="chat",
            priority="normal",
            is_command=False,
            command=None,
            confidence=0.5
        )
