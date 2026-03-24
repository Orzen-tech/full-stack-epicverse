import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    rows = await conn.fetch("SELECT validation_reason, valmiki_reference_anchor, kanda FROM card_combos WHERE validation_reason IS NOT NULL LIMIT 3")
    for row in rows:
        print(f"Reason: {row['validation_reason']}")
        print(f"Ref: {row['valmiki_reference_anchor']}")
        print(f"Kanda: {row['kanda']}")
        print("-" * 20)
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
