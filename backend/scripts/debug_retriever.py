import asyncio
from app.services.retriever import query_postgres_database
import os
from dotenv import load_dotenv

load_dotenv()

async def debug_retriever():
    # Attempting to query exactly like the AI would
    # Case 1: 1 and 28
    print("--- CASE 1: 1 and 28 in Mode 1 ---")
    result = await query_postgres_database("Mode 1", "1", "28")
    print(f"Result: {result}\n")
    
    # Case 2: 7 and 25 (Vishwamitra and Duty) - Seen in screenshot
    print("--- CASE 2: 7 and 25 (Vishwamitra and Duty) in Mode 1 ---")
    result = await query_postgres_database("Mode 1", "7", "25")
    print(f"Result: {result}\n")

if __name__ == "__main__":
    asyncio.run(debug_retriever())
