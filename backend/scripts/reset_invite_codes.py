import asyncio
import os
import sys
import random
import string

# Ensure backend directory is in path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.services.user_db import get_db_pool, init_db

def generate_random_code(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

async def create_500_invite_codes():
    print("🚀 [INVITE-BULK-GEN] Starting mass generation process...")
    
    # 1. Generate 500 unique codes
    new_codes = set()
    while len(new_codes) < 500:
        raw_code = generate_random_code()
        new_codes.add(f"EPIC-{raw_code}")
    
    codes_list = list(new_codes)
    
    # 2. Database Action
    await init_db()
    pool = await get_db_pool()
    if not pool:
        print("❌ [ERROR] Could not connect to Database Pool.")
        return

    async with pool.acquire() as conn:
        print("🧹 Clearing all existing invite codes...")
        await conn.execute("DELETE FROM invite_codes")
        
        print(f"✨ Injecting 500 newly generated codes...")
        # Prepare data for executemany
        # Format: [(code, max_uses, current_uses), ...]
        data = [(code, 1, 0) for code in codes_list]
        
        await conn.executemany(
            "INSERT INTO invite_codes (code, max_uses, current_uses) VALUES ($1, $2, $3)",
            data
        )
        
        # 3. Verify
        count = await conn.fetchval("SELECT COUNT(*) FROM invite_codes")
        print(f"\n✅ [SUCCESS] Database updated with {count} codes!")

    # 4. Save to a file for the user
    output_file = "EpicVerse_Invite_Codes_Batch_1.txt"
    with open(output_file, "w") as f:
        f.write("\n".join(codes_list))
    
    print(f"📄 Generated codes saved to: {os.path.abspath(output_file)}")

if __name__ == "__main__":
    asyncio.run(create_500_invite_codes())
