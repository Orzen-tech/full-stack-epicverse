import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Check all Mode 2 rows
        rows = await conn.fetch("SELECT gameplay_mode, character, character_card_number, virtue_karma, virtue_karma_card_number, combo_status FROM card_combos WHERE gameplay_mode = 'Mode 2'")
        print(f"Total Mode 2 rows: {len(rows)}")
        for r in rows:
            if (r['character_card_number'] == 1 and r['virtue_karma_card_number'] == 27) or (r['character_card_number'] == 27 and r['virtue_karma_card_number'] == 1):
                print(f"MATCH: {r}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
