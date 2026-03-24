import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def verify():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch("SELECT gameplay_mode, COUNT(*) as count FROM card_combos GROUP BY gameplay_mode ORDER BY gameplay_mode")
        print("\n--- Current Database Counts ---")
        for row in rows:
            print(f"Mode: {row['gameplay_mode']} | Count: {row['count']}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(verify())
