import contextlib
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

import asyncpg
from pydantic import BaseModel
from app.core.config import settings
from app.services.db_pool import get_pool


class UserRecord(BaseModel):
    firebase_id: str | None = None
    uid: str | None = None
    display_name: str | None = None
    email: str | None = None
    primary_language: str | None = "English"
    profile_picture: str | None = None
    invite_code: str | None = None
    session_id: str | None = None
    mfa_enabled: bool | None = None

    class Config:
        extra = "ignore"

    def get_uid(self) -> str | None:
        return self.firebase_id or self.uid


# ---------------------------------------------------------------------------
# Startup schema work must never queue live traffic.
#
# Even a no-op `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an
# ACCESS EXCLUSIVE lock, and `CREATE INDEX IF NOT EXISTS` a SHARE lock. If a
# long transaction already holds a conflicting lock, the DDL waits, and every
# ordinary query on that table queues behind the waiting DDL (up to the pool's
# 15 s command_timeout). Startup therefore gives up quickly instead: each
# startup statement runs in its OWN short transaction with a transaction-local
# lock_timeout. `SET LOCAL` is discarded automatically at COMMIT or ROLLBACK, so
# it can never remain on the pooled connection or reach a runtime query, and
# every statement still holds its locks exactly as long as it did in autocommit.
# The pool's command_timeout is deliberately left unchanged.
# ---------------------------------------------------------------------------
INIT_DB_LOCK_TIMEOUT_MS = 750


class SchemaLockTimeout(Exception):
    """init_db gave up because a schema lock was not granted within
    INIT_DB_LOCK_TIMEOUT_MS. Nothing was half-applied: the statement's
    transaction was rolled back, and all startup schema work is idempotent, so
    the next start simply completes it."""


@contextlib.asynccontextmanager
async def _startup_schema_txn(conn, label: str):
    """One explicit transaction with a transaction-local lock_timeout. A lock
    that cannot be obtained in time rolls everything in the block back and
    raises SchemaLockTimeout; the message and log contain only the statement
    text prefix (no credentials, hosts or data)."""
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL lock_timeout = {int(INIT_DB_LOCK_TIMEOUT_MS)}")
            yield
    except asyncpg.exceptions.LockNotAvailableError:
        print(f"[DB] init_db gave up: a schema lock was not available within "
              f"{INIT_DB_LOCK_TIMEOUT_MS} ms ({label}). Live traffic was not held longer than "
              f"that; startup schema work is idempotent and completes on the next start.", flush=True)
        raise SchemaLockTimeout(
            f"schema lock not available within {INIT_DB_LOCK_TIMEOUT_MS} ms ({label})") from None


