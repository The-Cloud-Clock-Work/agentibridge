"""Shared Redis helper for agentic-bridge.

Provides a standalone Redis connection by reading environment variables directly.

Usage::

    from agentic_bridge.redis_client import get_redis, redis_key, POSITION_TTL, SESSION_TTL

    r = get_redis()
    if r is not None:
        r.setex(redis_key("pos:transcript", session_id), POSITION_TTL, position)
"""

import os
import sys

# Lazy singleton
_redis_client = None
_redis_checked = False

# TTLs from environment (matching agenticore settings defaults)
SESSION_TTL: int = int(os.getenv("REDIS_SESSION_TTL", "86400"))  # 24h
POSITION_TTL: int = int(os.getenv("REDIS_POSITION_TTL", "3600"))  # 1h
_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "agenticore")


def get_redis():
    """Return a ``redis.Redis`` client or ``None`` if unavailable.

    Lazily connects on first call.  Returns ``None`` when ``REDIS_URL`` is
    unset or when the connection cannot be established.
    """
    global _redis_client, _redis_checked

    if _redis_checked:
        return _redis_client

    _redis_checked = True
    url = os.getenv("REDIS_URL")
    if not url:
        return None

    try:
        import redis as redis_lib

        timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", "5.0"))
        _redis_client = redis_lib.Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
        )
        _redis_client.ping()  # fail-fast on bad URL
    except Exception as exc:
        print(f"Redis connection failed ({url}): {exc}", file=sys.stderr)
        _redis_client = None

    return _redis_client


def redis_key(type_name: str, id_value: str) -> str:
    """Build a namespaced Redis key.

    Example::

        redis_key("pos:transcript", "abc") -> "agenticore:pos:transcript:abc"
    """
    return f"{_KEY_PREFIX}:{type_name}:{id_value}"
