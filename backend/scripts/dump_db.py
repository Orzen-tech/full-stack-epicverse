import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# Load database URL
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def dump_db():
    print(f"Connecting to: {DATABASE_URL}")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # 1. List all tables
        tables = await conn.fetch("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        print("\n=== SYSTEM TABLES ===")
        for t in tables:
            name = t['table_name']
            count = await conn.fetchval(f'SELECT count(*) FROM "{name}"')
            print(f" - {name} ({count} rows)")

        # 2. Dump Users
        print("\n=== USERS DATA (ALL) ===")
        users = await conn.fetch('SELECT * FROM users')
        for u in users:
            print(u)

        # 3. Dump Chat History
        print("\n=== CHAT HISTORY (LAST 20) ===")
        chats = await conn.fetch('SELECT * FROM chat_history ORDER BY created_at DESC LIMIT 20')
        for c in chats:
            print(f"[{c['created_at']}] {c['role'].upper()} (User {c['uid']}): {c['message']}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(dump_db())
