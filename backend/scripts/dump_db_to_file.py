import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def main():
    conn = await asyncpg.connect(DATABASE_URL)
    with open("db_dump.txt", "w", encoding="utf-8") as f:
        # Tables
        rows = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        f.write("=== TABLES ===\n")
        for r in rows:
            count = await conn.fetchval(f"SELECT count(*) FROM {r['table_name']}")
            f.write(f" - {r['table_name']} ({count} rows)\n")
        
        # Users
        f.write("\n=== USERS (ALL) ===\n")
        users = await conn.fetch("SELECT uid, email, current_mode FROM users")
        for u in users:
            f.write(f"ID: {u['uid']} | Email: {u['email']} | Mode: {u['current_mode']}\n")
            
        # Chat
        f.write("\n=== CHAT HISTORY (LAST 20) ===\n")
        chats = await conn.fetch("SELECT uid, role, message FROM chat_history ORDER BY created_at DESC LIMIT 20")
        for c in chats:
            f.write(f"User {c['uid']} | {c['role'].upper()}: {c['message']}\n")
            
    print("Database dump written to db_dump.txt")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
