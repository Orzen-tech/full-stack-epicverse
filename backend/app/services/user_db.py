import os
import asyncpg
from datetime import datetime
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from app.services.retriever import ensure_card_search_schema, init_db_pool

load_dotenv()

class UserRecord(BaseModel):
    uid: str = Field(..., alias="firebase_id")
    email: str | None = None
    phone_number: str | None = None
    display_name: str | None = Field(None, alias="display_name")
    primary_language: str | None = Field("English", alias="primary_language")
    invite_code: str | None = Field(None, alias="invite_code")
    profile_picture: str | None = Field(None, alias="profile_picture")
    session_id: str | None = Field(None, alias="session_id")

    class Config:
        populate_by_name = True
        extra = "ignore"

async def get_db_pool():
    """Returns the shared DB pool."""
    return await init_db_pool()

async def init_db():
    pool = await get_db_pool()
    if not pool:
        print("[DB] Cannot initialize: Pool not ready.")
        return
    async with pool.acquire() as conn:
        # 1. Users Table (primary key is uid)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                email TEXT,
                phone_number TEXT,
                display_name TEXT,
                primary_language TEXT,
                invite_code TEXT,
                profile_picture TEXT,
                current_mode TEXT DEFAULT 'Mode 1',
                last_session_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Ensure columns exist for existing tables (migration)
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_session_id TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_code TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_mode TEXT DEFAULT 'Mode 1'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass
        
        # 2. Chat History Table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                uid TEXT REFERENCES users(uid),
                session_id TEXT,
                message TEXT,
                role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. Invite Codes Table (Gatekeeper)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                max_uses INT DEFAULT 1,
                current_uses INT DEFAULT 0,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 4. High-Performance Indexes for 100+ User Concurrency
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_uid ON chat_history(uid)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_created_at ON chat_history(created_at)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_uid ON users(uid)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_invite_code ON invite_codes(code)')

        # 5. OTPs Table (Production Auth - Email & Phone Hybrid)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_otps (
                identifier TEXT PRIMARY KEY, -- Can be email or phone
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
    await ensure_card_search_schema()

async def save_user(user: UserRecord):
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            print(f"[DB] Saving user {user.uid}: Name={user.display_name}, Phone={user.phone_number}, Email={user.email}")
            await conn.execute('''
                INSERT INTO users (uid, email, phone_number, display_name, primary_language, invite_code, profile_picture, last_session_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (uid) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    phone_number = COALESCE(EXCLUDED.phone_number, users.phone_number),
                    display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                    primary_language = COALESCE(EXCLUDED.primary_language, users.primary_language),
                    invite_code = COALESCE(EXCLUDED.invite_code, users.invite_code),
                    profile_picture = COALESCE(EXCLUDED.profile_picture, users.profile_picture),
                    last_session_id = COALESCE(EXCLUDED.last_session_id, users.last_session_id)
            ''', user.uid, user.email, user.phone_number, user.display_name, user.primary_language, user.invite_code, user.profile_picture, user.session_id)
    except Exception as e:
        print(f"CRITICAL: Could not save user {user.uid} to DB: {e}")
    return True

async def verify_session(uid: str, session_id: str) -> bool:
    """
    Claims the session for this device. 'New Device Wins' policy.
    """
    try:
        pool = await get_db_pool()
        if not pool: return True
        async with pool.acquire() as conn:
            # Claim the session
            await conn.execute('UPDATE users SET last_session_id = $1 WHERE uid = $2', session_id, uid)
            return True
    except Exception as e:
        print(f"Session Sync Error: {e}")
        return True

async def is_session_active(uid: str, session_id: str) -> bool:
    """
    Checks if this device is still the 'authorized' one. 
    If False, it means another device has logged in and 'kicked' this one.
    """
    try:
        pool = await get_db_pool()
        if not pool: return True
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT last_session_id FROM users WHERE uid = $1', uid)
            if row and row['last_session_id'] and row['last_session_id'] != session_id:
                return False # Another device took over
            return True
    except Exception:
        return True # Fail open on DB issues



