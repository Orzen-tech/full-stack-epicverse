import asyncio
import asyncpg
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
raw_url = os.getenv("DATABASE_URL")
DATABASE_URL = raw_url.replace("postgresql+asyncpg://", "postgresql://")

async def double_check():
    async with httpx.AsyncClient() as client:
        ip1 = (await client.get("https://api.ipify.org")).text
        ip2 = (await client.get("http://ifconfig.me")).text
    
    print(f"Current detected IPs:\n - ipify: {ip1}\n - ifconfig: {ip2}")
    
    print(f"Testing connection to: {DATABASE_URL}")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(DATABASE_URL), timeout=10)
        print("✅ SUCCESS! Connected to DB.")
        await conn.close()
    except Exception as e:
        print(f"❌ FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(double_check())
