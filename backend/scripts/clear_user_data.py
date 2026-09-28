import asyncio
import asyncpg
import os

# --- DATABASE CONFIG ---
# DATABASE_URL must be provided via the environment (e.g. .env locally, or
# Secret Manager in Cloud Run). This script no longer carries a hardcoded
# production credential.
_raw_url = os.getenv("DATABASE_URL")
if not _raw_url:
    raise SystemExit(
        "DATABASE_URL environment variable is not set. "
        "Refusing to run without an explicit database connection string."
    )
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)

async def main():
    print("[DB-PURGE] Starting Production User Data Wipe...")
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # 1. Clear Chat History (Depends on Users)
        await conn.execute("TRUNCATE TABLE chat_history RESTART IDENTITY CASCADE")
        print("🗑️  Chat History Wiped.")
        
        # 2. Clear Users
        await conn.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE")
        print("🗑️  User Profiles Wiped.")
        
        # 3. Clear OTPs
        await conn.execute("TRUNCATE TABLE user_otps RESTART IDENTITY CASCADE")
        print("🗑️  Auth OTP Records Wiped.")
        
        await conn.close()
        print("✅ Production Database is now CLEAN (Invite Codes & Game Data preserved).")
        
    except Exception as e:
        print(f"❌ Database Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
