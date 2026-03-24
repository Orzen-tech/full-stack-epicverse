import os
import asyncpg
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from app.services.retriever import init_db_pool

load_dotenv()

class UserRecord(BaseModel):
    uid: str
    email: str | None = None
    display_name: str | None = Field(None, alias="displayName")
    primary_language: str | None = Field(None, alias="primaryLanguage")
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
        
        # Temporary Debug Check (Verification)
        result = await conn.fetch("SELECT uid, email, display_name FROM users LIMIT 20")
        print(f"\n[DB DEBUG] Found {len(result)} users in PostgreSQL:")
        for row in result:
             # Records work like dicts but without .get()
             email = row['email'] if 'email' in row else 'N/A'
             name = row['display_name'] if 'display_name' in row else 'N/A'
             print(f" - UID: {row['uid']}, Email: {email}, Name: {name}")
        print("-" * 30 + "\n")

async def save_user(user: UserRecord):
    try:
        pool = await get_db_pool()
        if not pool: return False
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (uid, email, display_name, primary_language, profile_picture)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (uid) 
                DO UPDATE SET 
                    email = EXCLUDED.email,
                    display_name = EXCLUDED.display_name,
                    primary_language = EXCLUDED.primary_language,
                    profile_picture = EXCLUDED.profile_picture
            ''', user.uid, user.email, user.display_name, user.primary_language, user.profile_picture)
    except Exception as e:
        print(f"Warning: Could not save user {user.uid} to DB: {e}")
    return True

from firebase_admin import auth

async def get_user(uid: str):
    # Tier 1: Try PostgreSQL
    try:
        pool = await get_db_pool()
        if pool:
            async with pool.acquire() as conn:
                row = await conn.fetchrow('SELECT * FROM users WHERE uid = $1', uid)
                if row:
                    return dict(row)
    except Exception as e:
        print(f"PostgreSQL Profile Fetch Error: {e}")

    # Tier 2: Try Firebase Admin (Real identity data)
    try:
        user = auth.get_user(uid)
        return {
            "uid": uid,
            "email": user.email,
            "display_name": user.display_name or "New User",
            "primary_language": "English",
            "profile_picture": None, # App expects Base64 for custom uploads; Google photos are URLs
            "current_mode": "Mode 1"
        }
    except Exception as e:
        print(f"Firebase Profile Fetch Error: {e}")
        # Final Mock Fallback
        return {
            "uid": uid,
            "email": f"{uid}@example.com",
            "display_name": "Dev User",
            "primary_language": "English",
            "profile_picture": None,
            "current_mode": "Mode 1"
        }

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
