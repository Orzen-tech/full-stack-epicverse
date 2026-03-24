import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def get_unique_modes():
    print(f"Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Get unique values from gameplay_mode column
        modes = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos ORDER BY gameplay_mode")
        
        print("\n--- Unique Modes in card_combos table ---")
        for i, record in enumerate(modes, 1):
            print(f"{i}. {record['gameplay_mode']}")
            
        count = await conn.fetchval("SELECT COUNT(DISTINCT gameplay_mode) FROM card_combos")
        print(f"\nTotal unique modes: {count}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(get_unique_modes())
