import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        res = await conn.fetch("SELECT * FROM card_combos WHERE gameplay_mode = 'Mode 2' AND character_card_number = 1 AND virtue_karma_card_number = 27")
        if res:
             for k, v in res[0].items():
                 print(f"{k}: {v}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
