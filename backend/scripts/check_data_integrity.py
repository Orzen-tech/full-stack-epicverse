import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def check_integrity():
    conn = await asyncpg.connect(DATABASE_URL)
    
    print("--- Database Integrity Audit ---")
    
    # 1. Check Samples
    rows = await conn.fetch("""
        SELECT gameplay_mode, character, attribute_card_no, 
               final_segment, revised_scholar_reason 
        FROM card_combos 
        LIMIT 5
    """)
    
    for r in rows:
        same = (r['final_segment'] == r['revised_scholar_reason'])
        print(f"Mode: {r['gameplay_mode']}")
        print(f"Char: {r['character']} | Card: {r['attribute_card_no']}")
        print(f"App Msg: {str(r['final_segment'])[:50]}...")
        print(f"Scholar: {str(r['revised_scholar_reason'])[:50]}...")
        print(f"ARE THEY SAME? {'✅ YES' if same else '❌ NO'}")
        print("-" * 20)
        
    # 2. Global Stats
    total = await conn.fetchval("SELECT COUNT(*) FROM card_combos")
    diff_count = await conn.fetchval("SELECT COUNT(*) FROM card_combos WHERE final_segment != revised_scholar_reason")
    same_count = total - diff_count
    
    print("\nFINAL INTEGRITY REPORT")
    print("=" * 30)
    print(f"Total Database Rows:  {total}")
    print(f"Rows with SAME text:  {same_count} ({(same_count/total)*100:.1f}%)")
    print(f"Rows with DIFF text:  {diff_count} ({(diff_count/total)*100:.1f}%)")
    print("=" * 30)
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check_integrity())
