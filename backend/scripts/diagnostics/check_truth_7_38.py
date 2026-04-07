
import asyncio
from app.services.retriever import query_postgres_database, init_db_pool

async def check_truth():
    await init_db_pool()
    mode = "GlowLine (Kishkindha Kanda)"
    char = "7"
    attr = "38"
    result = await query_postgres_database(mode, char, attr)
    print(f"DATABASE TRUTH FOR {char} & {attr} in {mode}:")
    print(result)

if __name__ == "__main__":
    asyncio.run(check_truth())
