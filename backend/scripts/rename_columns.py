import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def rename_columns():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    
    print("\n--- [RENAMING COLUMNS - PART 2] ---")
    try:
        await conn.execute('ALTER TABLE card_combos RENAME COLUMN combo_status TO final_segment')
        print("✓ combo_status -> final_segment")
    except Exception as e: print(f"Error (final_segment): {e}")

    print("\n[SCHEMA UPDATE COMPLETE]")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(rename_columns())
