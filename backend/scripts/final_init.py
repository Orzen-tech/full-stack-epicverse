import asyncio
from app.services.user_db import init_db
from app.services.retriever import init_db_pool

async def main():
    print("🚀 Connecting to database to create tables...")
    await init_db_pool()
    await init_db()
    print("✅ Database initialization complete. Tables 'users' and 'chat_history' are ready!")

if __name__ == "__main__":
    asyncio.run(main())