async def _ddl(conn, sql: str) -> None:
    """Runs ONE startup schema statement under _startup_schema_txn."""
    async with _startup_schema_txn(conn, " ".join(sql.split())[:70]):
        await conn.execute(sql)


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                display_name TEXT,
                email TEXT,
                primary_language TEXT,
                profile_picture TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_picture TEXT")
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_id TEXT")
        # Soft-delete support: a non-null timestamp means the account is
        # scheduled for permanent deletion 30 days later. A subsequent
        # authenticated sign-in auto-clears this column (grace period).
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMP NULL")
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_code TEXT")
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE")
        await _ddl(conn, "ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS user_otps (
                identifier TEXT PRIMARY KEY,
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attempts INT NOT NULL DEFAULT 0
            )
        ''')
        await _ddl(conn, "ALTER TABLE user_otps ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0")
        # Schema must match the columns read by validate_invite_code() and
        # mark_invite_code_used() below. Production rows already have these
        # columns; this DDL only fires on a fresh DB (e.g. staging / DR).
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                current_uses INT NOT NULL DEFAULT 0,
                max_uses INT NOT NULL DEFAULT 1,
                expires_at TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Belt-and-braces: if an older deploy left the legacy columns, make
        # sure the new ones exist too. IF NOT EXISTS makes these idempotent.
        await _ddl(conn, "ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS current_uses INT NOT NULL DEFAULT 0")
        await _ddl(conn, "ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS max_uses INT NOT NULL DEFAULT 1")
        await _ddl(conn, "ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NULL")
        await _ddl(conn, "ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS user_feedback (
                id SERIAL PRIMARY KEY,
                uid TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # F-07: OTP send-rate-limit counters (PostgreSQL replacement for
        # the previous Redis-backed limiter). key_hash is an HMAC-SHA256
        # hash of the identifier or client IP — never the raw value.
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS otp_send_rate_limits (
                key_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                request_count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (key_hash, scope, window_start)
            )
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_otp_send_rate_limits_window_start
            ON otp_send_rate_limits (window_start)
        ''')

        # F-09 Phase 1: additive MFA schema only — no runtime caller yet.
        # Self-contained challenge storage (see backend/app/services/user_db.py
        # docs elsewhere): deliberately NOT stored in user_otps, since that
        # table is shared/overwritten across OTP purposes by identifier alone.
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS mfa_login_challenges (
                challenge_id TEXT PRIMARY KEY,
                uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                identifier TEXT NOT NULL,
                purpose TEXT NOT NULL
                    CHECK (purpose IN ('login_mfa', 'enable_mfa', 'verify_email')),
                otp_hash TEXT NOT NULL,
                attempts INT NOT NULL DEFAULT 0
                    CHECK (attempts >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL
            )
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_mfa_challenges_uid
            ON mfa_login_challenges (uid)
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expires
            ON mfa_login_challenges (expires_at)
        ''')

        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS mfa_sessions (
                session_hash TEXT PRIMARY KEY,
                uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                auth_time TIMESTAMPTZ NULL,
                revoked_at TIMESTAMPTZ NULL
            )
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_uid
            ON mfa_sessions (uid)
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_expires
            ON mfa_sessions (expires_at)
        ''')
        # F-09: single-use proof that an email passed OTP verification
        # before its profile existed (signup). Keyed by an HMAC of the
        # normalised email; never the plaintext address.
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS email_verification_proofs (
                email_hash TEXT PRIMARY KEY,
                verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL
            )
        ''')

        # F-09 H1 (additive, idempotent): databases created before
        # 'verify_email' existed carry a CHECK that only allows the two MFA
        # purposes. The constraint is found in the catalog (its name is
        # auto-generated), replaced only if it lacks 'verify_email', and the
        # whole step runs in ONE transaction under an advisory lock, with the
        # same transaction-local lock_timeout as every other startup statement, so
        # concurrently starting instances cannot race each other and a lock that
        # cannot be had in time rolls the whole swap back (the constraint is never
        # left missing or half-changed). Older backend code never writes
        # 'verify_email', so it tolerates the wider constraint.
        async with _startup_schema_txn(conn, "mfa_login_challenges purpose constraint"):
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('f09_h1_challenge_purpose'))")
            await conn.execute('''
                DO $$
                DECLARE
                    r RECORD;
                    has_wide BOOLEAN := FALSE;
                BEGIN
                    FOR r IN
                        SELECT conname, pg_get_constraintdef(oid) AS def
                        FROM pg_constraint
                        WHERE conrelid = 'mfa_login_challenges'::regclass
                          AND contype = 'c'
                          AND pg_get_constraintdef(oid) ILIKE '%purpose%'
                    LOOP
                        IF r.def LIKE '%verify_email%' THEN
                            has_wide := TRUE;
                        ELSE
                            EXECUTE format('ALTER TABLE mfa_login_challenges DROP CONSTRAINT %I', r.conname);
                        END IF;
                    END LOOP;
                    IF NOT has_wide THEN
                        ALTER TABLE mfa_login_challenges
                            ADD CONSTRAINT mfa_login_challenges_purpose_check
                            CHECK (purpose IN ('login_mfa', 'enable_mfa', 'verify_email'));
                    END IF;
                END $$;
            ''')

        # F-09 H1: single-use proof of email ownership issued by
        # /auth/verify-otp for signup. The raw proof ("evp1_...") is returned
        # to the client once and never stored; only HMACs are. It replaces
        # the email-only v1 table above, which is kept until the legacy
        # rollout window closes.
        await _ddl(conn, '''
            CREATE TABLE IF NOT EXISTS email_verification_proofs_v2 (
                proof_hash TEXT PRIMARY KEY,
                email_hash TEXT NOT NULL,
                purpose TEXT NOT NULL
                    CHECK (purpose = 'signup_email_verification'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL,
                consumed_by_uid TEXT NULL,
                CHECK (expires_at > created_at),
                CHECK (consumed_by_uid IS NULL OR consumed_at IS NOT NULL)
            )
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_evp2_email_live
            ON email_verification_proofs_v2 (email_hash)
            WHERE consumed_at IS NULL
        ''')
        await _ddl(conn, '''
            CREATE INDEX IF NOT EXISTS idx_evp2_expires
            ON email_verification_proofs_v2 (expires_at)
        ''')


async def get_security_state(uid: str) -> dict | None:
    """Only the server-side security flags for this UID (no profile data).
    Errors propagate so callers fail closed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT mfa_enabled, email_verified FROM users WHERE uid = $1", uid)
    return dict(row) if row else None


class _InviteConsumptionRace(Exception):
    """Internal-only signal: the guarded invite-consumption UPDATE
    returned no row. Caught and translated to a safe, generic rejection —
    never allowed to surface database details to the client."""


async def sync_user_with_invite_check(user: UserRecord, verified_email: str | None = None) -> dict:
    """F-06: the sole path that may create a new `users` row.

    Existing users: a normal profile sync — no invite required, none
    consumed, regardless of what `invite_code` (if any) is in the request.

    Genuinely new users: a valid, unexpired, unexhausted `invite_code` is
    mandatory. Validation, row creation, and the invite's `current_uses`
    increment all happen inside one transaction, so a rejected invite can
    never leave a partially-created user, and a race can never consume
    more uses than `max_uses` allows.

    Concurrency:
      - Same brand-new uid (two concurrent requests, e.g. a real signup and
        the no-invite login fallback racing each other): serialized with a
        transaction-scoped Postgres advisory lock
        (`pg_advisory_xact_lock(hashtext(uid))`), taken *before* the
        `users` row is even looked up. A plain `SELECT ... FOR UPDATE`
        cannot serialize this case, since no row exists yet to lock for a
        genuinely new uid.
      - Same invite code, different uids: serialized with
        `SELECT ... FROM invite_codes ... FOR UPDATE` on that specific
        code row, so `current_uses` is read-then-incremented atomically.

    `verified_email` is the email from the verified Firebase ID token and is
    the ONLY authority for a new row's email: it is always the stored value,
    a different client-supplied email is rejected, and a token with no email
    cannot create an account at all ("email_required") — decided under the
    same lock as the existence check and before the invite is touched.

    Returns {"status": "existing"} | {"status": "created"} |
    {"status": "rejected", "reason": "email_required" | "invite_required"
     | "invalid_invite" | "expired_invite" | "exhausted_invite"
     | "email_mismatch"}.
    """
    uid = user.get_uid()
    if not uid:
        raise ValueError("UserRecord must have firebase_id or uid")

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                return await _sync_user_txn(conn, uid, user, verified_email)
        except _InviteConsumptionRace:
            # The transaction already rolled back (including the INSERT)
            # because this was raised inside conn.transaction(). Report it
            # the same way an already-exhausted invite is reported — no
            # database details reach the caller.
            return {"status": "rejected", "reason": "exhausted_invite"}


async def _sync_user_txn(conn, uid: str, user: UserRecord, verified_email: str | None) -> dict:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", uid)

    existing = await conn.fetchrow("SELECT uid FROM users WHERE uid = $1", uid)

    if existing:
        # F-09: email and mfa_enabled are server-controlled. Profile sync
        # may only change display fields; MFA codes are sent to the stored
        # email, so letting a client rewrite it would bypass MFA.
        await conn.execute('''
            UPDATE users SET
                display_name = COALESCE($2, display_name),
                primary_language = COALESCE($3, primary_language),
                profile_picture = COALESCE($4, profile_picture)
            WHERE uid = $1
        ''', uid, user.display_name, user.primary_language, user.profile_picture)
        return {"status": "existing"}

    # The verified token is the only authority for a new account's email. A
    # token without one fails closed here, before the invite is looked up.
    if not verified_email:
        return {"status": "rejected", "reason": "email_required"}
    if user.email and user.email.strip().lower() != verified_email.strip().lower():
        return {"status": "rejected", "reason": "email_mismatch"}
    new_email = verified_email

    if not user.invite_code:
        return {"status": "rejected", "reason": "invite_required"}

    code = user.invite_code.strip().upper()
    invite_row = await conn.fetchrow(
        """SELECT current_uses, max_uses,
                  (expires_at IS NOT NULL AND expires_at <= NOW()) AS is_expired
           FROM invite_codes
           WHERE UPPER(code) = UPPER($1)
           FOR UPDATE""",
        code,
    )
    if invite_row is None:
        return {"status": "rejected", "reason": "invalid_invite"}
    if invite_row["is_expired"]:
        return {"status": "rejected", "reason": "expired_invite"}
    if invite_row["current_uses"] >= invite_row["max_uses"]:
        return {"status": "rejected", "reason": "exhausted_invite"}

    await conn.execute('''
        INSERT INTO users (uid, display_name, email, primary_language, profile_picture, invite_code, mfa_enabled)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
    ''', uid, user.display_name, new_email, user.primary_language, user.profile_picture,
         code, False)

    # Defense-in-depth: the SELECT ... FOR UPDATE above is the primary
    # concurrency control and should make this condition unreachable in
    # practice. This guard additionally makes the increment itself
    # self-verifying at the SQL level — if it ever returns no row (e.g. a
    # future code change weakens/removes the row lock above), the invite
    # was not actually consumed, so the transaction must not commit a new
    # user against it. Raising here rolls back the INSERT too, since both
    # run inside the same `conn.transaction()` block in the caller.
    consumed = await conn.fetchrow(
        """UPDATE invite_codes
           SET current_uses = current_uses + 1
           WHERE UPPER(code) = UPPER($1) AND current_uses < max_uses
           RETURNING current_uses""",
        code,
    )
    if consumed is None:
        raise _InviteConsumptionRace()
    return {"status": "created"}


async def get_user(firebase_id: str):
    """Pure read — no state mutation. Pending-deletion cancellation is
    handled exclusively by the authenticated `POST
    /user/{firebase_id}/cancel-deletion` route (see `cancel_user_deletion`)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM users WHERE uid = $1', firebase_id)
        if row:
            return dict(row)
    return None


async def request_user_deletion(uid: str) -> bool:
    """Marks a user as pending deletion. Actual purge happens 30 days later
    via `purge_expired_deletions()` unless `cancel_user_deletion()` is
    called first (F-05: via the authenticated `POST
    /user/{firebase_id}/cancel-deletion` route — `get_user()` is a pure
    read and performs no cancellation itself).
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                'UPDATE users SET deletion_requested_at = NOW() WHERE uid = $1',
                uid,
            )
            print(f"[USER_DB] Deletion requested uid={uid} result={result}", flush=True)
            return True
    except Exception as e:
        print(f"[USER_DB] request_user_deletion error: {e}")
        return False


async def get_all_feedback() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT f.id, f.uid, u.display_name, u.email, f.message, f.created_at
            FROM user_feedback f
            LEFT JOIN users u ON u.uid = f.uid
            ORDER BY f.created_at DESC
        ''')
        return [dict(r) for r in rows]


