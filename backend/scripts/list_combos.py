import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def list_combos():
    conn = await asyncpg.connect(DATABASE_URL)
    
    modes = [
        'Afterlight (Uttara Kanda)',
        'CrownShift (Ayodhya Kanda)',
        'GlowLine (Kishkindha Kanda)',
        'Origin Arc( Balakanda)',
        'WildRun (Aranya Kanda)'
    ]
    
    for mode in modes:
        print(f"\n--- SAMPLE COMBOS: {mode} ---")
        rows = await conn.fetch("""
            SELECT character_card_number, attribute_card_no, final_status
            FROM card_combos 
            WHERE gameplay_mode = $1
            LIMIT 3
        """, mode)
        
        for row in rows:
            print(f"Cards: {row['character_card_number']} + {row['attribute_card_no']} ({row['final_status']})")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(list_combos())
