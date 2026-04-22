import asyncio
import asyncpg
import pandas as pd
import random
import string
import os

# --- DATABASE CONFIG ---
DATABASE_URL = "postgresql://REDACTED_PRODUCTION_CREDENTIAL_REMOVED"

def generate_random_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return "EPIC-" + "".join(random.choice(chars) for _ in range(length))

async def main():
    print("[INVITE-SYNC] Starting Invite Code Refresh (6-Char Format)...")
    
    # 1. Generate 500 codes (6 characters)
    new_codes = [generate_random_code(6) for _ in range(500)]
    
    # 2. Define Developer Master Code
    master_code = "EPIC-DEV-MASTER"
    all_codes = new_codes + [master_code]
    
    # 3. Save to Excel
    df = pd.DataFrame({
        "Invite Code": all_codes, 
        "Type": ["Standard"]*500 + ["Master Developer"],
        "Max Uses": [1]*500 + [9999999]
    })
    export_path = "e:/kriyora/EpicVerse/EpicVerse_Invite_Keys_Final.xlsx"
    df.to_excel(export_path, index=False)
    print(f"Excel Sheet Created: {export_path}")
    
    # 4. Update Database
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Clear existing
        await conn.execute("DELETE FROM invite_codes")
        print("Old Invite Codes Purged.")
        
        # Insert Standard Codes (1 use each)
        standard_values = [(code, 1, 0) for code in new_codes]
        await conn.executemany("INSERT INTO invite_codes (code, max_uses, current_uses) VALUES ($1, $2, $3)", standard_values)
        
        # Insert Master Code (Unlimited use)
        await conn.execute("INSERT INTO invite_codes (code, max_uses, current_uses) VALUES ($1, $2, $3)", master_code, 9999999, 0)
        
        await conn.close()
        print(f"501 New Invite Codes (6-Char) Imported to Production Database.")
        print(f"MASTER CODE IS: {master_code}")
        
    except Exception as e:
        print(f"Database Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
