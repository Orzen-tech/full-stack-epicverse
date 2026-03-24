import asyncio
from app.services.retriever import query_postgres_database

async def test():
    print("Testing '1' and '27' in 'Mode 2'...")
    res = await query_postgres_database("Mode 2", "1", "27")
    print(res)

if __name__ == "__main__":
    asyncio.run(test())
