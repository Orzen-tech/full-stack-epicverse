import asyncio
import asyncpg
import pandas as pd
import numpy as np
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_mode(file_path):
    print(f"\n--- Importing from {file_path} ---")
    df = pd.read_excel(file_path)
    df = df.replace({np.nan: None})
    
    # Precise mapping for Mode 3/4/5 Excel structure
    mapping = {
        'Game Play Mode': 'gameplay_mode',
        'Gameplay Mode': 'gameplay_mode',
        'Character': 'character',
        'Character Card No.': 'character_card_number',
        'Attribute': 'attribute',
        'Attribute Card No': 'attribute_card_no',
        'Final Segment': 'final_segment',
        'App Message (Crisp)': 'revised_scholar_reason',
        'Status': 'final_status',
        'Final Status': 'final_status',
        'Kāṇḍa': 'kanda',
        'Kanda': 'kanda'
    }
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Determine actual mode name
        actual_mode = "Unknown"
        if 'Game Play Mode' in df.columns:
            actual_mode = str(df['Game Play Mode'].iloc[0])
        elif 'Gameplay Mode' in df.columns:
            actual_mode = str(df['Gameplay Mode'].iloc[0])
            
        print(f"Detected Mode name in Excel: {actual_mode}")
        
        # Clean existing data for this mode
        print(f"Cleaning existing data for {actual_mode}...")
        await conn.execute("DELETE FROM card_combos WHERE gameplay_mode = $1", actual_mode)
        
        # Build insertion query
        db_cols = []
        excel_cols = []
        for excel_col, db_col in mapping.items():
            if excel_col in df.columns:
                db_cols.append(db_col)
                excel_cols.append(excel_col)
        
        # Remove duplicates from db_cols if multiple excel cols map to same db col
        unique_db_cols = []
        unique_excel_cols = []
        seen_db_cols = set()
        for ec, dc in zip(excel_cols, db_cols):
            if dc not in seen_db_cols:
                unique_db_cols.append(dc)
                unique_excel_cols.append(ec)
                seen_db_cols.add(dc)

        cols_str = ", ".join(unique_db_cols)
        vals_str = ", ".join([f"${i+1}" for i in range(len(unique_db_cols))])
        query = f"INSERT INTO card_combos ({cols_str}) VALUES ({vals_str})"
        
        records_to_insert = []
        for _, row in df.iterrows():
            record = []
            for col in unique_excel_cols:
                val = row[col]
                # Force integers for card numbers
                if 'card_number' in mapping.get(col, '') or 'card_no' in mapping.get(col, ''):
                    try:
                        val = int(float(val)) if val is not None else 0
                    except:
                        val = 0
                record.append(val)
            records_to_insert.append(record)
            
        print(f"Inserting {len(records_to_insert)} records...")
        await conn.executemany(query, records_to_insert)
        
        count = await conn.fetchval("SELECT COUNT(*) FROM card_combos WHERE gameplay_mode = $1", actual_mode)
        print(f"SUCCESS: Imported {count} records for {actual_mode}")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await conn.close()

async def main():
    base_path = 'e:/kriyora/EpicVerse/backend/data/'
    # Mode 3: AranyaKanda, Mode 5: SundaraKanda
    for i in [3, 5]:
        file_path = os.path.join(base_path, f'mode{i}.xlsx')
        if os.path.exists(file_path):
            await import_mode(file_path)
        else:
            print(f"Skipping {file_path} (not found)")

if __name__ == "__main__":
    asyncio.run(main())
