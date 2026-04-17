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
    display_name: str | None = Field(None, alias="display_name")
    primary_language: str | None = Field(None, alias="primary_language")
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

        # 5. OTPs Table (Production Auth)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_otps (
                email TEXT PRIMARY KEY,
                otp TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 6. Seed initial dev code if empty
        await conn.execute("INSERT INTO invite_codes (code) VALUES ('EPIC-DEV-2026') ON CONFLICT DO NOTHING")

        result = await conn.fetch("SELECT uid, email, display_name, profile_picture FROM users LIMIT 20")
        print(f"\n[DB DEBUG] Found {len(result)} users in PostgreSQL:")
        for row in result:
             email = row['email'] if 'email' in row else 'N/A'
             name = row['display_name'] if 'display_name' in row else 'N/A'
             has_photo = "YES" if row['profile_picture'] else "NO"
             print(f" - UID: {row['uid']}, Email: {email}, Name: {name}, Photo: {has_photo}")
        print("-" * 30 + "\n")
    await ensure_card_search_schema()

async def save_user(user: UserRecord):
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            print(f"[DB] Saving user {user.uid}: Name={user.display_name}, Email={user.email}, Photo={'YES' if user.profile_picture else 'NO'}")
            await conn.execute('''
                INSERT INTO users (uid, email, display_name, primary_language, invite_code, profile_picture, last_session_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (uid) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                    primary_language = COALESCE(EXCLUDED.primary_language, users.primary_language),
                    invite_code = COALESCE(EXCLUDED.invite_code, users.invite_code),
                    profile_picture = COALESCE(EXCLUDED.profile_picture, users.profile_picture),
                    last_session_id = COALESCE(EXCLUDED.last_session_id, users.last_session_id)
            ''', user.uid, user.email, user.display_name, user.primary_language, user.invite_code, user.profile_picture, user.session_id)
    except Exception as e:
        print(f"CRITICAL: Could not save user {user.uid} to DB: {e}")
    return True

async def verify_session(uid: str, session_id: str) -> bool:
    """Checks if the given session_id is the active one in the database."""
    try:
        pool = await get_db_pool()
        if not pool: return True # Default to true if DB is offline
        async with pool.acquire() as conn:
            db_session = await conn.fetchval('SELECT last_session_id FROM users WHERE uid = $1', uid)
            if not db_session: return True # No session tracked yet
            return db_session == session_id
    except Exception as e:
        print(f"Session Verification Error: {e}")
        return True


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
        if code == "EPIC-DEV-2026":
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

async def verify_otp(email: str, otp: str) -> bool:
    """Verifies a 6-digit OTP and handles the master bypass code."""

    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM user_otps 
                WHERE email = $1 AND otp = $2 
                AND created_at > (NOW() - INTERVAL '10 minutes')
                ORDER BY created_at DESC LIMIT 1
            ''', email, otp)
            if row:
                await conn.execute('DELETE FROM user_otps WHERE email = $1', email)
                return True
            return False
    except Exception as e:
        print(f"OTP Verification Error: {e}")
        return False

async def save_otp(email: str, otp: str) -> bool:
    """Saves or updates a 6-digit OTP for a specific email."""
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_otps (email, otp, created_at)
                VALUES ($1, $2, CURRENT_TIMESTAMP)
                ON CONFLICT (email) DO UPDATE SET
                    otp = EXCLUDED.otp,
                    created_at = CURRENT_TIMESTAMP
            ''', email.lower(), otp)
            return True
    except Exception as e:
        print(f"[DB] OTP Save Error: {e}")
        return False
