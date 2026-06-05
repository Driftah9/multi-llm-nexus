"""
User gate — two-stage pre-triage authorization.

Runs BEFORE any LLM call. Zero cloud token cost.

Stage 1: ACL + regex (microseconds, always free)
  - Is this user in the ACL?
  - Does the message match a hard deny pattern?
  - Is this user rate-limited?

Stage 2: Local LLM intent check (nano tier, ~50ms, zero cloud cost)
  - Only runs when a local provider is configured
  - Classifies message intent: chat | research | code | system | admin
  - Checks intent against user's allow/deny scope
  - Falls back to allow-by-default if local LLM unavailable

Config format in adapters.yaml:

  discord:
    default_user_scope: deny          # unknown users: deny | allow
    rate_limit:
      window_seconds: 60
      max_messages: 10
    users:
      "151160889318309889":           # operator — unrestricted
        scope: all
      "987654321000000000":           # invited user — limited
        allow: [research, chat, general_questions]
        deny: [system, admin, code_execution, file_access]
        rate_limit:                   # override global rate limit
          window_seconds: 60
          max_messages: 5

Intent categories for allow/deny lists:
  chat            — general conversation
  research        — search, look things up, explain topics
  code            — write code, debug, review
  system          — server status, service control, infrastructure
  admin           — deploy, restart, config changes, memory writes
  file_access     — read/write files, directory listings
  code_execution  — run scripts, execute commands
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..providers.base import BaseProvider

logger = logging.getLogger("nexus.user_gate")


class GateDecision(Enum):
    ALLOW = "allow"
    DENY_SILENT = "deny_silent"   # Drop with no response — default for unauthorized users
    DENY_RESPOND = "deny_respond" # Could send a canned response (future use)


@dataclass
class GateResult:
    decision: GateDecision
    reason: str = ""
    intent: Optional[str] = None  # What local LLM classified the message as

    @property
    def allowed(self) -> bool:
        return self.decision == GateDecision.ALLOW


# ── Hard deny patterns (Stage 1 regex) ──────────────────────────────────────
# These block regardless of user scope — catches obvious system/admin attempts

HARD_DENY_PATTERNS = [
    # System commands
    r"\b(systemctl|journalctl|crontab|sudo|su\s|passwd|useradd|userdel)\b",
    # Shell and code execution
    r"\b(exec\(|eval\(|subprocess|os\.system|Popen|__import__)\b",
    r"\b(bash|sh\s+-c|python\s+-c|perl\s+-e|ruby\s+-e)\s+['\"]",
    # Direct file system access
    r"\bcat\s+/|ls\s+/|rm\s+[-rf]|chmod\s+|chown\s+",
    r"\b(\/etc\/|\/var\/|\/home\/|\/root\/|\/proc\/|\/sys\/)\b",
    # Network and SSH
    r"\b(ssh\s+|scp\s+|rsync\s+|nc\s+|netcat|nmap|curl\s+http)\b",
    # Docker/container control
    r"\bdocker\s+(rm|stop|kill|exec|run|pull)\b",
    # Bot control commands
    r"^[!\/](restart|stop|deploy|config|admin|reset-all|wipe)\b",
]

_compiled_hard_deny = [
    re.compile(p, re.IGNORECASE) for p in HARD_DENY_PATTERNS
]


# ── Local LLM intent classification ─────────────────────────────────────────

INTENT_PROMPT = (
    "Classify this message intent into ONE word from this list:\n"
    "chat | research | code | system | admin | file_access | code_execution\n\n"
    "chat = general conversation, questions, opinions\n"
    "research = search, look up, explain, summarize, find info\n"
    "code = write code, debug, review code, implement features\n"
    "system = server status, service health, infrastructure queries\n"
    "admin = deploy, restart, config changes, memory writes, system control\n"
    "file_access = read files, list directories, access file system\n"
    "code_execution = run scripts, execute commands, shell access\n\n"
    "Reply with ONLY the single word. Nothing else.\n\n"
    f"Message: {{message}}"
)

VALID_INTENTS = {"chat", "research", "code", "system", "admin", "file_access", "code_execution"}

# Intents that always require admin scope regardless of user config
ADMIN_ONLY_INTENTS = {"admin", "file_access", "code_execution"}


# ── Rate limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Sliding-window rate limiter per user."""

    def __init__(self, window_seconds: int = 60, max_messages: int = 10):
        self.window = window_seconds
        self.max_messages = max_messages
        self._windows: dict[str, deque] = {}

    def check(self, user_id: str) -> bool:
        """Returns True if allowed, False if rate-limited."""
        now = time.time()
        cutoff = now - self.window

        if user_id not in self._windows:
            self._windows[user_id] = deque()

        w = self._windows[user_id]
        # Expire old entries
        while w and w[0] < cutoff:
            w.popleft()

        if len(w) >= self.max_messages:
            return False

        w.append(now)
        return True


# ── Gate result cache ─────────────────────────────────────────────────────────