async def get_dashboard_data() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_rows = await conn.fetch('''
            SELECT display_name, email, invite_code, created_at
            FROM users
            WHERE deletion_requested_at IS NULL
            ORDER BY created_at DESC
        ''')
        feedback_rows = await conn.fetch('''
            SELECT f.message, f.created_at, u.display_name, u.email
            FROM user_feedback f
            LEFT JOIN users u ON u.uid = f.uid
            ORDER BY f.created_at DESC
        ''')
        deletion_rows = await conn.fetch('''
            SELECT display_name, email, deletion_requested_at
            FROM users
            WHERE deletion_requested_at IS NOT NULL
            ORDER BY deletion_requested_at DESC
        ''')
    users = [dict(r) for r in user_rows]
    feedback = [dict(r) for r in feedback_rows]
    deletions = [dict(r) for r in deletion_rows]
    return {
        "total_users": len(users),
        "total_feedback": len(feedback),
        "total_deletions": len(deletions),
        "users": users,
        "feedback": feedback,
        "deletions": deletions,
    }


async def save_feedback(uid: str, message: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO user_feedback (uid, message) VALUES ($1, $2)',
                uid, message
            )
        return True
    except Exception as e:
        print(f"[USER_DB] save_feedback error: {e}")
        return False


async def cancel_user_deletion(uid: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                'UPDATE users SET deletion_requested_at = NULL WHERE uid = $1',
                uid,
            )
            return True
    except Exception as e:
        print(f"[USER_DB] cancel_user_deletion error: {e}")
        return False


