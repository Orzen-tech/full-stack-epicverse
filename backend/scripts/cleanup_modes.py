import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def cleanup_db():
    print(f"Connecting to database to clean up modes...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # Delete everything that IS NOT Mode 1
        # Also handle cases where Mode 1 might be capitalized differently
        res = await conn.execute("DELETE FROM card_combos WHERE LOWER(gameplay_mode) != 'mode 1'")
        print(f"Cleanup Successful: {res}")
        
        # Check remaining
        remaining = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
        print(f"Remaining modes in DB: {[r['gameplay_mode'] for r in remaining]}")
        
        await conn.close()
    except Exception as e:
        print(f"Cleanup Failed: {e}")

if __name__ == "__main__":
    asyncio.run(cleanup_db())
