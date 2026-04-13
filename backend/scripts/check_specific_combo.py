import asyncio
import sys
import os

sys.path.append(os.getcwd())
from app.services.retriever import init_db_pool

async def check():
    await init_db_pool()
    from app.services.retriever import db_pool
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM card_combos WHERE gameplay_mode = $1 AND character_card_number = $2 AND attribute_card_no = $3', 
                                 'OriginArc (Balakanda)', 1, 29)
        if row:
            print(f"MODE: {row['gameplay_mode']}")
            print(f"CARDS: {row['character_card_number']} & {row['attribute_card_no']}")
            print("-" * 30)
            print(f"STATUS: {row['final_status']}")
            print(f"SEGMENT: {row['final_segment']}")
            print(f"REASON: {row['revised_scholar_reason']}")
        else:
            print("Row NOT FOUND for OriginArc [1 & 29].")

asyncio.run(check())
