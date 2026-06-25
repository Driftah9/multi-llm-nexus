"""Single-leader lease + monotonic fencing token for the council orchestrator.

The council has ONE orchestrator at a time (failover primary→secondary→…, never two
concurrently) holding sole canonical-write authority. This is the leader-election
primitive that makes "single-writer by construction" enforceable across crashes and a
cooperative knock handoff — adopt-don't-invent: a lease (auto-expires if the holder dies)
+ a fencing token (rejects a stale, resurrected orchestrator's writes).

Backend: any Redis-compatible coordination store (SET NX PX / INCR / EVAL), connection
config-driven (NEXUS_COORD_REDIS_HOST/PORT/URL). It is an OPTIONAL capability — council
failover declares `needs_shared_state` in its manifest, so on a floor deployment with no
store wired this whole feature is DEFERRED (dark) and every method degrades gracefully
(acquire→None, holds→False) without raising.

# AGNOSTIC — the lease/fence/knock contract is store-agnostic; this binds to the Redis
# protocol. Swap the backend by reimplementing _redis().
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from .capability_gate import CapabilityRequirement

logger = logging.getLogger(__name__)

# This feature's activation bar (see capability_gate). Council deliberation/failover needs
# more than one capable orchestrator AND a shared coordination store.
REQUIREMENT = CapabilityRequirement(
    "council_failover", min_capable_executors=2, needs_shared_state=True,
    notes="Single-leader lease + fencing across orchestrators; dark at the 1-provider floor.",
)

_HOST = os.environ.get("NEXUS_COORD_REDIS_HOST", "localhost")
_PORT = int(os.environ.get("NEXUS_COORD_REDIS_PORT", "6379"))
_URL = os.environ.get("NEXUS_COORD_REDIS_URL", "")

_LEASE_KEY = "council:lease:orchestrator"
_FENCE_KEY = "council:lease:fence"
_KNOCK_KEY = "council:lease:knock"
DEFAULT_TTL_MS = 30_000

_RENEW_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


@dataclass
class LeaseState:
    holder: Optional[str]
    fencing_token: int
    ttl_ms: int
    mine: bool = False


def shared_store_available() -> bool:
    """True if a Redis-compatible coordination store is importable AND reachable —
    feeds SystemCapabilities.shared_state for the capability gate."""
    try:
        import redis  # noqa: F401
        c = _connect()
        if c is None:
            return False
        c.ping()
        return True
    except Exception:
        return False


def _connect():
    try:
        import redis
    except ImportError:
        return None
    try:
        if _URL:
            return redis.Redis.from_url(_URL, decode_responses=True,
                                        socket_timeout=2.0, socket_connect_timeout=2.0)
        return redis.Redis(host=_HOST, port=_PORT, decode_responses=True,
                           socket_timeout=2.0, socket_connect_timeout=2.0)
    except Exception:
        return None


class CouncilLease:
    """One instance per orchestrator candidate. `holder_id` identifies the provider/role
    competing for leadership. Graceful when no store is wired (all ops no-op)."""

    def __init__(self, holder_id: str, ttl_ms: int = DEFAULT_TTL_MS, namespace: str = ""):
        self.holder_id = holder_id
        self.ttl_ms = ttl_ms
        self._token = 0
        self._r = None
        _suffix = f":{namespace}" if namespace else ""
        self._lease_key = _LEASE_KEY + _suffix
        self._fence_key = _FENCE_KEY + _suffix
        self._knock_key = _KNOCK_KEY + _suffix

    def _redis(self):
        if self._r is None:
            self._r = _connect()
        return self._r

    @property
    def fencing_token(self) -> int:
        return self._token

    def acquire(self) -> Optional[int]:
        """Take leadership. Returns a NEW fencing token on success (caller is now the
        single writer), None if held elsewhere or no store. The fence increments only on
        a genuine acquisition — so a token uniquely identifies a leadership term."""
        r = self._redis()
        if r is None:
            return None
        try:
            if r.set(self._lease_key, self.holder_id, nx=True, px=self.ttl_ms):
                self._token = int(r.incr(self._fence_key))
                logger.info(f"[lease] {self.holder_id} ACQUIRED (fence={self._token})")
                return self._token
            return None
        except Exception as e:
            logger.warning(f"[lease] acquire failed: {e}")
            return None

    def renew(self) -> bool:
        r = self._redis()
        if r is None:
            return False
        try:
            return bool(r.eval(_RENEW_LUA, 1, self._lease_key, self.holder_id, self.ttl_ms))
        except Exception as e:
            logger.warning(f"[lease] renew failed: {e}")
            return False

    def release(self) -> bool:
        r = self._redis()
        if r is None:
            return False
        try:
            res = bool(r.eval(_RELEASE_LUA, 1, self._lease_key, self.holder_id))
            if res:
                logger.info(f"[lease] {self.holder_id} RELEASED")
            return res
        except Exception as e:
            logger.warning(f"[lease] release failed: {e}")
            return False

    def state(self) -> LeaseState:
        r = self._redis()
        if r is None:
            return LeaseState(holder=None, fencing_token=0, ttl_ms=-2)
        try:
            holder = r.get(self._lease_key)
            fence = int(r.get(self._fence_key) or 0)
            ttl = r.pttl(self._lease_key)
            return LeaseState(holder=holder, fencing_token=fence, ttl_ms=int(ttl),
                              mine=(holder == self.holder_id))
        except Exception as e:
            logger.warning(f"[lease] state read failed: {e}")
            return LeaseState(holder=None, fencing_token=0, ttl_ms=-2)

    def holds(self) -> bool:
        return self.state().mine

    def is_fenced_out(self, token: int) -> bool:
        """Split-brain guard. True if a writer holding `token` has been superseded (the
        fence advanced past it). The canonical writer MUST call this before committing and
        abort if True. Fail-CLOSED on read error (losing a write is safe; a split-brain
        double-write is not). No store → True (cannot prove leadership → don't write)."""
        r = self._redis()
        if r is None:
            return True
        try:
            cur = int(r.get(self._fence_key) or 0)
            return token < cur
        except Exception as e:
            logger.warning(f"[lease] fence check failed ({e}) — failing closed")
            return True

    # ── knock: cooperative preemption ────────────────────────────────────────
    def request_knock(self) -> bool:
        r = self._redis()
        if r is None:
            return False
        try:
            r.set(self._knock_key, self.holder_id, px=max(self.ttl_ms * 4, 60_000))
            logger.info(f"[lease] {self.holder_id} KNOCKED")
            return True
        except Exception as e:
            logger.warning(f"[lease] knock failed: {e}")
            return False

    def knock_pending(self) -> Optional[str]:
        r = self._redis()
        if r is None:
            return None
        try:
            return r.get(self._knock_key)
        except Exception:
            return None

    def clear_knock(self) -> bool:
        r = self._redis()
        if r is None:
            return False
        try:
            r.delete(self._knock_key)
            return True
        except Exception as e:
            logger.warning(f"[lease] clear_knock failed: {e}")
            return False
