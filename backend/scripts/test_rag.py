import asyncio
import os
from app.services.retriever import semantic_search_database
from dotenv import load_dotenv

load_dotenv()

async def test():
    # Test a general question that should trigger RAG
    query = "Why did Rama go to the forest?"
    print(f"Testing RAG for query: {query}")
    result = await semantic_search_database(query)
    print("\nResults:")
    print(result)

if __name__ == "__main__":
    asyncio.run(test())
