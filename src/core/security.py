"""
Central security layer — before-call authorization and scoping.

OpenClaw pattern: every API call path (adapter→bridge→provider) passes
through a single security checkpoint that validates:
  - Caller scope (what capability level do they have?)
  - Action type (what are they trying to do?)
  - Resource access (are they allowed to do it in this channel?)

Scope model:
  - admin: restart, deploy, config changes, scoped memory modifications
  - write: chat, specialist dispatch, autonomous replies, team access
  - read: status queries, heartbeat, cost reporting
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("nexus.security")


@dataclass
class SecurityContext:
    """Context for authorization checks."""

    user_id: str  # Platform user ID (mm:user123, dc:user456, etc)
    channel_id: str  # Channel/conversation ID
    action: str  # What the user is trying to do (chat, deploy, restart, etc)
    scope: str  # Current user scope (admin, write, read)
    session_key: str  # Session identifier


@dataclass
class AuthResult:
    """Result of an authorization check."""

    allowed: bool
    scope_required: str  # What scope was needed for this action
    reason: Optional[str] = None  # If denied, why


class SecurityPolicy:
    """
    Scope-based authorization for Nexus actions.

    Loads from config/security.yaml. Maps actions → required scopes.
    """

    # Built-in scope hierarchy
    SCOPES = {
        "read": {"status", "cost-query", "heartbeat"},
        "write": {
            "chat",
            "specialist-dispatch",
            "autonomous-reply",
            "command",
            "team-access",
        },
        "admin": {
            "restart",
            "deploy",
            "config-change",
            "memory-toggle",
            "wiki-ingest",
        },
    }

    # Scope hierarchy: admin > write > read
    SCOPE_RANK = {"read": 0, "write": 1, "admin": 2}

    def __init__(self, config: Optional[dict] = None):
        """
        Args:
            config: Security policy config (action mappings, overrides).
                   If None, uses built-in defaults.
        """
        self.config = config or {}
        self.custom_actions = self.config.get("custom_actions", {})
        self.defaults = self.config.get("defaults", {})

    def check(self, context: SecurityContext) -> AuthResult:
        """
        Check if the requested action is allowed for the user's scope.

        Args:
            context: SecurityContext with user, action, current scope

        Returns:
            AuthResult with allowed=True/False and reason
        """
        action = context.action
        user_scope = context.scope

        # Determine required scope for this action
        required_scope = self._get_required_scope(action)

        if required_scope is None:
            # Unknown action — default to safe behavior
            logger.warning(
                f"Unknown action '{action}' from {context.user_id} — denying"
            )
            return AuthResult(
                allowed=False,
                scope_required="unknown",
                reason=f"Action '{action}' is not recognized",
            )

        # Check scope hierarchy
        if self.SCOPE_RANK.get(user_scope, -1) >= self.SCOPE_RANK.get(
            required_scope, -1
        ):
            return AuthResult(allowed=True, scope_required=required_scope)

        return AuthResult(
            allowed=False,
            scope_required=required_scope,
            reason=f"Action '{action}' requires {required_scope} scope, user has {user_scope}",
        )

    def _get_required_scope(self, action: str) -> Optional[str]:
        """
        Determine the required scope for an action.

        Checks custom config first, then built-in mappings.

        Args:
            action: Action name (e.g., "chat", "deploy")

        Returns:
            Required scope ("read", "write", "admin"), or None if unknown
        """
        # Check custom actions
        if action in self.custom_actions:
            return self.custom_actions[action]

        # Check built-in scopes
        for scope, actions in self.SCOPES.items():
            if action in actions:
                return scope

        return None


class AuthorizationGate:
    """
    Integration point for bridge.invoke() — checks before calling provider.

    Usage:
        gate = AuthorizationGate(policy)
        result = gate.check_before_invoke(
            user_id="mm:user123",
            channel_id="mm:town-square",
            action="chat",
            current_scope="write"
        )
        if not result.allowed:
            raise AuthorizationError(result.reason)
    """

    def __init__(self, policy: Optional[SecurityPolicy] = None):
        self.policy = policy or SecurityPolicy()

    def check_before_invoke(
        self, user_id: str, channel_id: str, action: str, current_scope: str
    ) -> AuthResult:
        """
        Check authorization before invoking provider.

        Args:
            user_id: User identifier
            channel_id: Channel identifier
            action: Requested action
            current_scope: User's current scope level

        Returns:
            AuthResult with allowed flag and reason
        """
        context = SecurityContext(
            user_id=user_id,
            channel_id=channel_id,
            action=action,
            scope=current_scope,
            session_key=f"{channel_id}",
        )

        result = self.policy.check(context)

        if not result.allowed:
            logger.warning(
                f"Authorization denied: {context.user_id} in {context.channel_id} "
                f"action={action} scope={current_scope} reason={result.reason}"
            )
        else:
            logger.debug(
                f"Authorization granted: {context.user_id} action={action} scope={current_scope}"
            )

        return result


class AuthorizationError(Exception):
    """Raised when authorization check fails."""

    pass
