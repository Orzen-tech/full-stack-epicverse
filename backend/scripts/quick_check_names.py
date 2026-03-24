import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Check by name
        res = await conn.fetch("SELECT * FROM card_combos WHERE gameplay_mode = 'Mode 2' AND (LOWER(character) LIKE '%rama%' OR LOWER(virtue_karma) LIKE '%rama%') AND (LOWER(character) LIKE '%sacrifice%' OR LOWER(virtue_karma) LIKE '%sacrifice%')")
        print(f"Found by name: {len(res)}")
        for r in res:
             print(f"Mode: {r['gameplay_mode']}, Char: {r['character']} ({r['character_card_number']}), Virtue: {r['virtue_karma']} ({r['virtue_karma_card_number']}), Status: {r['combo_status']}")
             
        # Also check card numbers for these rows specifically
        res_nums = await conn.fetch("SELECT character, character_card_number, virtue_karma, virtue_karma_card_number FROM card_combos WHERE gameplay_mode = 'Mode 2' LIMIT 5")
        print("\nSample Mode 2 data:")
        for r in res_nums:
            print(f"{r['character']} (CN: {r['character_card_number']}) | {r['virtue_karma']} (VCN: {r['virtue_karma_card_number']})")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
