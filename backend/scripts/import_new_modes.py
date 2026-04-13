import pandas as pd
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_all_modes():
    data_dir = "e:/kriyora/EpicVerse/backend/data"
    # Mapping of File Index -> Correct Mode Name
    KANDA_MAP = {
        1: "OriginArc (Balakanda)",
        2: "CrownShift (AyodhyaKanda)",
        3: "WildRun (AranyaKanda)",
        4: "GlowLine (KishkindhaKanda)",
        5: "lankaLeap (SundaraKanda)",
        6: "WarRoom (YuddhaKanda)",
        7: "AfterLight (UttaraKanda)"
    }
    
    print(f"Connecting to database...")
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    
    print("GLOBAL RESET: Deleting all existing card_combos...")
    await conn.execute("DELETE FROM card_combos")
    
    total_added = 0
    for i in range(1, 8):
        file = f"mode {i}.xlsx"
        path = os.path.join(data_dir, file)
        correct_mode = KANDA_MAP[i]
        
        if not os.path.exists(path):
            print(f"SKIPPING: {file} not found.")
            continue
            
        print(f"--- Reading {file} (Target: {correct_mode}) ---")
        df = pd.read_excel(path).replace({pd.NA: None})
        
        success_count = 0
        for _, row in df.iterrows():
            try:
                scripture_text = str(row.get('App Message (Crisp)', ''))
                status = str(row.get('Final Segment', '')) # Current Excel maps status to 'Final Segment'
                
                await conn.execute('''
                    INSERT INTO card_combos (
                        gameplay_mode, character, character_card_number, 
                        attribute, attribute_card_no,
                        final_segment, final_status, revised_scholar_reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ''', 
                correct_mode, # FORCE THE CORRECT LABEL
                str(row.get('Character', '')),
                int(row.get('Character Card No.', 0)) if pd.notnull(row.get('Character Card No.')) else 0,
                str(row.get('Attribute', '')),
                int(row.get('Attribute Card No.', 0)) if pd.notnull(row.get('Attribute Card No.')) else 0,
                scripture_text,
                status,
                scripture_text # Scholar reason
                )
                success_count += 1
            except Exception as e:
                # pass
                if success_count == 0: print(f"Row error: {e}")
        
        print(f"SUCCESS: {success_count} rows added.")
        total_added += success_count
            
    print("-" * 30)
    # Verification Summary
    total_in_db = await conn.fetchval("SELECT COUNT(*) FROM card_combos")
    modes_in_db = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
    
    print("EPICVERSE FINAL TRUTH SYNC REPORT")
    print("=" * 30)
    print(f"Total Rows Synchronized: {total_added}")
    print(f"Database Final Total:    {total_in_db}")
    print(f"Unique Modes Present:     {len(modes_in_db)}")
    print(f"Verified Final Statuses:  {[r['gameplay_mode'] for r in modes_in_db]}")
    print("=" * 30)
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(import_all_modes())
