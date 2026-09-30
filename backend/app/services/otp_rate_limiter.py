"""Distributed, Redis-backed OTP send rate limiting (security finding F-07).

Replaces the previous in-memory `_otp_rate`/`_otp_allowed` limiter, which
was per-process and therefore reset on every Cloud Run instance restart
and was not shared across concurrent instances. Reuses the existing
`settings.REDIS_URL` — no new Redis service, no new credentials.

Design:
  - Two independent fixed-window counters per send request: one keyed by a
    hash of the (lowercased) identifier, one keyed by a hash of the
    (X-Forwarded-For-derived) client IP. Neither the raw identifier nor the
    raw IP is ever stored in a Redis key.
  - Each counter is incremented and given its TTL in a single atomic Redis
    Lua script (INCR, then EXPIRE only on the request that created the
    key) — this closes the classic "INCR then separate EXPIRE" race, where
    a crash/interruption between the two calls could leave a key with no
    TTL at all, locking that identifier/IP out permanently instead of for
    the intended 10-minute window.
  - No OTP value is ever read, written, or referenced by this module —
    it only ever touches integer counters.
  - Fail-open: if Redis is unreachable for any reason, the send is
    allowed and a single generic warning is logged. This module never
    logs the identifier, the client IP, an OTP, a Firebase token, the
    Redis URL, or any other credential — every log line here is a fixed,
    generic string.
"""
import asyncio
import errno as errno_module
import hashlib
import ipaddress
import socket
import ssl
from urllib.parse import urlparse

from fastapi import Request

from app.core.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - redis package already a dependency elsewhere
    Redis = None  # type: ignore

try:
    from redis import exceptions as redis_exceptions
except Exception:  # pragma: no cover
    redis_exceptions = None  # type: ignore


def _classify_redis_error(e: Exception) -> str:
    """Coarse, safe failure category derived only from the exception's
    TYPE — never from its message, which can contain connection details
    (host, port, and in some redis-py versions, credential fragments).
    Diagnostic only; does not change fail-open behavior."""
    if redis_exceptions is not None:
        if isinstance(e, redis_exceptions.AuthenticationError):
            return "authentication_error"
        if isinstance(e, redis_exceptions.TimeoutError):
            return "timeout"
        if isinstance(e, redis_exceptions.ConnectionError):
            return "connection_error"
        if isinstance(e, redis_exceptions.ResponseError):
            return "response_error"
        if isinstance(e, redis_exceptions.RedisError):
            return "other_redis_error"
    if isinstance(e, asyncio.TimeoutError):
        return "timeout"
    if isinstance(e, ssl.SSLError):
        return "tls_error"
    if isinstance(e, socket.gaierror):
        return "dns_error"
    if isinstance(e, OSError):
        return "connection_error"
    return f"unclassified:{type(e).__name__}"


