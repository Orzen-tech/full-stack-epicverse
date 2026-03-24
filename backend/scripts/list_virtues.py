import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Check all unique virtues
        rows = await conn.fetch("SELECT DISTINCT virtue_karma FROM card_combos WHERE gameplay_mode = 'Mode 2'")
        print(f"Unique Mode 2 virtues: {len(rows)}")
        for r in rows:
            print(f"- {r['virtue_karma']}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check())
