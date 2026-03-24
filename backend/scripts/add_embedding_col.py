import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    try:
        # Add a vector column to store embeddings
        await conn.execute("ALTER TABLE card_combos ADD COLUMN IF NOT EXISTS embedding vector(1536)")
        print("Embedding column added successfully.")
    except Exception as e:
        print(f"Failed to add embedding column: {e}")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(run())
