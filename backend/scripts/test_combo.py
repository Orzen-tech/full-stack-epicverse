import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def test_combo():
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow('''
        SELECT combo_status, final_status, validation_reason 
        FROM card_combos 
        WHERE LOWER(gameplay_mode) LIKE '%balakanda%' 
        AND (character_card_number = 1 OR virtue_karma_card_number = 1) 
        AND (character_card_number = 28 OR virtue_karma_card_number = 28)
    ''')
    print(f"RESULT: {row}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(test_combo())