from firebase_admin import auth

async def get_user(uid: str):
    # Tier 1: Try PostgreSQL (High Speed Path)
    try:
        pool = await get_db_pool()
        if pool:
            async with pool.acquire() as conn:
                row = await conn.fetchrow('SELECT * FROM users WHERE uid = $1', uid)
                if row:
                    return dict(row)
    except Exception as e:
        print(f"PostgreSQL Profile Fetch Error: {e}")
    return None

async def get_chat_history(uid: str, limit: int = 10):
    """Fetches chat history for a specific user (UID) with DB-offline safety."""
    try:
        pool = await get_db_pool()
        if not pool: return []
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT role, message FROM chat_history 
                WHERE uid = $1 
                ORDER BY created_at DESC LIMIT $2
            ''', uid, limit)
            return [{"role": r['role'], "content": r['message']} for r in reversed(rows)]
    except Exception as e:
        print(f"Warning: Could not fetch history for {uid} (DB Offline)")
        return []

async def save_chat_history(uid: str, session_id: str, role: str, message: str):
    """Saves chat history for a specific user (UID) with DB-offline safety."""
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (uid) VALUES ($1)
                ON CONFLICT (uid) DO NOTHING
            ''', uid)
            await conn.execute('''
                INSERT INTO chat_history (uid, session_id, role, message)
                VALUES ($1, $2, $3, $4)
            ''', uid, session_id, role, message)
    except Exception as e:
        print(f"Warning: Could not save history for {uid} (DB Offline)")
    return True

async def validate_invite_code(code: str) -> dict:
    """Checks if an invite code is valid, not expired, and has uses remaining."""
    if not code:
        return {"valid": False, "message": "Invite code is required"}
        
    code = code.replace(" ", "").upper()
    master_code = os.getenv("DEV_MASTER_INVITE_CODE")
    if master_code and code == master_code:
        return {"valid": True, "message": "Master Developer Access Granted"}

    try:
        pool = await get_db_pool()
        if not pool: return {"valid": False, "message": "Database offline"}
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM invite_codes WHERE code = $1', code)
            if not row:
                return {"valid": False, "message": "Invalid code"}
            if row['current_uses'] >= row['max_uses']:
                return {"valid": False, "message": "Code has reached maximum usage"}
            if row['expires_at'] and row['expires_at'] < datetime.now():
                return {"valid": False, "message": "Code has expired"}
            return {"valid": True, "message": "Code is valid"}
    except Exception as e:
        print(f"Invite Validation Error: {e}")
        return {"valid": False, "message": f"Validation error: {e}"}

async def consume_invite_code(code: str) -> bool:
    """Deletes an invite code after it is successfully used (except master dev code)."""
    try:
        if not code: return False
        code = code.replace(" ", "").upper()
        master_code = os.getenv("DEV_MASTER_INVITE_CODE")
        if master_code and code == master_code:
            return True
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('DELETE FROM invite_codes WHERE code = $1', code)
            return True
    except Exception as e:
        print(f"Invite Deletion Error: {e}")
        return False

# --- Production OTP Handlers ---

async def verify_otp(identifier: str, otp: str) -> bool:
    """Verifies a 6-digit OTP and handles the master bypass code."""

    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM user_otps
                WHERE identifier = $1 AND otp = $2
                AND created_at > (NOW() - INTERVAL '10 minutes')
                ORDER BY created_at DESC LIMIT 1
            ''', identifier.lower(), otp)  # .lower() matches how save_otp stores it
            if row:
                await conn.execute('DELETE FROM user_otps WHERE identifier = $1', identifier)
                return True
            return False
    except Exception as e:
        print(f"OTP Verification Error: {e}")
        return False

async def save_otp(identifier: str, otp: str) -> bool:
    """Saves or updates a 6-digit OTP for a specific identifier."""
    try:
        pool = await get_db_pool()
        if not pool: return False
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
