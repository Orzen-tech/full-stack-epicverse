import asyncio
import asyncpg
import os
import argparse
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def lookup(mode, card1, card2):
    conn = await asyncpg.connect(DATABASE_URL)
    
    # Precise Scripture Lookup
    row = await conn.fetchrow("""
        SELECT gameplay_mode, character_card_number, attribute_card_no, 
               final_status, final_segment, revised_scholar_reason 
        FROM card_combos 
        WHERE character_card_number = $1 AND attribute_card_no = $2
          AND gameplay_mode ILIKE $3
        LIMIT 1
    """, int(card1), int(card2), f"%{mode}%")
    
    if row:
        print(f"\n--- SCRIPTURE FOUND: {row['gameplay_mode']} ---")
        print(f"Cards:      {row['character_card_number']} + {row['attribute_card_no']}")
        print(f"Status:     {row['final_status']}")
        print(f"TIER 1 (Msg): {row['final_segment']}")
        print(f"TIER 2 (Scholar): {row['revised_scholar_reason']}\n")
    else:
        print(f"\n--- NO COMBO IN SCRIPTURES for {mode} ({card1}+{card2}) ---\n")
        
    await conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--card1", required=True)
    parser.add_argument("--card2", required=True)
    args = parser.parse_args()
    
    asyncio.run(lookup(args.mode, args.card1, args.card2))
