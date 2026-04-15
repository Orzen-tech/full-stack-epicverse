import asyncio
import random
import string
import sys
import os
import pandas as pd

# Add parent directory to path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.user_db import get_db_pool, init_db

def generate_3l3n_code():
    """Generates a 6-character code: 3 Letters + 3 Numbers (e.g., ABC123)"""
    letters = ''.join(random.choice(string.ascii_uppercase) for _ in range(3))
    numbers = ''.join(random.choice(string.digits) for _ in range(3))
    return f"{letters}{numbers}"

async def create_invites(count=500, max_uses=1, export_file="EpicVerse_Invites.xlsx"):
    """Creates a batch of invite codes and exports to Excel."""
    await init_db()
    pool = await get_db_pool()
    
    if not pool:
        print("Error: Could not connect to database.")
        return

    codes = set()
    print(f"Generating {count} unique codes in 3L3N format...")
    
    while len(codes) < count:
        codes.add(generate_3l3n_code())

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
    
    # Export to Excel
    try:
        df = pd.DataFrame(final_codes, columns=["Invite Code"])
        df.to_excel(export_file, index=False)
        print(f"\nSUCCESS: Generated and saved {len(final_codes)} codes to {export_file}")
    except Exception as e:
        print(f"Failed to export Excel: {e}")
        # Fallback to CSV
        csv_file = export_file.replace(".xlsx", ".csv")
        with open(csv_file, "w") as f:
            f.write("Invite Code\n")
            f.write("\n".join(final_codes))
        print(f"Exported to CSV instead: {csv_file}")

if __name__ == "__main__":
    count = 500
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
        
    asyncio.run(create_invites(count=count))