def _classify_connection_cause(e: Exception) -> str:
    """Refines a `connection_error` category by inspecting the TYPE (and,
    for OSError, the numeric .errno only — never any message/string) of
    the underlying cause chained onto the caught exception via Python's
    automatic exception chaining (__cause__/__context__). Makes NO new
    network call or DNS lookup — it only inspects an exception object
    that has already been raised by the Redis operation that already
    failed. Falls back to connection_other if no usable cause exists."""
    cause = e.__cause__ or e.__context__
    if cause is None:
        return "connection_other"

    if isinstance(cause, socket.gaierror):
        return "dns_resolution_error"
    if isinstance(cause, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(cause, ConnectionResetError):
        return "connection_reset"
    if isinstance(cause, OSError):
        if cause.errno in (errno_module.ENETUNREACH, errno_module.EHOSTUNREACH):
            return "network_unreachable"
        return "connection_other"

    return "connection_other"


def _classify_destination(redis_url: str) -> str:
    """Classifies ONLY the general category of the configured Redis
    destination (localhost / private_ip / public_ip / hostname /
    unix_socket / unknown) — never logs the hostname, IP, port,
    credentials, or any other part of the URL. Parsing/classification
    only; the raw value never reaches a log line."""
    try:
        parsed = urlparse(redis_url)
    except Exception:
        return "unknown"

    if parsed.scheme == "unix":
        return "unix_socket"

    host = parsed.hostname
    if not host:
        return "unknown"

    if host == "localhost":
        return "localhost"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP address -> it's a DNS hostname.
        return "hostname"

    if ip.is_loopback:
        return "localhost"
    if ip.is_private:
        return "private_ip"
    return "public_ip"


def _format_redis_failure(stage: str, e: Exception) -> str:
    """Builds the single fixed-shape diagnostic log line used by every
    Redis failure path below. Only ever includes fixed category labels
    (never a message/string derived from the exception or from
    settings.REDIS_URL)."""
    category = _classify_redis_error(e)
    cause_part = ""
    if category == "connection_error":
        cause_part = f", cause={_classify_connection_cause(e)}"
    destination = _classify_destination(settings.REDIS_URL)
    return (
        f"[OTP-RATE-LIMIT] Redis unavailable (stage={stage}, category={category}"
        f"{cause_part}, destination={destination}), allowing request"
    )

# Deliberately a separate client/connection from app.services.retriever's
# redis_client: that module permanently disables itself
# (_REDIS_ENABLED = False) after a single connection failure, which is the
# right trade-off for a non-critical semantic cache but the wrong one for a
# security control — this limiter should keep retrying on every call
# rather than staying disabled for the rest of the process's lifetime.
_otp_redis_client: "Redis | None" = None

IDENTIFIER_LIMIT = 3
IDENTIFIER_WINDOW_SECONDS = 600  # 10 minutes
IP_LIMIT = 20
IP_WINDOW_SECONDS = 600  # 10 minutes

_INCR_WITH_TTL_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


async def _get_redis() -> "Redis | None":
    global _otp_redis_client
    if Redis is None or not settings.REDIS_URL:
        return None
    if _otp_redis_client is not None:
        return _otp_redis_client
    try:
        client = Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
            health_check_interval=30,
        )
    except Exception as e:
        print(_format_redis_failure("from_url", e), flush=True)
        return None

    try:
        await client.ping()
    except Exception as e:
        print(_format_redis_failure("ping", e), flush=True)
        return None

    _otp_redis_client = client
    return _otp_redis_client


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    """Safe client-IP extraction for Cloud Run.

    Cloud Run's front-end proxy APPENDS the real client IP to
    X-Forwarded-For rather than replacing whatever the client sent, so a
    client can freely set their own value as the FIRST entry. Only the
    LAST entry is the one Google's own infrastructure added and a client
    cannot forge. `request.client.host` is not the real client IP on
    Cloud Run (it's Google's internal forwarding layer) and is used only
    as a last-resort fallback (e.g. local development).
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


async def _incr_with_ttl(redis, key: str, window_seconds: int) -> int:
    return await redis.eval(_INCR_WITH_TTL_SCRIPT, 1, key, window_seconds)


async def check_otp_send_allowed(identifier: str, request: Request) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds), matching the previous
    in-memory limiter's return shape. Fails open (allowed=True) if Redis
    is unavailable.
    """
    redis = await _get_redis()
    if redis is None:
        return True, 0

    try:
        id_key = f"otp:send:id:{_hash(identifier.lower())}"
        id_count = await _incr_with_ttl(redis, id_key, IDENTIFIER_WINDOW_SECONDS)
        if id_count > IDENTIFIER_LIMIT:
            ttl = await redis.ttl(id_key)
            return False, max(int(ttl), 0)

        ip_key = f"otp:send:ip:{_hash(client_ip(request))}"
        ip_count = await _incr_with_ttl(redis, ip_key, IP_WINDOW_SECONDS)
        if ip_count > IP_LIMIT:
            ttl = await redis.ttl(ip_key)
            return False, max(int(ttl), 0)

        return True, 0
    except Exception as e:
        print(_format_redis_failure("command", e), flush=True)
        return True, 0
