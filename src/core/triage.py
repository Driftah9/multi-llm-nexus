"""
Message triage — fast classification before routing to provider pool.

Classifies each message on five dimensions:
  task_type   — what kind of work (code, research, support, chat, system)
  priority    — high / normal / low
  urgency     — immediate (<2s needed) | normal | deferred (no time pressure)
  task_value  — routine | important | critical (affects paid token allocation)
  capability_required — general | code | search | reasoning | voice | rag
  estimated_complexity — nano | standard | deep

These dimensions feed into PoolRouter.select() which picks the cheapest
available provider that satisfies the task requirements.

Urgency and task_value are the primary cost-routing signals:
  - deferred + routine  → prefer free-tier / local, queue if needed
  - normal              → standard cost-class routing
  - immediate           → prefer local (low latency) or fast free-tier
  - critical / important → paid tokens justified when local/free exhausted
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..providers.base import BaseProvider, Message


@dataclass
class TriageResult:
    # Core fields (always populated)
    task_type: str                       # code | research | support | system | chat
    priority: str                        # high | normal | low
    is_command: bool
    command: Optional[str]
    confidence: float                    # 0.0 - 1.0

    # Routing dimensions (new)
    urgency: str = "normal"              # immediate | normal | deferred
    task_value: str = "routine"          # routine | important | critical
    capability_required: str = "general" # general | code | search | reasoning | voice | rag
    estimated_complexity: str = "standard"  # nano | standard | deep


# ── Keyword patterns ───────────────────────────────────────────────────────────

KEYWORD_PATTERNS = {
    "code": r"\b(code|debug|fix|error|bug|function|script|python|bash|implement|refactor|pr|commit|deploy)\b",
    "research": r"\b(search|find|look up|what is|explain|summarize|research|docs|who|when|where|why|how)\b",
    "support": r"\b(help|broken|not working|issue|problem|failed|crash|down|timeout)\b",
    "system": r"\b(status|restart|stop|start|deploy|build|monitor|health|alert|cron|service)\b",
}

COMPLEXITY_HINTS = {
    "nano": r"\b(quick|simple|short|brief|yes|no|confirm|thanks|ok|check|ping|test)\b",
    "deep": r"\b(analyze|analyse|architecture|design|review|audit|investigate|comprehensive|detailed|compare|strategy)\b",
}

URGENCY_HINTS = {
    "immediate": r"\b(urgent|asap|now|immediately|right now|critical|emergency|blocking|blocked)\b",
    "deferred":  r"\b(whenever|later|no rush|background|eventually|low priority|when you can)\b",
}

CAPABILITY_HINTS = {
    "code":      r"\b(code|script|function|class|implement|refactor|debug|pr|commit|git|python|bash|js|typescript)\b",
    "search":    r"\b(search|look up|find|current|latest|news|today|live|web|online)\b",
    "reasoning": r"\b(reason|think|analyze|analyse|logic|deduce|infer|why|because|explain|chain|step by step)\b",
    "voice":     r"\b(speak|say|read aloud|voice|audio|tts|speech)\b",
    "rag":       r"\b(document|pdf|upload|attachment|file|retrieval|context|knowledge base)\b",
}


class Triage:
    def __init__(self, provider: Optional["BaseProvider"] = None):
        self.provider = provider
        self._task_patterns = {
            task: re.compile(pattern, re.IGNORECASE)
            for task, pattern in KEYWORD_PATTERNS.items()
        }
        self._complexity_patterns = {
            level: re.compile(pattern, re.IGNORECASE)
            for level, pattern in COMPLEXITY_HINTS.items()
        }
        self._urgency_patterns = {
            level: re.compile(pattern, re.IGNORECASE)
            for level, pattern in URGENCY_HINTS.items()
        }
        self._capability_patterns = {
            cap: re.compile(pattern, re.IGNORECASE)
            for cap, pattern in CAPABILITY_HINTS.items()
        }

    async def classify(self, message: str) -> TriageResult:
        # Commands: detect locally, no LLM needed
        stripped = message.strip()
        if stripped.startswith("/") or stripped.startswith("!"):
            parts = stripped.split()
            cmd = parts[0][1:] if parts else ""
            return TriageResult(
                task_type="command",
                priority="high",
                is_command=True,
                command=cmd,
                confidence=1.0,
                urgency="immediate",
                task_value="routine",
                capability_required="general",
                estimated_complexity="nano",
            )

        if self.provider:
            return await self._llm_classify(message)

        return self._keyword_classify(message)

    async def _llm_classify(self, message: str) -> TriageResult:
        from ..providers.base import Message as Msg

        prompt = (
            "Classify this message on 5 dimensions. Reply with ONLY the pipe-separated format below.\n\n"
            "Format: category|urgency|value|capability|complexity\n\n"
            "category  : code | research | support | system | chat\n"
            "urgency   : immediate (response needed <2s) | normal | deferred (no rush)\n"
            "value     : routine | important (high-stakes) | critical (blocking others)\n"
            "capability: general | code | search | reasoning | voice | rag\n"
            "complexity: nano (any small model ok) | standard (mid model) | deep (frontier needed)\n\n"
            f"Message: {message[:500]}"
        )
        try:
            response = await self.provider.send(
                [Msg(role="user", content=prompt)],
                system=(
                    "You are a message classifier for an AI routing system. "
                    "Reply with ONLY the exact format: category|urgency|value|capability|complexity. "
                    "No explanation, no other text."
                ),
            )
            raw = response.content.strip()
            # Strip markdown code blocks if model wraps the reply
            if raw.startswith("`"):
                raw = raw.strip("`").strip()
            parts = [p.strip().lower() for p in raw.split("|")]

            if len(parts) == 5:
                category, urgency, value, capability, complexity = parts

                # Validate against allowed values — fall back to defaults on bad output
                valid_categories = {"code", "research", "support", "system", "chat"}
                valid_urgency = {"immediate", "normal", "deferred"}
                valid_value = {"routine", "important", "critical"}
                valid_capability = {"general", "code", "search", "reasoning", "voice", "rag"}
                valid_complexity = {"nano", "standard", "deep"}

                return TriageResult(
                    task_type=category if category in valid_categories else "chat",
                    priority=self._value_to_priority(value),
                    is_command=False,
                    command=None,
                    confidence=0.85,
                    urgency=urgency if urgency in valid_urgency else "normal",
                    task_value=value if value in valid_value else "routine",
                    capability_required=capability if capability in valid_capability else "general",
                    estimated_complexity=complexity if complexity in valid_complexity else "standard",
                )
        except Exception as e:
            logger.debug(f"LLM triage failed: {e}")

        return self._keyword_classify(message)

    def _keyword_classify(self, message: str) -> TriageResult:
        """Keyword-based fallback when no LLM triage provider is available."""
        # Task type
        task_type = "chat"
        for task, pattern in self._task_patterns.items():
            if pattern.search(message):
                task_type = task
                break

        # Complexity
        complexity = "standard"
        if self._complexity_patterns["nano"].search(message):
            complexity = "nano"
        elif self._complexity_patterns["deep"].search(message):
            complexity = "deep"

        # Urgency
        urgency = "normal"
        if self._urgency_patterns["immediate"].search(message):
            urgency = "immediate"
        elif self._urgency_patterns["deferred"].search(message):
            urgency = "deferred"

        # Capability
        capability = "general"
        for cap, pattern in self._capability_patterns.items():
            if pattern.search(message):
                capability = cap
                break

        # Value — heuristic: support + immediate = important; code = important
        task_value = "routine"
        if task_type in ("support", "system") and urgency == "immediate":
            task_value = "important"
        elif task_type == "code":
            task_value = "important"

        return TriageResult(
            task_type=task_type,
            priority=self._value_to_priority(task_value),
            is_command=False,
            command=None,
            confidence=0.6,
            urgency=urgency,
            task_value=task_value,
            capability_required=capability,
            estimated_complexity=complexity,
        )

    @staticmethod
    def _value_to_priority(task_value: str) -> str:
        return {"critical": "high", "important": "high", "routine": "normal"}.get(
            task_value, "normal"
        )
