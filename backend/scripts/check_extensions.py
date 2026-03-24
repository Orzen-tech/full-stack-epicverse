import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    try:
        rows = await conn.fetch("SELECT extname FROM pg_extension")
        print("Extensions:")
        for row in rows:
            print(f"- {row['extname']}")
    except Exception as e:
        print(f"Error: {e}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
