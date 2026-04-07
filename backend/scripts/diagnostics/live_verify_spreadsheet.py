
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator, run_ai_pipeline
from app.services.retriever import init_db_pool

async def live_verify():
    await init_db_pool()
    mode = "Origin Arc( Balakanda)"
    
    # Test 1: Standard Status Check
    print("--- 🏁 TEST 1: Rapid Status Check ('7 and 29 combo') ---")
    pipe_out = await run_ai_pipeline("7 and 29 combo", game_mode=mode)
    if "final_response" in pipe_out:
        print(f"AI STATUS RESP: {pipe_out['final_response']}")
    else:
        async for chunk in run_ai_pipeline_generator("7 and 29 combo", game_mode=mode):
            print(f"AI STATUS RESP: {chunk.get('text_chunk', '')}")
            break
            
    # Test 2: Scholarly Explanation
    print("\n--- 🏁 TEST 2: Scholarly Explanation ('Why is 7 and 29 valid?') ---")
    full_reason = ""
    async for chunk in run_ai_pipeline_generator("Explain why 7 and 29 is valid", game_mode=mode):
        full_reason += chunk.get('text_chunk', '')
    
    print(f"AI SCHOLARLY REASON: {full_reason}")
    print(f"\n✅ ACCURACY MATCH: {'YES' if 'Bala Kanda, Sarga 27' in full_reason or 'Sarga 27' in full_reason else 'NO'}")

if __name__ == "__main__":
    asyncio.run(live_verify())
