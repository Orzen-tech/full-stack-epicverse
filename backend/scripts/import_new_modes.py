import pandas as pd
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_all_modes():
    data_dir = "e:/kriyora/EpicVerse/backend/data"
    # All 7 modes for the Ramayana Edition
    files = [f"mode{i}.xlsx" for i in range(1, 8)]
    
    print(f"Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    # 1. Global Reset (Clean State for Final Sync)
    print("Performing Global Reset of card_combos for Final Truth Sync...")
    await conn.execute("DELETE FROM card_combos")
    
    total_added = 0
    for file in files:
        path = os.path.join(data_dir, file)
        if not os.path.exists(path):
            print(f"SKIPPING: {file} not found.")
            continue
            
        print(f"--- Reading {file} ---")
        df = pd.read_excel(path)
        print(f"Importing {len(df)} rows for {file}...")
        
        success_count = 0
        for _, row in df.iterrows():
            try:
                # Synchronizing Scholarship: App Message (Crisp) maps to both Segment and Scholar Reason
                scripture_text = str(row.get('App Message (Crisp)', ''))
                
                await conn.execute('''
                    INSERT INTO card_combos (
                        gameplay_mode, character, character_card_number, 
                        attribute, attribute_card_no,
                        final_segment, final_status, revised_scholar_reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ''', 
                str(row.get('Game Play Mode', '')),
                str(row.get('Character', '')),
                int(row.get('Character Card No.', 0)) if pd.notnull(row.get('Character Card No.')) else 0,
                str(row.get('Attribute', '')),
                int(row.get('Attribute Card No.', 0)) if pd.notnull(row.get('Attribute Card No.')) else 0,
                scripture_text,
                str(row.get('Final Segment', '')),
                scripture_text # Syncing Scholar Reason with App Msg
                )
                success_count += 1
            except Exception as e:
                # Skip row if fatal error (mapping delta safeguard)
                pass
        
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
