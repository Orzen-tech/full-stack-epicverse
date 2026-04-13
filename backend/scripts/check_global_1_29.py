import asyncio
import sys
import os

sys.path.append(os.getcwd())
from app.services.retriever import init_db_pool

async def check():
    await init_db_pool()
    from app.services.retriever import db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT gameplay_mode, final_status FROM card_combos WHERE character_card_number = 1 AND attribute_card_no = 29')
        print(f"Global Status Check for 1 & 29:")
        for r in rows:
            print(f"Mode: {r['gameplay_mode']} | Status: {r['final_status']}")

asyncio.run(check())
