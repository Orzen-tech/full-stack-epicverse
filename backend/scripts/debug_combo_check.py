import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check_combo():
    print(f"Connecting to {DATABASE_URL}")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Check for 1 and 27 in Mode 2
        query = """
            SELECT * FROM card_combos 
            WHERE gameplay_mode = 'Mode 2' 
            AND (
                (character_card_number = 1 AND virtue_karma_card_number = 27)
                OR 
                (character_card_number = 27 AND virtue_karma_card_number = 1)
            )
        """
        rows = await conn.fetch(query)
        print(f"Found {len(rows)} matching rows:")
        for row in rows:
            print(dict(row))
            
        # Also check column names to be sure
        cols_query = "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'card_combos'"
        cols = await conn.fetch(cols_query)
        print("\nColumns in card_combos:")
        for col in cols:
            print(f"{col['column_name']} ({col['data_type']})")
            
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(check_combo())
