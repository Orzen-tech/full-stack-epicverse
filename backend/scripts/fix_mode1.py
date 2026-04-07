import pandas as pd
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def reimport_mode1():
    data_dir = "e:/kriyora/EpicVerse/backend/data"
    excel_file = "Origin Arc ( Balakanda)_ Mode 1.xlsx"
    path = os.path.join(data_dir, excel_file)
    
    if not os.path.exists(path):
        print(f"ERROR: File not found at {path}")
        return

    print(f"Reading {excel_file}...")
    df = pd.read_excel(path).fillna("")
    
    potential_mode_name = str(df.iloc[0]['Game Play Mode'])
    print(f"New Mode 1 string from Excel: '{potential_mode_name}'")

    print(f"Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    # 1. Delete "old" mode 1 (Origin Arc) data.
    # We delete anything that looks like Balakanda/Origin Arc to be sure.
    print("Deleting existing Mode 1 database records...")
    await conn.execute("DELETE FROM card_combos WHERE LOWER(gameplay_mode) LIKE '%balakanda%'")
    await conn.execute("DELETE FROM card_combos WHERE LOWER(gameplay_mode) LIKE '%origin arc%'")
    
    # 2. Import "new" Mode 1 data.
    print(f"Starting import of {len(df)} rows...")
    success_count = 0
    
    for _, row in df.iterrows():
        try:
            await conn.execute('''
                INSERT INTO card_combos (
                    gameplay_mode, character, character_card_number, virtue_karma, 
                    virtue_karma_card_number, combo_status, final_status, validation_reason
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ''', 
            str(row.get('Game Play Mode', '')).strip(),
            str(row.get('Character', '')).strip(),
            int(row.get('Character Card No.', 0)) if row.get('Character Card No.') else 0,
            str(row.get('Attribute', '')).strip(),
            int(row.get('Attribute Card No.', 0)) if row.get('Attribute Card No.') else 0,
            str(row.get('Category', '')).strip(),
            str(row.get('Final Segment', '')).strip(),
            str(row.get('Revised Scholar Reason', '')).strip()
            )
            success_count += 1
        except Exception as e:
            # print(f"Error at row {_}: {e}")
            pass
            
    print(f"SUCCESS: {success_count} records re-imported.")
    
    # Final check
    rows = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos WHERE LOWER(gameplay_mode) LIKE '%balakanda%'")
    print(f"Modes currently in DB matching 'Balakanda': {[r['gameplay_mode'] for r in rows]}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(reimport_mode1())
