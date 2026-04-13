import asyncio
import sys
import os

# Ensure app is importable
sys.path.append(os.getcwd())

from app.services.retriever import init_db_pool, db_pool

async def check():
    await init_db_pool()
    from app.services.retriever import db_pool
    
    if not db_pool:
        print("Failed to initialize DB pool.")
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM card_combos WHERE gameplay_mode = $1 AND (character_card_number = $2 AND attribute_card_no = $3)', 
                               'CrownShift (AyodhyaKanda)', 8, 25)
        print(f"Found {len(rows)} rows for 8 & 25")
        for r in rows:
            print(f"Mode: {r['gameplay_mode']}")
            print(f"Reason: {r['revised_scholar_reason']}")
        
        rows = await conn.fetch('SELECT * FROM card_combos WHERE gameplay_mode = $1 AND (character_card_number = $2 AND attribute_card_no = $3)', 
                               'CrownShift (AyodhyaKanda)', 3, 103)
        print(f"\nFound {len(rows)} rows for 3 & 103")
        for r in rows:
            print(f"Reason: {r['revised_scholar_reason']}")

asyncio.run(check())
