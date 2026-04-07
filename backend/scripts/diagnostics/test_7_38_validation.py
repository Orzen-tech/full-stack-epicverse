
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator
from app.services.retriever import init_db_pool

async def run_test():
    await init_db_pool()
    query = "Check combo 7 and 38"
    mode = "GlowLine (Kishkindha Kanda)"
    lang = "English"
    
    print(f"--- 🚀 Testing Combo: 7 & 38 in {mode} ---")
    start_time = time.time()
    
    first_chunk_time = None
    final_text = ""
    
    async for chunk in run_ai_pipeline_generator(query, game_mode=mode, user_lang=lang):
        if first_chunk_time is None:
            first_chunk_time = time.time() - start_time
        final_text += chunk.get('text_chunk', '')
        # print(f"Chunk: {chunk['text_chunk']}")
        
    print(f"\n📊 FINAL RESULTS:")
    print(f"- **AI RESPONSE:** {final_text}")
    print(f"- **LATENCY TO FIRST WORD:** {first_chunk_time:.3f}s")
    print(f"- **ACCURACY:** {'✅ CORRECT (Invalid)' if 'invalid' in final_text.lower() or 'not' in final_text.lower() else '❌ INCORRECT'}")
    print(f"--- 🏁 Test Complete ---")

if __name__ == "__main__":
    asyncio.run(run_test())
