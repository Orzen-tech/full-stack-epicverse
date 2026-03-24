import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Check for 1 and 27 in Mode 2
        query = """
            SELECT character, character_card_number, virtue_karma, virtue_karma_card_number, combo_status 
            FROM card_combos 
            WHERE gameplay_mode = 'Mode 2' 
            AND (
                (character_card_number = 1 AND virtue_karma_card_number = 27)
                OR 
                (character_card_number = 27 AND virtue_karma_card_number = 1)
            )
        """
        rows = await conn.fetch(query)
        print(f"Total matches: {len(rows)}")
        for r in rows:
            print(f"Char: {r['character']} ({r['character_card_number']}), Virtue: {r['virtue_karma']} ({r['virtue_karma_card_number']}), Status: {r['combo_status']}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
