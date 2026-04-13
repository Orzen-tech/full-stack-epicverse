import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check_db_status():
    # Fix scheme for asyncpg
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    print(f"Connecting to {url}...")
    try:
        conn = await asyncpg.connect(url)
        rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
        print("\n[Modes currently in DB]:")
        for r in rows:
            print(f" - '{r['gameplay_mode']}'")
        
        # Test a query for 2 and 38 in Mode 1
        query = '''
            SELECT final_status, revised_scholar_reason 
            FROM card_combos 
            WHERE LOWER(gameplay_mode) LIKE '%balakanda%'
            AND (character_card_number = 2 AND attribute_card_no = 38)
            OR (character_card_number = 38 AND attribute_card_no = 2)
        '''
        results = await conn.fetch(query)
        print(f"\n[Test Query result for 2 & 38 in Balakanda]: {len(results)} rows")
        for r in results:
            print(f" - Status: {r['final_status']} | Reason Snippet: {r['revised_scholar_reason'][:50]}...")
            
        await conn.close()
    except Exception as e:
        print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check_db_status())
