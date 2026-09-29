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
import hashlib

from fastapi import Request

from app.core.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - redis package already a dependency elsewhere
    Redis = None  # type: ignore

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
        await client.ping()
        _otp_redis_client = client
        return _otp_redis_client
    except Exception:
        print("[OTP-RATE-LIMIT] Redis unavailable, allowing request", flush=True)
        return None


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
    except Exception:
        print("[OTP-RATE-LIMIT] Redis unavailable, allowing request", flush=True)
        return True, 0
