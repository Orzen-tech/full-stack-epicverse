"""Persistence for the encrypted-credential gateway: single-use server nonces, the gateway's own rate limiter, and proof claims.

Three NEW tables with names that no other EpicVerse lineage uses (checked against the deployed lineage and the unreleased main):
  * enc_auth_nonces       - only a SHA-256 of each nonce is stored. Consuming is ONE atomic UPDATE ... WHERE consumed_at IS NULL.
  * enc_auth_limits       - fixed-window counters keyed by an HMAC of scope and value (no e-mail address or IP is stored).
  * enc_auth_proof_claims - which verified Firebase UID first used an e-mail ownership proof at the gateway (key = an HMAC of the raw
                            proof; the raw proof is never stored). Written only AFTER Firebase confirmed the account.
Nothing here touches an existing table, and the existing OTP rate limiter is not used or modified. Nothing is ever logged with a nonce,
e-mail, IP, proof or credential.
"""
import asyncio
import contextlib
import hashlib
import hmac
import secrets
import time

import asyncpg

from app.core.config import settings
from app.services.db_pool import get_pool
from app.services.enc_auth_envelope import (
    CAPACITY, EXPIRED, OPERATIONS, REPLAY, UNKNOWN_NONCE, WRONG_OPERATION, EnvelopeError, b64e,
)
from app.services.enc_auth_limits_eval import SCOPES, blocked_scope

_EXPECTED_COLUMNS = {
    "enc_auth_nonces": {"nonce_hash", "op", "issued_at", "expires_at", "consumed_at"},
    "enc_auth_limits": {"scope", "key_hash", "window_start", "hits"},
    "enc_auth_proof_claims": {"proof_hash", "uid", "created_at"},
}


class SchemaMismatch(Exception):
    """A table with one of our names exists with a different shape (a name collision). The gateway then fails closed."""


class StoreUnavailable(Exception):
    """The database could not be used. `cause` is one of the fixed codes in enc_auth_diag.CAUSES; no value is ever attached."""

    def __init__(self, cause="db_unavailable"):
        super().__init__(cause)
        self.cause = cause


@contextlib.asynccontextmanager
async def connection():
    """A pooled connection with a bounded wait, translating database trouble into StoreUnavailable(cause)."""
    cm = None
    try:
        pool = await get_pool()
        cm = pool.acquire(timeout=float(settings.ENC_AUTH_DB_ACQUIRE_TIMEOUT_SECONDS))
        conn = await cm.__aenter__()
    except asyncio.TimeoutError:
        raise StoreUnavailable("pool") from None
    except Exception:
        raise StoreUnavailable("db_unavailable") from None
    try:
        yield conn
    except asyncio.TimeoutError:
        raise StoreUnavailable("db_timeout") from None
    except (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, OSError):
        raise StoreUnavailable("db_unavailable") from None
    finally:
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)


