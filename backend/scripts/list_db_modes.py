import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def list_modes():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    
    print("\n--- [MODES IN DATABASE] ---")
    rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos ORDER BY gameplay_mode")
    for r in rows:
        print(f"-> {r['gameplay_mode']}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(list_modes())
