import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check_db_status():
    print(f"Connecting to {DATABASE_URL}...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
        print("\n[Modes currently in DB]:")
        for r in rows:
            print(f" - '{r['gameplay_mode']}'")
        
        # Test a query for 2 and 38 in Mode 1
        # In Mode 1, 2 = Character Rama, 38 = Attribute
        query = '''
            SELECT combo_status, final_status, validation_reason 
            FROM card_combos 
            WHERE LOWER(gameplay_mode) LIKE '%balakanda%'
            AND (CAST(character_card_number AS TEXT) = '2' OR CAST(virtue_karma_card_number AS TEXT) = '2')
            AND (CAST(character_card_number AS TEXT) = '38' OR CAST(virtue_karma_card_number AS TEXT) = '38')
        '''
        results = await conn.fetch(query)
        print(f"\n[Test Query result for 2 & 38 in Balakanda]: {len(results)} rows")
        for r in results:
            print(f" - {r}")
            
        await conn.close()
    except Exception as e:
        print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check_db_status())
