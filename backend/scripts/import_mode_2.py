import asyncio
import asyncpg
import pandas as pd
import numpy as np
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FILE_PATH = 'e:/kriyora/model_try/backend/data/EpicVerse_Mode_2.xlsx'

async def import_mode_2():
    print(f"Reading Excel file: {FILE_PATH}")
    df = pd.read_excel(FILE_PATH)
    # Replace NaN with None for database compatibility
    df = df.replace({np.nan: None})
    
    # Mapping Excel columns to Database columns
    # Adjust mapping based on actual Excel column names found in previous steps
    mapping = {
        'Gameplay Mode': 'gameplay_mode',
        'Level Name': 'level_name',
        'Kāṇḍa': 'kanda',
        'Character': 'character',
        'Character Subtitle': 'character_subtitle',
        'Combo Type': 'combo_type',
        'Virtue Category': 'virtue_category',
        'Virtue / Karma': 'virtue_karma',
        'Card Subtitle': 'card_subtitle',
        'Karma Polarity': 'karma_polarity',
        'Karma Direction (at play)': 'karma_direction',
        'Combo Status (Base)': 'combo_status',
        'Validation Reason (Valmiki-based)': 'validation_reason',
        'Valmiki Reference Anchor': 'valmiki_reference_anchor',
        'Intensity Score': 'intensity_score',
        'Rank (1=Highest)': 'rank',
        'Character Card Number': 'character_card_number',
        'Virtu/Karma Card Number': 'virtue_karma_card_number',
        'Avatar ExplainRanking (Short)': 'avatar_explain',
        'Avatar Follow-up (Firm)': 'avatar_followup',
        'App Explanation (if Excluded)': 'app_explanation',
        'Final Status (Mode 2)': 'final_status',
        'Combo Display': 'combo_display',
        'Avatar Dispute Response (Default)': 'avatar_dispute_response',
        'Avatar Dispute Response (Witty Options)': 'avatar_dispute_witty'
    }
    
    print(f"Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # Check if table columns match our mapping
        table_cols_res = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'card_combos'")
        table_cols = [c['column_name'] for c in table_cols_res]
        
        # Determine columns that actually exist in the table and are in our Excel
        final_mapping = {}
        for excel_col, db_col in mapping.items():
            if excel_col in df.columns and db_col in table_cols:
                final_mapping[excel_col] = db_col
        
        db_columns = list(final_mapping.values())
        excel_columns = list(final_mapping.keys())
        
        # Special handling for combo_display if not in Excel
        if 'combo_display' in table_cols and 'combo_display' not in db_columns:
            # We can generate it if 'Character' and 'Virtue/Karma' exist
            if 'Character' in df.columns and 'Virtue/Karma' in df.columns:
                df['combo_display'] = df['Character'].astype(str) + " + " + df['Virtue/Karma'].astype(str)
                db_columns.append('combo_display')
                excel_columns.append('combo_display')

        # Clear existing Mode 2 data to avoid duplicates if re-running
        print("Cleaning existing Mode 2 data...")
        await conn.execute("DELETE FROM card_combos WHERE gameplay_mode = $1", "Mode 2")
        
        # Prepare insert query
        cols_str = ", ".join(db_columns)
        vals_str = ", ".join([f"${i+1}" for i in range(len(db_columns))])
        query = f"INSERT INTO card_combos ({cols_str}) VALUES ({vals_str})"
        
        records_to_insert = []
        for _, row in df.iterrows():
            record = [row[col] for col in excel_columns]
            records_to_insert.append(record)
            
        print(f"Inserting {len(records_to_insert)} records...")
        await conn.executemany(query, records_to_insert)
        
        # Verify
        count = await conn.fetchval("SELECT COUNT(*) FROM card_combos WHERE gameplay_mode = $1", "Mode 2")
        print(f"Successfully imported {count} records for Mode 2.")
        
    except Exception as e:
        print(f"Error during import: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(import_mode_2())
