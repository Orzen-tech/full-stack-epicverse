import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    url = os.getenv('DATABASE_URL')
    if not url:
        print("Error: DATABASE_URL not found in .env")
        return
        
    try:
        conn = await asyncpg.connect(url)
        print(f"Connected to {url}")
        
        # Check constraints for card_embeddings
        constraints = await conn.fetch("""
            SELECT conname, contype, a.attname
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE conrelid = 'card_embeddings'::regclass;
        """)
        print("\n[CONSTRAINTS] card_embeddings:")
        for c in constraints:
            print(f" - {c['conname']} ({c['contype']}): {c['attname']}")

        # Check constraints for users
        constraints_users = await conn.fetch("""
            SELECT conname, contype, a.attname
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE conrelid = 'users'::regclass;
        """)
        print("\n[CONSTRAINTS] users:")
        for c in constraints_users:
            print(f" - {c['conname']} ({c['contype']}): {c['attname']}")
            
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
