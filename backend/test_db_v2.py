import asyncio
import asyncpg
import os
import json
from dotenv import load_dotenv

load_dotenv()
raw_url = os.getenv("DATABASE_URL")
DATABASE_URL = raw_url.replace("postgresql+asyncpg://", "postgresql://")

async def test_backend_integrity():
    print(f"Testing connection to: {DATABASE_URL}")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ Connection successful!")
        
        # Check tables
        tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        print(f"Tables: {[t['table_name'] for t in tables]}")
        
        # Check counts
        if any(t['table_name'] == 'card_combos' for t in tables):
            count = await conn.fetchval("SELECT count(*) FROM card_combos")
            print(f"Card Combos Count: {count}")
            
            # Check unique modes
            modes = await conn.fetch("SELECT DISTINCT gameplay_mode FROM card_combos")
            print(f"Game Modes ({len(modes)}): {[m['gameplay_mode'] for m in modes]}")
            
            # Sample data
            if count > 0:
                sample = await conn.fetchrow("SELECT * FROM card_combos LIMIT 1")
                print(f"Sample Row: {json.dumps(dict(sample), default=str, indent=2)}")
        else:
            print("❌ Table 'card_combos' NOT FOUND!")
            
        await conn.close()
    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_backend_integrity())
