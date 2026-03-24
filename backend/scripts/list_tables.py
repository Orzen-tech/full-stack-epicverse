import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

async def list_all_tables():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tables = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        print("\n--- All Tables ---")
        for t in tables:
            print(t['table_name'])
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(list_all_tables())
