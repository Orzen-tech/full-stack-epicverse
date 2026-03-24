import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("pgvector enabled successfully.")
    except Exception as e:
        print(f"Failed to enable pgvector: {e}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