class GateCache:
    """
    Short-lived cache for gate decisions.
    Prevents re-running local LLM for identical/similar messages from same user.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 500):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict[str, tuple[float, GateResult]] = {}

    def _key(self, user_id: str, message: str) -> str:
        h = hashlib.md5(message.strip().lower().encode()).hexdigest()[:12]
        return f"{user_id}:{h}"

    def get(self, user_id: str, message: str) -> Optional[GateResult]:
        k = self._key(user_id, message)
        entry = self._store.get(k)
        if entry and (time.time() - entry[0]) < self.ttl:
            return entry[1]
        return None

    def set(self, user_id: str, message: str, result: GateResult) -> None:
        # Evict oldest if full
        if len(self._store) >= self.max_size:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._key(user_id, message)
        self._store[self._key(user_id, message)] = (time.time(), result)


# ── Main gate ─────────────────────────────────────────────────────────────────

class UserGate:
    """
    Two-stage pre-triage authorization gate.

    Drop in before any triage or LLM call. When result.allowed is False,
    the adapter silently drops the message.

    Usage:
        gate = UserGate(platform="discord", config=adapters_config["discord"])
        result = await gate.check(user_id="987654321", message=text)
        if not result.allowed:
            return  # silent drop
    """

    def __init__(
        self,
        platform: str,
        config: dict,
        local_provider: Optional["BaseProvider"] = None,
    ):
        self.platform = platform
        self.local_provider = local_provider

        # Load user ACL config
        self.users: dict[str, dict] = config.get("users", {})
        self.default_scope: str = config.get("default_user_scope", "deny")

        # Rate limiting
        global_rl = config.get("rate_limit", {})
        self._global_rate = RateLimiter(
            window_seconds=global_rl.get("window_seconds", 60),
            max_messages=global_rl.get("max_messages", 10),
        )
        # Per-user rate limiters (created on demand)
        self._user_rates: dict[str, RateLimiter] = {}

        self._cache = GateCache()

    def _get_user_config(self, user_id: str) -> Optional[dict]:
        """Look up user config. Checks string ID directly."""
        return self.users.get(str(user_id))

    def _get_rate_limiter(self, user_id: str, user_cfg: Optional[dict]) -> RateLimiter:
        """Get per-user rate limiter if configured, else global."""
        if user_cfg and "rate_limit" in user_cfg:
            if user_id not in self._user_rates:
                rl = user_cfg["rate_limit"]
                self._user_rates[user_id] = RateLimiter(
                    window_seconds=rl.get("window_seconds", 60),
                    max_messages=rl.get("max_messages", 5),
                )
            return self._user_rates[user_id]
        return self._global_rate

    def _regex_hard_deny(self, message: str) -> bool:
        """Stage 1B: hard deny regardless of user scope."""
        for pattern in _compiled_hard_deny:
            if pattern.search(message):
                return True
        return False

    def _scope_allows(self, user_cfg: dict, intent: str) -> bool:
        """Check if user's scope allows this intent."""
        # Admin-only intents blocked for non-admin users
        if intent in ADMIN_ONLY_INTENTS and user_cfg.get("scope") != "all":
            return False

        deny_list = user_cfg.get("deny", [])
        allow_list = user_cfg.get("allow", [])

        if intent in deny_list:
            return False
        if allow_list and intent not in allow_list:
            return False
        return True

    async def _local_classify_intent(self, message: str) -> Optional[str]:
        """
        Stage 2: Ask local nano LLM what the user intends.
        Returns intent string or None if classification failed.
        """
        if not self.local_provider:
            return None

        from ..providers.base import Message as Msg
        try:
            prompt = INTENT_PROMPT.format(message=message[:300])
            response = await self.local_provider.send(
                [Msg(role="user", content=prompt)],
                system="You are a message classifier. Reply with ONE word only.",
            )
            intent = response.content.strip().lower().split()[0]
            return intent if intent in VALID_INTENTS else None
        except Exception as e:
            logger.debug(f"Local intent classify failed: {e}")
            return None

    async def check(self, user_id: str, message: str) -> GateResult:
        """
        Run the full gate check for a user+message.

        Returns GateResult. If result.allowed is False, caller should
        silently drop the message without any response.
        """
        user_id = str(user_id)
        user_cfg = self._get_user_config(user_id)

        # ── Stage 0: Unknown user ─────────────────────────────────────────
        if user_cfg is None:
            if self.default_scope == "deny":
                logger.debug(f"Gate: unknown user {user_id} on {self.platform} — silent drop")
                return GateResult(GateDecision.DENY_SILENT, "user not in ACL")
            # default_scope = "allow" — unknown users get through but still hit later stages

        # ── Stage 0B: Operator (scope: all) ──────────────────────────────
        if user_cfg and user_cfg.get("scope") == "all":
            return GateResult(GateDecision.ALLOW, "operator scope")

        # ── Stage 1A: Rate limit ──────────────────────────────────────────
        rl = self._get_rate_limiter(user_id, user_cfg)
        if not rl.check(user_id):
            logger.debug(f"Gate: {user_id} rate-limited on {self.platform}")
            return GateResult(GateDecision.DENY_SILENT, "rate limited")

        # ── Stage 1B: Hard regex deny ─────────────────────────────────────
        if self._regex_hard_deny(message):
            logger.debug(f"Gate: {user_id} hard deny (regex) on {self.platform}")
            return GateResult(GateDecision.DENY_SILENT, "hard deny pattern matched")

        # ── Check cache before Stage 2 ────────────────────────────────────
        cached = self._cache.get(user_id, message)
        if cached is not None:
            logger.debug(f"Gate: {user_id} cache hit on {self.platform}: {cached.decision}")
            return cached

        # ── Stage 2: Local LLM intent check ──────────────────────────────
        intent = None
        if self.local_provider and user_cfg:
            intent = await self._local_classify_intent(message)

            if intent:
                if not self._scope_allows(user_cfg, intent):
                    logger.info(
                        f"Gate: {user_id} denied — intent '{intent}' "
                        f"not in scope on {self.platform}"
                    )
                    result = GateResult(
                        GateDecision.DENY_SILENT,
                        f"intent '{intent}' not in user scope",
                        intent=intent,
                    )
                    self._cache.set(user_id, message, result)
                    return result

        # ── Allowed ───────────────────────────────────────────────────────
        result = GateResult(GateDecision.ALLOW, "passed all checks", intent=intent)
        self._cache.set(user_id, message, result)
        return result
