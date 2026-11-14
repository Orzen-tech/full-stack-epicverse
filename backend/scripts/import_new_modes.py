import pandas as pd
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def import_all_modes():
    # Default: backend/data next to this script's parent; override with EPICVERSE_DATA_DIR.
    data_dir = os.environ.get("EPICVERSE_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
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
    
    # Everything below runs in ONE transaction: any failure or row-count mismatch rolls
    # back, so production is never left with an empty or half-loaded table.
    # NOTE: deleting card_combos also deletes card_embeddings (ON DELETE CASCADE).
    tx = conn.transaction()
    await tx.start()
    print("GLOBAL RESET: Deleting all existing card_combos...")
    await conn.execute("DELETE FROM card_combos")

    total_added = 0
    expected_rows = 0
    missing_files = []
    for i in range(1, 8):
        file = f"mode {i}.xlsx"
        path = os.path.join(data_dir, file)
        correct_mode = KANDA_MAP[i]
        
        if not os.path.exists(path):
            print(f"SKIPPING: {file} not found.")
            missing_files.append(file)
            continue

        print(f"--- Reading {file} (Target: {correct_mode}) ---")
        df = pd.read_excel(path).replace({pd.NA: None})
        # The message column is "App Message (Crisp)" in most sheets but was "App Message"
        # in the Mode 6 sheet; reading only the first name once loaded Mode 6 with blank text.
        msg_col = 'App Message (Crisp)' if 'App Message (Crisp)' in df.columns else 'App Message'
        if msg_col not in df.columns:
            await tx.rollback()
            raise SystemExit(f"{file}: no 'App Message (Crisp)' / 'App Message' column; rolled back")
        expected_rows += len(df)

        success_count = 0
        for _, row in df.iterrows():
            try:
                scripture_text = str(row.get(msg_col, '') or '').strip()
                if not scripture_text:
                    raise ValueError("blank message text")
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
    if missing_files or total_added != expected_rows:
        await tx.rollback()
        await conn.close()
        raise SystemExit(f"ABORTED and rolled back: loaded {total_added} of {expected_rows} rows; missing files: {missing_files}")
    await tx.commit()
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