async def ensure_schema(conn):
    """Additive only. Idempotent. Verifies the shape afterwards, because CREATE TABLE IF NOT EXISTS silently does nothing
    when a table of that name already exists."""
    if not settings.ENC_AUTH_ENABLED:
        return                  # gateway disabled (the default): NO gateway-specific DDL - no tables, no indexes, not even the shape queries
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS enc_auth_nonces (
            nonce_hash TEXT PRIMARY KEY,
            op TEXT NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NULL
        )
    ''')
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_enc_nonce_expires ON enc_auth_nonces (expires_at)")
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS enc_auth_limits (
            scope TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            window_start TIMESTAMPTZ NOT NULL,
            hits INT NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, key_hash, window_start)
        )
    ''')
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_enc_limits_window ON enc_auth_limits (window_start)")
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS enc_auth_proof_claims (
            proof_hash TEXT PRIMARY KEY,
            uid TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    ''')
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_enc_claims_created ON enc_auth_proof_claims (created_at)")
    for table, expected in _EXPECTED_COLUMNS.items():
        rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = $1", table)
        if {r["column_name"] for r in rows} != expected:
            raise SchemaMismatch(table)


def _nonce_hash(nonce):
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


class PgNonceStore:
    """Server-issued, operation-bound, short-lived, single-use nonces."""

    def __init__(self, ttl_seconds, max_outstanding):
        self.ttl = int(ttl_seconds)
        self.max_outstanding = int(max_outstanding)

    def now(self):
        return int(time.time())

    async def issue(self, operation):
        if operation not in OPERATIONS:
            raise EnvelopeError(WRONG_OPERATION)
        nonce = b64e(secrets.token_bytes(32))
        async with connection() as conn:
            outstanding = await conn.fetchval(
                "SELECT count(*) FROM enc_auth_nonces WHERE consumed_at IS NULL AND expires_at > NOW()")
            if outstanding >= self.max_outstanding:
                raise EnvelopeError(CAPACITY)
            await conn.execute(
                "INSERT INTO enc_auth_nonces (nonce_hash, op, expires_at) VALUES ($1, $2, NOW() + make_interval(secs => $3))",
                _nonce_hash(nonce), operation, float(self.ttl))
            if secrets.randbelow(50) == 0:  # opportunistic purge of long-dead rows
                await conn.execute("DELETE FROM enc_auth_nonces WHERE expires_at < NOW() - INTERVAL '1 hour'")
        return nonce

    @staticmethod
    def _verdict(row, operation):
        if row is None:
            return UNKNOWN_NONCE
        if row["op"] != operation:
            return WRONG_OPERATION
        if row["consumed_at"] is not None:
            return REPLAY
        if not row["live"]:
            return EXPIRED
        return None

    async def inspect(self, nonce, operation):
        """Read-only. Raises EnvelopeError unless the nonce is unused, unexpired and issued for this operation."""
        async with connection() as conn:
            row = await conn.fetchrow(
                "SELECT op, consumed_at, expires_at > NOW() AS live FROM enc_auth_nonces WHERE nonce_hash = $1",
                _nonce_hash(nonce))
        verdict = self._verdict(row, operation)
        if verdict:
            raise EnvelopeError(verdict)

    async def consume(self, nonce, operation):
        """Atomic single use: exactly one caller can flip consumed_at; every other caller gets REPLAY (or EXPIRED/UNKNOWN)."""
        async with connection() as conn:
            won = await conn.fetchval(
                "UPDATE enc_auth_nonces SET consumed_at = NOW() "
                "WHERE nonce_hash = $1 AND op = $2 AND consumed_at IS NULL AND expires_at > NOW() RETURNING 1",
                _nonce_hash(nonce), operation)
            if won:
                return
            row = await conn.fetchrow(
                "SELECT op, consumed_at, expires_at > NOW() AS live FROM enc_auth_nonces WHERE nonce_hash = $1",
                _nonce_hash(nonce))
        raise EnvelopeError(self._verdict(row, operation) or REPLAY)


# ---------------------------------------------------------------------------------------------------------------------
# Gateway rate limiter (fixed windows). Own table, own secret; the OTP limiter is neither used nor changed.
# ---------------------------------------------------------------------------------------------------------------------

def _key_hash(scope, value):
    secret = settings.ENC_AUTH_HASH_SECRET.encode("utf-8")
    return hmac.new(secret, f"{scope}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


_BUMP = '''
    INSERT INTO enc_auth_limits (scope, key_hash, window_start, hits)
    VALUES ($1, $2, to_timestamp(floor(extract(epoch FROM NOW()) / $3::double precision) * $3::double precision), 1)
    ON CONFLICT (scope, key_hash, window_start) DO UPDATE SET hits = enc_auth_limits.hits + 1
    RETURNING hits, window_start
'''
_RETRY_AFTER = "SELECT GREATEST(1, CEIL(EXTRACT(EPOCH FROM ($1::timestamptz + make_interval(secs => $2) - NOW()))))::int"
_RELEASE = "UPDATE enc_auth_limits SET hits = GREATEST(hits - 1, 0) WHERE scope = $1 AND key_hash = $2 AND window_start = $3"


async def hit(scope, value, limit, window_seconds=None):
    """Counts one event and returns (allowed, retry_after_seconds). Used for plain request limits (requests and signups per IP)."""
    window = float(window_seconds or settings.ENC_AUTH_WINDOW_SECONDS)
    async with connection() as conn:
        row = await conn.fetchrow(_BUMP, scope, _key_hash(scope, value), window)
        retry = await conn.fetchval(_RETRY_AFTER, row["window_start"], window)
        if secrets.randbelow(100) == 0:
            await conn.execute("DELETE FROM enc_auth_limits WHERE window_start < NOW() - INTERVAL '1 hour'")
    return row["hits"] <= limit, retry


class Attempt:
    """A counted password-verification attempt. Give it back with release_password_attempt() unless it was a real wrong password."""
    __slots__ = ("rows", "blocked", "retry_after")

    def __init__(self, rows, blocked=None, retry_after=0):
        self.rows, self.blocked, self.retry_after = rows, blocked, retry_after


def _limits():
    return {"pair": settings.ENC_AUTH_PAIR_FAIL_LIMIT, "ip": settings.ENC_AUTH_IP_FAIL_LIMIT,
            "email": settings.ENC_AUTH_EMAIL_FAIL_LIMIT, "global": settings.ENC_AUTH_GLOBAL_FAIL_LIMIT}


async def reserve_password_attempt(email, ip):
    """Counts the attempt BEFORE the password is checked, in all four scopes, in one transaction (so concurrent guesses cannot
    overshoot a limit). A refused attempt is given back at once, so counters reflect real verifications only.
    The e-mail is normalised here; neither it nor the IP is stored, only HMACs."""
    email_key = (email or "").strip().lower()
    names = {"pair": "pwf_pair", "ip": "pwf_ip", "email": "pwf_email", "global": "pwf_global"}
    values = {"pair": f"{email_key}|{ip}", "ip": ip, "email": email_key, "global": "*"}
    window = float(settings.ENC_AUTH_WINDOW_SECONDS)
    async with connection() as conn:
        async with conn.transaction():
            hits, rows = {}, []
            for scope in SCOPES:
                key = _key_hash(names[scope], values[scope])
                row = await conn.fetchrow(_BUMP, names[scope], key, window)
                hits[scope] = row["hits"]
                rows.append((scope, names[scope], key, row["window_start"]))
            blocked = blocked_scope(hits, _limits(), settings.ENC_AUTH_ABUSE_IP_FAIL_LIMIT)
            if blocked is None:
                return Attempt([(n, k, w) for _s, n, k, w in rows])
            for _s, name, key, start in rows:                      # refused: give every increment back
                await conn.execute(_RELEASE, name, key, start)
            scope = {"abuse_ip": "ip"}.get(blocked, blocked)
            start = next(w for s, _n, _k, w in rows if s == scope)
            retry = await conn.fetchval(_RETRY_AFTER, start, window)
            return Attempt([], blocked=blocked, retry_after=retry)


async def release_password_attempt(attempt):
    """Gives a counted attempt back (the password was right, or the check could not be completed: that is not a failure)."""
    if not attempt.rows:
        return
    async with connection() as conn:
        async with conn.transaction():
            for name, key, start in attempt.rows:
                await conn.execute(_RELEASE, name, key, start)


async def record_failure(email, ip):
    """Counts a failure that never reached a password check (for example an e-mail or UID mismatch on re-authentication)."""
    attempt = await reserve_password_attempt(email, ip)
    return attempt.blocked is None


# ---------------------------------------------------------------------------------------------------------------------
# Proof claims (Option D): the first VERIFIED Firebase UID that used a proof at the gateway.
# ---------------------------------------------------------------------------------------------------------------------

def claim_key(raw_proof):
    return _key_hash("proof_claim", raw_proof)


async def bind_claim(raw_proof, uid):
    """Atomically records `uid` as the owner of the proof unless another UID already is. True if `uid` is (now) the owner.
    Call it only AFTER the identity service confirmed the account (created it, or verified the password of the existing one)."""
    key = claim_key(raw_proof)
    async with connection() as conn:
        await conn.execute("INSERT INTO enc_auth_proof_claims (proof_hash, uid) VALUES ($1, $2) ON CONFLICT (proof_hash) DO NOTHING", key, uid)
        owner = await conn.fetchval("SELECT uid FROM enc_auth_proof_claims WHERE proof_hash = $1", key)
        if secrets.randbelow(100) == 0:
            await conn.execute("DELETE FROM enc_auth_proof_claims WHERE created_at < NOW() - INTERVAL '2 days'")
    return owner == uid


async def claim_owner(raw_proof):
    async with connection() as conn:
        return await conn.fetchval("SELECT uid FROM enc_auth_proof_claims WHERE proof_hash = $1", claim_key(raw_proof))


async def claim_allows(uid, raw_proof):
    """For /auth/mark-verified. True: go on to the unchanged consume step. False: the proof was claimed by a different UID.
    None: the claim could not be checked (answer 'unavailable'). With the gateway disabled this is always True and touches nothing."""
    if not settings.ENC_AUTH_ENABLED:
        return True
    if not isinstance(raw_proof, str) or not raw_proof or len(raw_proof) > 128:
        return True                                  # not a usable proof: the unchanged consume step rejects it
    if not settings.ENC_AUTH_HASH_SECRET:
        return None
    try:
        owner = await claim_owner(raw_proof)
    except StoreUnavailable:
        return None
    return owner is None or owner == uid
