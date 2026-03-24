import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        res = await conn.fetch("SELECT combo_status, final_status FROM card_combos WHERE gameplay_mode = 'Mode 2' AND character_card_number = 1 AND virtue_karma_card_number = 27")
        if res:
             print(f"Combo Status: {res[0]['combo_status']}")
             print(f"Final Status: {res[0]['final_status']}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
