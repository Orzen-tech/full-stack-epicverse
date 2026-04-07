import pandas as pd
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_mode_1():
    file_path = "e:/kriyora/EpicVerse/backend/data/Origin Arc ( Balakanda)_ Mode 1.xlsx"
    print(f"Reading {file_path}...")
    df = pd.read_excel(file_path)
    
    # Map columns to DB columns
    # gameplay_mode -> Game Play Mode
    # character -> Character
    # character_card_number -> Character Card No.
    # virtue_karma -> Attribute
    # virtue_karma_card_number -> Attribute Card No.
    # combo_status -> Category
    # final_status -> Final Segment
    # validation_reason -> Revised Scholar Reason
    
    print(f"Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    print(f"Clearing any existing data...")
    await conn.execute("DELETE FROM card_combos")
    
    print(f"Importing {len(df)} rows...")
    success_count = 0
    
    for _, row in df.iterrows():
        try:
            await conn.execute('''
                INSERT INTO card_combos (
                    gameplay_mode, character, character_card_number, virtue_karma, 
                    virtue_karma_card_number, combo_status, final_status, validation_reason
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ''', 
            str(row.get('Game Play Mode', 'Mode 1')),
            str(row.get('Character', '')),
            int(row.get('Character Card No.', 0)) if pd.notnull(row.get('Character Card No.')) else 0,
            str(row.get('Attribute', '')),
            int(row.get('Attribute Card No.', 0)) if pd.notnull(row.get('Attribute Card No.')) else 0,
            str(row.get('Category', '')),
            str(row.get('Final Segment', '')),
            str(row.get('Revised Scholar Reason', ''))
            )
            success_count += 1
        except Exception as e:
            print(f"Row {success_count} failed: {e}")
            
    print(f"Import Successful: {success_count} rows added.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(import_mode_1())
