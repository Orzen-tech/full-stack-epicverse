
import asyncio
from app.services.retriever import query_postgres_database, init_db_pool

async def debug_7_32():
    await init_db_pool()
    result = await query_postgres_database("Origin Arc( Balakanda)", "7", "32")
    print(f"RAW DB TRUTH FOR 7 & 32 (Justice):")
    print(result)

if __name__ == "__main__":
    asyncio.run(debug_7_32())