async def purge_expired_deletions() -> list[str]:
    """Hard-deletes users whose `deletion_requested_at` is older than 30 days.
    Returns the list of uids that were purged (caller is responsible for
    deleting them from Firebase Auth).
    """
    purged: list[str] = []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT uid FROM users WHERE deletion_requested_at IS NOT NULL "
                "AND deletion_requested_at < NOW() - INTERVAL '30 days'"
            )
            for r in rows:
                uid = r['uid']
                try:
                    await conn.execute('DELETE FROM chat_history WHERE uid = $1', uid)
                except Exception:
                    pass
                await conn.execute('DELETE FROM users WHERE uid = $1', uid)
                purged.append(uid)
        if purged:
            print(f"[USER_DB] Purged {len(purged)} expired accounts: {purged}", flush=True)
    except Exception as e:
        print(f"[USER_DB] purge_expired_deletions error: {e}")
    return purged


_OTP_AT_REST_PURPOSE = b"epicverse/otp-at-rest/v1"
_LEGACY_PLAINTEXT_OTP = re.compile(r'[0-9]{6}')
_HASHED_OTP = re.compile(r'[0-9a-f]{64}')


def _otp_at_rest_hash(identifier: str, otp: str) -> str:
    """HMAC-SHA256 of the OTP bound to its identifier, under a subkey derived
    for this purpose only. Raises if the secret is missing or too short, so
    nothing is ever stored or accepted without it."""
    secret = settings.MFA_OTP_HASH_SECRET or ""
    if len(secret) < 16:
        raise RuntimeError("OTP hash secret unavailable")
    key = hmac.new(secret.encode('utf-8'), _OTP_AT_REST_PURPOSE, hashlib.sha256).digest()
    ident = identifier.encode('utf-8')
    msg = len(ident).to_bytes(4, 'big') + ident + otp.encode('utf-8')
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _otp_matches(stored: str, identifier: str, otp: str) -> bool:
    expected = _otp_at_rest_hash(identifier, otp)
    if _HASHED_OTP.fullmatch(stored):
        return hmac.compare_digest(stored.encode('utf-8'), expected.encode('utf-8'))
    # TRANSITIONAL: rows written by the previous release hold the 6-digit code
    # in plaintext. They expire after 1 minute and are swept after 1 hour; this
    # branch can be removed once the old release is fully drained. New rows are
    # never written in plaintext.
    if _LEGACY_PLAINTEXT_OTP.fullmatch(stored):
        return hmac.compare_digest(stored.encode('utf-8'), otp.encode('utf-8'))
    return False


