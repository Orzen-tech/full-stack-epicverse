"""Distributed, PostgreSQL-backed OTP send rate limiting (security finding F-07).

Originally implemented against Redis; Redis existed in this project before
F-07 (semantic cache / session memory — see retriever.py, memory_store.py)
but the specific Redis Cloud instance this module was pointed at is no
longer reachable and its provenance could not be recovered. This module
now uses the project's EXISTING PostgreSQL connection pool instead — no
new database, no new pool, no new Redis service.

Design:
  - Two independent fixed-window (deterministic UTC 10-minute bucket)
    counters per send request: one keyed by an HMAC-SHA256 hash of the
    (lowercased) identifier, one keyed by an HMAC-SHA256 hash of the
    (X-Forwarded-For-derived) client IP. Neither the raw identifier nor
    the raw IP is ever stored, and plain SHA-256 is deliberately NOT used
    (it would be reversible via a dictionary attack against a known email
    corpus) — HMAC with a server-side secret is required.
  - Each counter is incremented atomically via a single
    `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING` statement per
    scope — Postgres's own atomic upsert primitive. No read-then-write,
    no process-local counters, no advisory lock or SELECT FOR UPDATE
    needed (the upsert's own row-level locking is sufficient).
  - No OTP value is ever read, written, or referenced by this module —
    it only ever touches integer counters.
  - Fail-CLOSED on a Postgres *operational* failure (query error, pool
    issue): when the counters cannot be read or written the send is
    rejected, so a storage fault can never turn into unlimited OTP emails.
    OTP verification's independent 5-wrong-attempt Postgres lockout is
    unaffected either way.
  - Fail-CLOSED (send rejected) if OTP_RATE_LIMIT_HASH_SECRET is missing
    or empty: a forgotten secret must never silently disable hashing or
    silently disable the limiter — see check_otp_send_allowed().
  - Never logs the identifier, the client IP, a hash, an OTP, a Firebase
    token, DATABASE_URL, or any other credential — every log line here is
    a fixed, generic string.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.core.config import settings
from app.services.db_pool import get_pool

IDENTIFIER_LIMIT = 3
IP_LIMIT = 20
WINDOW_MINUTES = 10

# Deterministic lazy cleanup: at most once every CLEANUP_INTERVAL per
# process/instance (not per request). Any instance attempting it is fine —
# the DELETE is idempotent and harmless if another instance already ran it
# in the same interval. No Cloud Scheduler, no new background service.
# 15 minutes is chosen to be comfortably longer than the 10-minute window
# (so a cleanup sweep always has at least one fully-expired window behind
# it to remove) while still keeping the table's steady-state size small;
# it does not need to be tight, since rows only ever matter for the
# single most-recent window per key.
CLEANUP_INTERVAL = timedelta(minutes=15)
CLEANUP_RETENTION = timedelta(minutes=20)

_last_cleanup_attempt: datetime | None = None


def _window_start(now: datetime) -> datetime:
    """Floors `now` (must be UTC) to the nearest deterministic
    WINDOW_MINUTES boundary, e.g. 10:00, 10:10, 10:20, ... — a fixed
    window, not a sliding one, matching the previously approved design."""
    floored_minute = (now.minute // WINDOW_MINUTES) * WINDOW_MINUTES
    return now.replace(minute=floored_minute, second=0, microsecond=0)


def _hmac_hash(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


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


async def _incr_scope(conn, key_hash: str, scope: str, window_start: datetime) -> int:
    """Atomic upsert-and-increment for one (key_hash, scope, window_start).
    A single statement — Postgres's own atomic UPSERT primitive; no
    separate SELECT/UPDATE, no advisory lock needed."""
    row = await conn.fetchrow(
        """
        INSERT INTO otp_send_rate_limits (key_hash, scope, window_start, request_count)
        VALUES ($1, $2, $3, 1)
        ON CONFLICT (key_hash, scope, window_start)
        DO UPDATE SET request_count = otp_send_rate_limits.request_count + 1
        RETURNING request_count
        """,
        key_hash, scope, window_start,
    )
    return row["request_count"]


async def _maybe_cleanup(conn) -> None:
    """Deterministic, process-local-timestamp-gated lazy cleanup. Runs at
    most once every CLEANUP_INTERVAL per instance; harmless if multiple
    instances happen to run it around the same time (the DELETE is
    idempotent). Never raises — cleanup failure must never affect the
    actual rate-limit check."""
    global _last_cleanup_attempt
    now = datetime.now(timezone.utc)
    if _last_cleanup_attempt is not None and (now - _last_cleanup_attempt) < CLEANUP_INTERVAL:
        return
    _last_cleanup_attempt = now
    try:
        await conn.execute(
            "DELETE FROM otp_send_rate_limits WHERE window_start < $1",
            now - CLEANUP_RETENTION,
        )
    except Exception:
        print("[OTP-RATE-LIMIT] Cleanup sweep failed (non-fatal)", flush=True)


async def check_otp_send_allowed(identifier: str, request: Request) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds).

    Fail-CLOSED (allowed=False) if OTP_RATE_LIMIT_HASH_SECRET is missing —
    a forgotten secret must never silently disable hashing or silently
    disable the limiter.

    Fail-CLOSED (allowed=False, retry in 60 s) if the Postgres operation
    itself fails; OTP verification's independent 5-wrong-attempt Postgres
    lockout is unaffected either way.
    """
    secret = settings.OTP_RATE_LIMIT_HASH_SECRET
    if not secret:
        print("[OTP-RATE-LIMIT] Rejected: hash secret not configured", flush=True)
        return False, 0

    now = datetime.now(timezone.utc)
    window_start = _window_start(now)
    retry_after = max(int((window_start + timedelta(minutes=WINDOW_MINUTES) - now).total_seconds()), 0)

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            id_hash = _hmac_hash(secret, identifier.lower())
            id_count = await _incr_scope(conn, id_hash, "identifier", window_start)
            if id_count > IDENTIFIER_LIMIT:
                return False, retry_after

            ip_hash = _hmac_hash(secret, client_ip(request))
            ip_count = await _incr_scope(conn, ip_hash, "ip", window_start)
            if ip_count > IP_LIMIT:
                return False, retry_after

            await _maybe_cleanup(conn)
            return True, 0
    except Exception:
        print("[OTP-RATE-LIMIT] Postgres unavailable, rejecting request (fail-closed)", flush=True)
        return False, 60
