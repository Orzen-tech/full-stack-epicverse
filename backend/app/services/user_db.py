from pydantic import BaseModel
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
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_otps (
                identifier TEXT PRIMARY KEY,
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
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


async def save_user(user: UserRecord):
    uid = user.get_uid()
    if not uid:
        raise ValueError("UserRecord must have firebase_id or uid")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO users (uid, display_name, email, primary_language, profile_picture)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (uid)
            DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                email = COALESCE(EXCLUDED.email, users.email),
                primary_language = COALESCE(EXCLUDED.primary_language, users.primary_language),
                profile_picture = COALESCE(EXCLUDED.profile_picture, users.profile_picture)
        ''', uid, user.display_name, user.email, user.primary_language, user.profile_picture)
    return True


async def get_user(firebase_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Auto-cancel a pending deletion: if the user returns within the
        # 30-day grace window, clear `deletion_requested_at` so the scheduled
        # purge will skip them. This is the "sign back in → account restored"
        # behaviour surfaced in the Settings UI.
        row = await conn.fetchrow('SELECT * FROM users WHERE uid = $1', firebase_id)
        if row and row.get('deletion_requested_at') is not None:
            await conn.execute(
                'UPDATE users SET deletion_requested_at = NULL WHERE uid = $1',
                firebase_id,
            )
            print(f"[USER_DB] Auto-cancelled pending deletion for uid={firebase_id}", flush=True)
            row = await conn.fetchrow('SELECT * FROM users WHERE uid = $1', firebase_id)
        if row:
            return dict(row)
    return None


async def request_user_deletion(uid: str) -> bool:
    """Marks a user as pending deletion. Actual purge happens 30 days later
    via `purge_expired_deletions()` unless the user signs in and triggers
    auto-cancel in `get_user()` first.
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
                INSERT INTO user_otps (identifier, otp, created_at)
                VALUES ($1, $2, CURRENT_TIMESTAMP)
                ON CONFLICT (identifier) DO UPDATE SET
                    otp = EXCLUDED.otp,
                    created_at = CURRENT_TIMESTAMP
            ''', identifier.lower(), otp)
            return True
    except Exception as e:
        print(f"[DB] OTP Save Error: {e}")
        return False


async def verify_otp(identifier: str, otp: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM user_otps
                WHERE identifier = $1 AND otp = $2
                AND created_at > (NOW() - INTERVAL '1 minute')
            ''', identifier.lower(), otp)
            if row:
                await conn.execute('DELETE FROM user_otps WHERE identifier = $1', identifier.lower())
                return True
            return False
    except Exception as e:
        print(f"OTP Verification Error: {e}")
        return False


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


async def mark_invite_code_used(code: str, uid: str) -> bool:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE invite_codes SET current_uses = current_uses + 1 WHERE UPPER(code) = UPPER($1)",
                code,
            )
            return True
    except Exception as e:
        print(f"[DB] mark_invite_code_used error: {e}")
        return False


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