async def save_otp(identifier: str, otp: str) -> bool:
    try:
        stored = _otp_at_rest_hash(identifier.lower(), otp)
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM user_otps WHERE created_at < NOW() - INTERVAL '1 hour'"
                )
                await conn.execute('''
                    INSERT INTO user_otps (identifier, otp, created_at, attempts)
                    VALUES ($1, $2, CURRENT_TIMESTAMP, 0)
                    ON CONFLICT (identifier) DO UPDATE SET
                        otp = EXCLUDED.otp,
                        created_at = CURRENT_TIMESTAMP,
                        attempts = 0
                ''', identifier.lower(), stored)
            return True
    except Exception as e:
        print(f"[DB] OTP Save Error: {type(e).__name__}")
        return False


MAX_OTP_ATTEMPTS = 5


async def verify_otp(identifier: str, otp: str) -> str:
    """Verifies an OTP with concurrency-safe attempt tracking.

    Returns 'success', 'invalid', or 'too_many_attempts'. The whole
    check-then-act sequence runs inside one transaction with
    `SELECT ... FOR UPDATE`, which row-locks this identifier for the
    transaction's duration — a concurrent request for the same identifier
    blocks at its own SELECT until this one commits, so two simultaneous
    guesses can never both observe the same pre-increment attempt count.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow('''
                    SELECT otp, attempts FROM user_otps
                    WHERE identifier = $1 AND created_at > (NOW() - INTERVAL '1 minute')
                    FOR UPDATE
                ''', identifier.lower())

                if not row:
                    return 'invalid'

                if row['attempts'] >= MAX_OTP_ATTEMPTS:
                    # Already exhausted by a prior request — invalidate now.
                    await conn.execute('DELETE FROM user_otps WHERE identifier = $1', identifier.lower())
                    return 'too_many_attempts'

                # Constant-time comparison. The expected value is derived from
                # the submitted code (raises, so fails closed, without the secret).
                if row['otp'] is not None and _otp_matches(
                        row['otp'], identifier.lower(), otp):
                    await conn.execute('DELETE FROM user_otps WHERE identifier = $1', identifier.lower())
                    return 'success'

                new_attempts = row['attempts'] + 1
                if new_attempts >= MAX_OTP_ATTEMPTS:
                    await conn.execute('DELETE FROM user_otps WHERE identifier = $1', identifier.lower())
                    return 'too_many_attempts'

                await conn.execute(
                    'UPDATE user_otps SET attempts = $2 WHERE identifier = $1',
                    identifier.lower(), new_attempts
                )
                return 'invalid'
    except Exception as e:
        print(f"OTP Verification Error: {type(e).__name__}")
        return 'invalid'


async def update_session_id(uid: str, session_id: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET session_id = $1 WHERE uid = $2",
                session_id, uid
            )
            return True
    except Exception as e:
        print(f"[DB] update_session_id error: {e}")
        return False


async def get_session_id(uid: str) -> str | None:
    """Returns the currently-authoritative session_id for uid, or None.

    Used by the realtime session watchdog to detect cross-instance session
    supersession: if this value differs from the running session's own id,
    another device has taken over and the running session should self-close.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_id FROM users WHERE uid = $1", uid
            )
            return row["session_id"] if row else None
    except Exception as e:
        print(f"[DB] get_session_id error: {e}")
        return None


