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
    
    # Generic mapping variations found across Excel files
    mapping = {
        'Gameplay Mode': 'gameplay_mode',
        'Level Name': 'level_name',
        'Kāṇḍa': 'kanda',
        'Ka': 'kanda', # Fallback
        'Character': 'character',
        'Character Subtitle': 'character_subtitle',
        'Combo Type': 'combo_type',
        'Virtue Category': 'virtue_category',
        'Virtue / Karma': 'virtue_karma',
        'Virtue/Karma': 'virtue_karma', # Fallback
        'Card Subtitle': 'card_subtitle',
        'Karma Polarity': 'karma_polarity',
        'Karma Direction (at play)': 'karma_direction',
        'Karma Direction': 'karma_direction', # Fallback
        'Combo Status (Base)': 'combo_status',
        'Combo Status': 'combo_status', # Fallback
        'Validation Reason (Valmiki-based)': 'validation_reason',
        'Validation Reason': 'validation_reason', # Fallback
        'Validation': 'validation_reason', # Fallback
        'Valmiki Reference Anchor': 'valmiki_reference_anchor',
        'Intensity Score': 'intensity_score',
        'Rank (1=Highest)': 'rank',
        'Rank': 'rank', # Fallback
        'Character Card Number': 'character_card_number',
        'Virtu/Karma Card Number': 'virtue_karma_card_number',
        'Virtue/Karma Card Number': 'virtue_karma_card_number', # Fallback
        'Avatar ExplainRanking (Short)': 'avatar_explain',
        'Avatar Explain': 'avatar_explain', # Fallback
        'Avatar Follow-up (Firm)': 'avatar_followup',
        'Avatar Followup': 'avatar_followup', # Fallback
        'App Explanation (if Excluded)': 'app_explanation',
        'App Explanation': 'app_explanation', # Fallback
        'Final Status (Mode 3)': 'final_status',
        'Final Status (Mode 4)': 'final_status',
        'Final Status (Mode 5)': 'final_status',
        'Final Status': 'final_status', # Fallback
        'Combo Display': 'combo_display',
        'Avatar Dispute Response (Default)': 'avatar_dispute_response',
        'Avatar Dispute Response (Witty Options)': 'avatar_dispute_witty'
    }
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        table_cols_res = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'card_combos'")
        table_cols = [c['column_name'] for c in table_cols_res]
        
        final_mapping = {}
        for excel_col, db_col in mapping.items():
            if excel_col in df.columns and db_col in table_cols and db_col not in final_mapping.values():
                final_mapping[excel_col] = db_col
        
        db_columns = list(final_mapping.values())
        excel_columns = list(final_mapping.keys())
        
        # Determine actual mode name from data if possible
        if 'Gameplay Mode' in df.columns:
            actual_mode = str(df['Gameplay Mode'].iloc[0])
        else:
            actual_mode = "Unknown"
            
        print(f"Detected Mode name in Excel: {actual_mode}")
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
        
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await conn.close()

async def main():
    base_path = 'e:/kriyora/EpicVerse/backend/data/'
    for i in range(3, 6):
        file_path = os.path.join(base_path, f'EpicVerse_Mode_{i}.xlsx')
        if os.path.exists(file_path):
            await import_mode(file_path)

if __name__ == "__main__":
    asyncio.run(main())
