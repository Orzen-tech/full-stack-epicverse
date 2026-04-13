import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")

async def list_cols():
    conn = await asyncpg.connect(DATABASE_URL)
    cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'card_combos'")
    print([c['column_name'] for c in cols])
    await conn.close()

if __name__ == "__main__":
    asyncio.run(list_cols())
