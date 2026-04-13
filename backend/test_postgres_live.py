import asyncio
import asyncpg
import os
import json
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")

# Test cases: (char, attr, mode_name)
# We need to use the actual mode names found in the database
MODE_1 = "Origin Arc( Balakanda)"
MODE_2 = "CrownShift (Ayodhya Kanda)"

TEST_CASES = [
    (1, 29, MODE_1),
    (1, 30, MODE_1),
    (2, 29, MODE_1),
    (5, 53, MODE_2), # Sample I saw earlier
    (7, 55, MODE_1),
]

async def test_live_db():
    print(f"Connecting to LIVE DATABASE: {DATABASE_URL}")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ Connected.\n")
        
        results = []
        for c1, a1, mode in TEST_CASES:
            print(f"Querying: Mode='{mode}' | Cards={c1}+{a1}")
            # Query the database
            row = await conn.fetchrow("""
                SELECT gameplay_mode, character, character_card_number, attribute, 
                       attribute_card_no, final_status, final_segment, revised_scholar_reason
                FROM card_combos
                WHERE (character_card_number = $1 AND attribute_card_no = $2 AND gameplay_mode = $3)
                   OR (character_card_number = $2 AND attribute_card_no = $1 AND gameplay_mode = $3)
                LIMIT 1
            """, c1, a1, mode)
            
            if row:
                results.append({
                    "Cards": f"{c1}+{a1}",
                    "Mode": mode,
                    "Char": row['character'],
                    "Status": row['final_status'],
                    "Segment": (row['final_segment'] or row['revised_scholar_reason'])[:60] + "..."
                })
            else:
                results.append({
                    "Cards": f"{c1}+{a1}",
                    "Mode": mode,
                    "Char": "N/A",
                    "Status": "NOT FOUND",
                    "Segment": "---"
                })

        # Print Table
        print("\n" + "="*80)
        print(f"{'CARDS':<10} | {'MODE':<25} | {'CHAR':<12} | {'STATUS':<15} | {'SEGMENT PREVIEW'}")
        print("-" * 80)
        for r in results:
            print(f"{r['Cards']:<10} | {r['Mode']:<25} | {r['Char']:<12} | {r['Status']:<15} | {r['Segment']}")
        print("="*80 + "\n")

        await conn.close()
    except Exception as e:
        print(f"❌ DATABASE ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_live_db())
