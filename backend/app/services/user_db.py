import os
import asyncpg
from datetime import datetime
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from app.services.retriever import ensure_card_search_schema, init_db_pool

load_dotenv()

class UserRecord(BaseModel):
    uid: str
    email: str | None = None
    display_name: str | None = Field(None, alias="displayName")
    primary_language: str | None = Field(None, alias="primaryLanguage")
    invite_code: str | None = Field(None, alias="inviteCode")
    profile_picture: str | None = Field(None, alias="profilePicture")

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
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
        
        # 5. Seed initial dev code if empty
        await conn.execute("INSERT INTO invite_codes (code) VALUES ('EPIC-DEV-2026') ON CONFLICT DO NOTHING")

        
        # Temporary Debug Check (Verification)
        result = await conn.fetch("SELECT uid, email, display_name FROM users LIMIT 20")
        print(f"\n[DB DEBUG] Found {len(result)} users in PostgreSQL:")
        for row in result:
             # Records work like dicts but without .get()
             email = row['email'] if 'email' in row else 'N/A'
             name = row['display_name'] if 'display_name' in row else 'N/A'
             print(f" - UID: {row['uid']}, Email: {email}, Name: {name}")
        print("-" * 30 + "\n")
    await ensure_card_search_schema()

async def save_user(user: UserRecord):
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (uid, email, display_name, primary_language, invite_code, profile_picture)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (uid) 
                DO UPDATE SET 
                    email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    primary_language = EXCLUDED.primary_language,
                    invite_code = EXCLUDED.invite_code,
                    profile_picture = EXCLUDED.profile_picture
            ''', user.uid, user.email, user.display_name, user.primary_language, user.invite_code, user.profile_picture)
    except Exception as e:
        print(f"Warning: Could not save user {user.uid} to DB: {e}")
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

    # Tier 2: Return None if not in DB. 
    # The frontend will now handle the sync with the data it already has from the login.
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
            # Reverse to get chronological order for OpenAI
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
            # Ensure user exists first (FK safety)
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
            row = await conn.fetchrow('''
                SELECT * FROM invite_codes WHERE code = $1
            ''', code)
            
            if not row:
                return {"valid": False, "message": "Invalid code"}
            
            # Check usage
            if row['current_uses'] >= row['max_uses']:
                return {"valid": False, "message": "Code has reached maximum usage"}
            
            # Check expiration
            if row['expires_at'] and row['expires_at'] < datetime.now():
                return {"valid": False, "message": "Code has expired"}
            
            return {"valid": True, "message": "Code is valid"}
    except Exception as e:
        print(f"Invite Validation Error: {e}")
        return {"valid": False, "message": f"Validation error: {e}"}

async def consume_invite_code(code: str) -> bool:
    """Increments the usage count of an invite code."""
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE invite_codes 
                SET current_uses = current_uses + 1 
                WHERE code = $1
            ''', code)
            return True
    except Exception as e:
        print(f"Invite Consumption Error: {e}")
        return False

