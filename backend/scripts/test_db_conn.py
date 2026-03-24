import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

async def test_conn():
    print(f"Testing connection to {DATABASE_URL}...")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DATABASE_URL), timeout=10)
        print("Connected successfully!")
        await conn.close()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_conn())
