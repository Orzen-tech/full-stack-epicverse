import hashlib
import hmac

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


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                display_name TEXT,
                email TEXT,
                primary_language TEXT,
                profile_picture TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_picture TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS session_id TEXT")
        # Soft-delete support: a non-null timestamp means the account is
        # scheduled for permanent deletion 30 days later. A subsequent
        # authenticated sign-in auto-clears this column (grace period).
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMP NULL")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_code TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_otps (
                identifier TEXT PRIMARY KEY,
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                attempts INT NOT NULL DEFAULT 0
            )
        ''')
        await conn.execute("ALTER TABLE user_otps ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0")
        # Schema must match the columns read by validate_invite_code() and
        # mark_invite_code_used() below. Production rows already have these
        # columns; this DDL only fires on a fresh DB (e.g. staging / DR).
        await conn.execute('''
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
        await conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS current_uses INT NOT NULL DEFAULT 0")
        await conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS max_uses INT NOT NULL DEFAULT 1")
        await conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NULL")
        await conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        await conn.execute('''
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
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS otp_send_rate_limits (
                key_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                request_count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (key_hash, scope, window_start)
            )
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_otp_send_rate_limits_window_start
            ON otp_send_rate_limits (window_start)
        ''')

        # F-09 Phase 1: additive MFA schema only — no runtime caller yet.
        # Self-contained challenge storage (see backend/app/services/user_db.py
        # docs elsewhere): deliberately NOT stored in user_otps, since that
        # table is shared/overwritten across OTP purposes by identifier alone.
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS mfa_login_challenges (
                challenge_id TEXT PRIMARY KEY,
                uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                identifier TEXT NOT NULL,
                purpose TEXT NOT NULL
                    CHECK (purpose IN ('login_mfa', 'enable_mfa')),
                otp_hash TEXT NOT NULL,
                attempts INT NOT NULL DEFAULT 0
                    CHECK (attempts >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL
            )
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mfa_challenges_uid
            ON mfa_login_challenges (uid)
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mfa_challenges_expires
            ON mfa_login_challenges (expires_at)
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS mfa_sessions (
                session_hash TEXT PRIMARY KEY,
                uid TEXT NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                auth_time TIMESTAMPTZ NULL,
                revoked_at TIMESTAMPTZ NULL
            )
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_uid
            ON mfa_sessions (uid)
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mfa_sessions_expires
            ON mfa_sessions (expires_at)
        ''')
        # F-09: single-use proof that an email passed OTP verification
        # before its profile existed (signup). Keyed by an HMAC of the
        # normalised email; never the plaintext address.
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS email_verification_proofs (
                email_hash TEXT PRIMARY KEY,
                verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                consumed_at TIMESTAMPTZ NULL
            )
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

    `verified_email` is the email from the verified Firebase ID token. When
    given, a newly inserted row always stores it, and a different
    client-supplied email is rejected — decided under the same lock as the
    existence check, so no concurrent delete can slip a client email in.

    Returns {"status": "existing"} | {"status": "created"} |
    {"status": "rejected", "reason": "invite_required" | "invalid_invite"
     | "expired_invite" | "exhausted_invite" | "email_mismatch"}.
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

    new_email = user.email
    if verified_email:
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


async def save_otp(identifier: str, otp: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_otps (identifier, otp, created_at, attempts)
                VALUES ($1, $2, CURRENT_TIMESTAMP, 0)
                ON CONFLICT (identifier) DO UPDATE SET
                    otp = EXCLUDED.otp,
                    created_at = CURRENT_TIMESTAMP,
                    attempts = 0
            ''', identifier.lower(), otp)
            return True
    except Exception as e:
        print(f"[DB] OTP Save Error: {e}")
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

                if row['otp'] == otp:
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
        print(f"OTP Verification Error: {e}")
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



