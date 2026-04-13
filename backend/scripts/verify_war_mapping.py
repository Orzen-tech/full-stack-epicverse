import asyncio
import sys
import os

sys.path.append(os.getcwd())
from app.services.retriever import init_db_pool

async def check():
    await init_db_pool()
    from app.services.retriever import db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM card_combos WHERE gameplay_mode = $1 AND character_card_number = $2 AND attribute_card_no IN (42, 43, 44)', 
                               'WarRoom (YuddhaKanda)', 3)
        print(f"Checking WarRoom for Lakshmana (3)...")
        for r in rows:
            print(f"Attr: {r['attribute_card_no']}")
            print(f"Status: {r['final_status']}")
            print(f"Reason: {r['revised_scholar_reason']}")
            print("-" * 20)

asyncio.run(check())
