import asyncio
import random
import string
import sys
import os
import pandas as pd

# Add parent directory to path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.user_db import get_db_pool, init_db

def generate_mixed_code(length=6):
    """Generates a mixed 6-character alphanumeric code (e.g., A2S3D4)"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

async def create_invites(count=500, max_uses=1, export_file="EpicVerse_New_Invites.xlsx"):
    """Clears old codes, creates new ones, and exports to Excel."""
    await init_db()
    pool = await get_db_pool()
    
    if not pool:
        print("Error: Could not connect to database.")
        return

    # 1. Clear OLD codes (Except master key)
    print("Clearing old invite codes from database...")
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM invite_codes WHERE code != 'EPIC-DEV-2026'")

    # 2. Generate NEW codes
    print(f"Generating {count} unique mixed alphanumeric codes...")
    codes = set()
    while len(codes) < count:
        codes.add(generate_mixed_code())

    final_codes = []
    async with pool.acquire() as conn:
        for code in codes:
            try:
                await conn.execute('''
                    INSERT INTO invite_codes (code, max_uses)
                    VALUES ($1, $2)
                    ON CONFLICT (code) DO NOTHING
                ''', code, max_uses)
                final_codes.append(code)
            except Exception as e:
                print(f"Failed to insert {code}: {e}")
    
    # 3. Export to Excel
    try:
        df = pd.DataFrame(final_codes, columns=["Invite Code"])
        df.to_excel(export_file, index=False)
        print(f"\nSUCCESS: Cleared DB and saved {len(final_codes)} NEW codes to {export_file}")
    except Exception as e:
        print(f"Failed to export Excel: {e}")
        # Fallback to CSV
        csv_file = export_file.replace(".xlsx", ".csv")
        with open(csv_file, "w") as f:
            f.write("Invite Code\n")
            f.write("\n".join(final_codes))
        print(f"Exported to CSV instead: {csv_file}")

if __name__ == "__main__":
    asyncio.run(create_invites(count=500))
