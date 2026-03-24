import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    count = await conn.fetchval("SELECT count(*) FROM card_combos")
    print(f"Total rows: {count}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