async def verify_session(uid: str, session_id: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT session_id FROM users WHERE uid = $1", uid
            )
            if row is None:
                return False
            stored = row.get("session_id")
            return stored is None or stored == session_id
    except Exception as e:
        print(f"[DB] verify_session error: {e}")
        return True


async def is_session_active(uid: str, session_id: str) -> bool:
    return await verify_session(uid, session_id)


async def validate_invite_code(code: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT 1 FROM invite_codes
                   WHERE UPPER(code) = UPPER($1)
                   AND current_uses < max_uses
                   AND (expires_at IS NULL OR expires_at > NOW())""",
                code,
            )
            return row is not None
    except Exception as e:
        print(f"[DB] validate_invite_code error: {e}")
        return False


async def mark_email_verified_for_uid(uid: str, email: str) -> int:
    """Marks only the profile of this exact Firebase UID as verified, and
    only if its stored email matches the verified email. Other rows sharing
    the email (e.g. orphaned profiles) are never touched. Returns rows updated."""
    if not uid or not email:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(
                "UPDATE users SET email_verified = TRUE WHERE uid = $1 AND LOWER(email) = LOWER($2)",
                uid, email,
            )
            return int(status.split()[-1])
    except Exception as e:
        print(f"[DB] mark_email_verified_for_uid error: {type(e).__name__}", flush=True)
        return 0


EMAIL_PROOF_TTL_MINUTES = 30


def _email_proof_key(email: str) -> str | None:
    secret = settings.MFA_OTP_HASH_SECRET
    if not secret or not email:
        return None
    return hmac.new(secret.encode("utf-8"), f"email-proof:{email.strip().lower()}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


async def record_email_verification_proof(email: str) -> bool:
    """Records (or refreshes) a single-use, 30-minute proof that this email
    just passed OTP verification. Used only when no profile could be marked
    yet (signup before the Firebase account / profile exists)."""
    key = _email_proof_key(email)
    if key is None:
        print("[DB] email proof not recorded: hash secret not configured", flush=True)
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO email_verification_proofs (email_hash, verified_at, expires_at, consumed_at)
                VALUES ($1, NOW(), NOW() + make_interval(mins => $2), NULL)
                ON CONFLICT (email_hash) DO UPDATE SET
                    verified_at = EXCLUDED.verified_at,
                    expires_at = EXCLUDED.expires_at,
                    consumed_at = NULL
            ''', key, EMAIL_PROOF_TTL_MINUTES)
            return True
    except Exception as e:
        print(f"[DB] record_email_verification_proof error: {type(e).__name__}", flush=True)
        return False


async def mark_email_verified_with_proof(uid: str, email: str) -> int:
    """Marks this exact UID verified only if an unexpired, unconsumed proof
    exists for the email and the profile's stored email matches. The proof
    is consumed in the same transaction. Returns rows marked (0 or 1)."""
    key = _email_proof_key(email)
    if key is None or not uid:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                proof = await conn.fetchrow('''
                    SELECT 1 FROM email_verification_proofs
                    WHERE email_hash = $1 AND consumed_at IS NULL AND expires_at > NOW()
                    FOR UPDATE
                ''', key)
                if proof is None:
                    return 0
                status = await conn.execute(
                    "UPDATE users SET email_verified = TRUE WHERE uid = $1 AND LOWER(email) = LOWER($2)",
                    uid, email,
                )
                if int(status.split()[-1]) != 1:
                    return 0
                await conn.execute(
                    "UPDATE email_verification_proofs SET consumed_at = NOW() WHERE email_hash = $1", key)
                return 1
    except Exception as e:
        print(f"[DB] mark_email_verified_with_proof error: {type(e).__name__}", flush=True)
        return 0


# ---------------------------------------------------------------------------
# F-09 H1: proof-secret email verification (signup).
#
# /auth/verify-otp issues a random one-time proof to the client that solved the
# OTP. Only HMACs of the proof and of the normalised email are stored. The
# proof is later presented to the authenticated /auth/mark-verified, which
# derives UID and email from the Firebase token alone. Nothing here is ever
# logged: no proof, hash, email or OTP.
# ---------------------------------------------------------------------------
EMAIL_PROOF_V2_PREFIX = "evp1_"
EMAIL_PROOF_V2_TTL = timedelta(minutes=15)
_EMAIL_PROOF_V2_MAX_LEN = 128


def norm_email(value) -> str:
    """The single normalisation used for OTP identifiers, proof hashes and
    token/database email comparison."""
    return value.strip().lower() if isinstance(value, str) else ""


def email_proof_available() -> bool:
    """False when the HMAC secret is missing; callers must then fail closed."""
    return bool(settings.MFA_OTP_HASH_SECRET)


def _hmac_hex(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _proof_v2_hash(secret: str, raw_proof: str) -> str:
    return _hmac_hex(secret, f"email-proof-v2:{raw_proof}")


def _proof_v2_email_hash(secret: str, email: str) -> str:
    return _hmac_hex(secret, f"email-proof-v2-email:{email}")


async def issue_email_verification_proof(email: str) -> str | None:
    """Creates a 15-minute, single-use proof for this normalised email and
    returns the RAW proof (shown to the caller exactly once). Any earlier
    unconsumed proof for the same email is superseded. Returns None when the
    HMAC secret is missing or the email is unusable (fail closed)."""
    secret = settings.MFA_OTP_HASH_SECRET
    email = norm_email(email)
    if not secret or "@" not in email:
        return None
    raw_proof = EMAIL_PROOF_V2_PREFIX + secrets.token_urlsafe(32)
    email_hash = _proof_v2_email_hash(secret, email)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", email_hash)
            await conn.execute(
                "DELETE FROM email_verification_proofs_v2 WHERE expires_at < NOW() - INTERVAL '1 day'")
            await conn.execute(
                "UPDATE email_verification_proofs_v2 SET consumed_at = NOW() "
                "WHERE email_hash = $1 AND consumed_at IS NULL", email_hash)
            await conn.execute('''
                INSERT INTO email_verification_proofs_v2
                    (proof_hash, email_hash, purpose, created_at, expires_at)
                VALUES ($1, $2, 'signup_email_verification', NOW(), NOW() + $3::interval)
            ''', _proof_v2_hash(secret, raw_proof), email_hash, EMAIL_PROOF_V2_TTL)
    return raw_proof


async def consume_email_verification_proof(uid: str, token_email: str, raw_proof: str) -> bool:
    """Atomically marks ONLY this UID verified and consumes the proof.

    `uid` and `token_email` must come from a verified Firebase ID token. The
    proof must exist, be unexpired and unconsumed, and have been issued for
    this exact email; the UID's SQL row must hold the same email. If any
    check fails nothing changes and False is returned (callers must not
    reveal which check failed). The proof row is locked, so of two concurrent
    attempts exactly one can succeed."""
    secret = settings.MFA_OTP_HASH_SECRET
    email = norm_email(token_email)
    if (not secret or not uid or "@" not in email or not isinstance(raw_proof, str)
            or not raw_proof.startswith(EMAIL_PROOF_V2_PREFIX)
            or len(raw_proof) > _EMAIL_PROOF_V2_MAX_LEN):
        return False
    proof_hash = _proof_v2_hash(secret, raw_proof)
    email_hash = _proof_v2_email_hash(secret, email)
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                proof = await conn.fetchrow('''
                    SELECT email_hash FROM email_verification_proofs_v2
                    WHERE proof_hash = $1 AND consumed_at IS NULL AND expires_at > NOW()
                    FOR UPDATE
                ''', proof_hash)
                if proof is None or not hmac.compare_digest(proof["email_hash"], email_hash):
                    return False
                status = await conn.execute(
                    "UPDATE users SET email_verified = TRUE WHERE uid = $1 AND LOWER(email) = $2",
                    uid, email)
                if int(status.split()[-1]) != 1:
                    return False
                await conn.execute(
                    "UPDATE email_verification_proofs_v2 SET consumed_at = NOW(), consumed_by_uid = $2 "
                    "WHERE proof_hash = $1", proof_hash, uid)
                return True
    except Exception as e:
        print(f"[DB] consume_email_verification_proof error: {type(e).__name__}", flush=True)
        return False


# F-06: mark_invite_code_used() was removed. It unconditionally deleted an
# invite row on any use (ignoring max_uses — it never incremented
# current_uses anywhere), which both broke multi-use codes and, combined
# with sync_user() never calling validate_invite_code(), was part of the
# invite-bypass finding. Invite validation, consumption (current_uses
# increment, never delete), and user creation are now atomic inside
# sync_user_with_invite_check() above — the sole path that may create a
# new `users` row.


async def delete_user_from_db(uid: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute('DELETE FROM chat_history WHERE uid = $1', uid)
            except Exception:
                pass
            await conn.execute('DELETE FROM users WHERE uid = $1', uid)
            return True
    except Exception as e:
        print(f"CRITICAL: Could not delete user {uid} from DB: {e}")
        return False



