
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator
from app.services.retriever import init_db_pool

async def audit_all():
    await init_db_pool()
    mode = "Origin Arc( Balakanda)"
    
    test_cases = [
        ("Why is 7 and 25 valid?", "Sarga 19"),
        ("Explain combo 7 and 31.", "Sarga 22"),
        ("Is 7 and 32 a valid match?", "Sarga 19")
    ]
    
    print(f"{'Combo':<15} | {'Expected Citation':<20} | {'AI Found Citations?':<20} | {'Result'}")
    print("-" * 100)
    
    for query, citation in test_cases:
        full_reason = ""
        async for chunk in run_ai_pipeline_generator(query, game_mode=mode):
            full_reason += chunk.get('text_chunk', '')
        
        found = citation in full_reason
        print(f"{query:<15} | {citation:<20} | {'✅ Found' if found else '❌ Missing':<20} | {'PASS' if found else 'FAIL'}")

if __name__ == "__main__":
    asyncio.run(audit_all())
