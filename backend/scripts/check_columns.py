import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    rows = await conn.fetch('SELECT * FROM card_combos LIMIT 1')
    if rows:
        cols = list(rows[0].keys())
        for col in cols:
            print(col)
    else:
        print("No rows found in card_combos")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
