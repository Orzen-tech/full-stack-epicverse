import asyncio
from dotenv import load_dotenv

load_dotenv()

from app.services.retriever import init_db_pool

async def check():
    print("Checking Database Connection...")
    try:
        pool = await init_db_pool()
        async with pool.acquire() as conn:
            version = await conn.fetchval('SELECT version();')
            print(f"DATABASE ONLINE 🟢\nVersion: {version}")
        await pool.close()
    except Exception as e:
        print(f"DATABASE OFFLINE 🔴: {e}")

if __name__ == "__main__":
    asyncio.run(check())
