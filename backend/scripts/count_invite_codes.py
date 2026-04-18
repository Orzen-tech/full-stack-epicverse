import asyncio
import os
import sys

# Ensure backend directory is in path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.services.user_db import get_db_pool

async def check_count():
    pool = await get_db_pool()
    if not pool:
        print("❌ [ERROR] Could not connect to Database Pool.")
        return

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM invite_codes")
        print(f"\n[DB-AUDIT] Active Invite Codes: {count}")
        
        # Also check if any have been used (just in case)
        used_count = await conn.fetchval("SELECT COUNT(*) FROM invite_codes WHERE current_uses > 0")
        if used_count > 0:
            print(f"⚠️ [WARNING] {used_count} codes have already been used.")

if __name__ == "__main__":
    asyncio.run(check_count())
