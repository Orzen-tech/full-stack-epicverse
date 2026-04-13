import asyncio
import sys
import os

sys.path.append(os.getcwd())
from app.services.retriever import init_db_pool

async def check():
    await init_db_pool()
    from app.services.retriever import db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT attribute_card_no, final_status, revised_scholar_reason FROM card_combos WHERE gameplay_mode = $1 AND character_card_number = $2 AND attribute_card_no BETWEEN 40 AND 55 ORDER BY attribute_card_no', 
                               'WarRoom (YuddhaKanda)', 3)
        print(f"Targeted Audit for WarRoom - Lakshmana (3) [Attr 40-55]:")
        for r in rows:
            reason_snippet = r['revised_scholar_reason'][:30] if r['revised_scholar_reason'] else "MISSING"
            print(f"Attr {r['attribute_card_no']}: [{r['final_status']}] - {reason_snippet}...")

asyncio.run(check())
