import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def get_count():
    print(f"Checking database for unique modes...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        rows = await conn.fetch("SELECT gameplay_mode, COUNT(*) as count FROM card_combos GROUP BY gameplay_mode")
        print("-" * 30)
        if not rows:
            print("No modes found in database.")
        else:
            for row in rows:
                print(f"Mode: '{row['gameplay_mode']}' -> Records: {row['count']}")
        print("-" * 30)
        print(f"Total Unique Modes: {len(rows)}")
        await conn.close()
    except Exception as e:
        print(f"Error checking modes: {e}")

if __name__ == "__main__":
    asyncio.run(get_count())
