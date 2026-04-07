import asyncio
import asyncpg
import pandas as pd
import numpy as np
import os
from dotenv import load_dotenv

# Absolute paths for your environment
env_path = r'e:\kriyora\EpicVerse\backend\.env'
data_dir = r'e:\kriyora\EpicVerse\backend\data'
load_dotenv(env_path)
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_mode_file(file_path):
    print(f"\n--- Reading from {os.path.basename(file_path)} ---")
    try:
        df = pd.read_excel(file_path)
        df = df.replace({np.nan: None})
        
        # Comprehensive mapping of all common column variations
        mapping = {
            'Gameplay Mode': 'gameplay_mode',
            'Level Name': 'level_name',
            'Kāṇḍa': 'kanda',
            'Ka': 'kanda',
            'Character': 'character',
            'Character Subtitle': 'character_subtitle',
            'Combo Type': 'combo_type',
            'Virtue Category': 'virtue_category',
            'Virtue / Karma': 'virtue_karma',
            'Virtue/Karma': 'virtue_karma',
            'Card Subtitle': 'card_subtitle',
            'Karma Polarity': 'karma_polarity',
            'Karma Direction (at play)': 'karma_direction',
            'Karma Direction': 'karma_direction',
            'Combo Status (Base)': 'combo_status',
            'Combo Status': 'combo_status',
            'Validation Reason (Valmiki-based)': 'validation_reason',
            'Validation Reason': 'validation_reason',
            'Validation': 'validation_reason',
            'Valmiki Reference Anchor': 'valmiki_reference_anchor',
            'Intensity Score': 'intensity_score',
            'Rank (1=Highest)': 'rank',
            'Rank': 'rank',
            'Character Card Number': 'character_card_number',
            'Virtu/Karma Card Number': 'virtue_karma_card_number',
            'Virtue/Karma Card Number': 'virtue_karma_card_number',
            'Avatar ExplainRanking (Short)': 'avatar_explain',
            'Avatar Explain': 'avatar_explain',
            'Avatar Follow-up (Firm)': 'avatar_followup',
            'Avatar Followup': 'avatar_followup',
            'App Explanation (if Excluded)': 'app_explanation',
            'App Explanation': 'app_explanation',
            'Final Status (Mode 2)': 'final_status',
            'Final Status (Mode 3)': 'final_status',
            'Final Status (Mode 4)': 'final_status',
            'Final Status (Mode 5)': 'final_status',
            'Final Status': 'final_status',
            'Combo Display': 'combo_display',
            'Avatar Dispute Response (Default)': 'avatar_dispute_response',
            'Avatar Dispute Response (Witty Options)': 'avatar_dispute_witty'
        }
        
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Check for existing table columns
            table_cols_res = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'card_combos'")
            table_cols = [c['column_name'] for c in table_cols_res]
            
            final_mapping = {}
            for excel_col, db_col in mapping.items():
                if excel_col in df.columns and db_col in table_cols and db_col not in final_mapping.values():
                    final_mapping[excel_col] = db_col
            
            db_columns = list(final_mapping.values())
            excel_columns = list(final_mapping.keys())
            
            # Determine mode name from file if not in column
            if 'gameplay_mode' in db_columns:
                actual_mode = str(df.iloc[0]['Gameplay Mode'])
            else:
                actual_mode = "Mode Unknown"

            # Clean existing individual mode data
            print(f"Cleaning existing data for {actual_mode}...")
            await conn.execute("DELETE FROM card_combos WHERE gameplay_mode = $1", actual_mode)
            
            cols_str = ", ".join(db_columns)
            vals_str = ", ".join([f"${i+1}" for i in range(len(db_columns))])
            query = f"INSERT INTO card_combos ({cols_str}) VALUES ({vals_str})"
            
            records_to_insert = []
            for _, row in df.iterrows():
                record = [row[col] for col in excel_columns]
                records_to_insert.append(record)
                
            print(f"Inserting {len(records_to_insert)} records...")
            await conn.executemany(query, records_to_insert)
            
            count = await conn.fetchval("SELECT COUNT(*) FROM card_combos WHERE gameplay_mode = $1", actual_mode)
            print(f"SUCCESS: Imported {count} records for {actual_mode}")
            
        finally:
            await conn.close()
    except Exception as e:
        print(f"FAILED to process {file_path}: {e}")

async def master_import():
    print(f"🚀 MASTER IMPORT: Connecting to {DATABASE_URL}")
    
    conn = await asyncpg.connect(DATABASE_URL)
    # TOTAL WIPE for fresh start as requested
    print("🧹 CLEARING ALL OLD DATA (TRUNCATE)...")
    await conn.execute("TRUNCATE TABLE card_combos RESTART IDENTITY")
    await conn.close()

    # Iterate through all Excel files (Mode 2 to 5)
    for i in range(2, 6):
        file_name = f"EpicVerse_Mode_{i}.xlsx"
        file_path = os.path.join(data_dir, file_name)
        if os.path.exists(file_path):
             await import_mode_file(file_path)
        else:
             print(f"Skipping {file_name} (Not found)")

    print("\n✅ MASTER IMPORT COMPLETE!")

if __name__ == "__main__":
    asyncio.run(master_import())
