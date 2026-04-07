import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
    for r in rows:
        print(f"'{r[0]}'")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
