
import asyncio
import time
import os
import sys

# Add backend to path
sys.path.append(os.getcwd())

from app.services.ai_pipeline import run_ai_pipeline_generator
from app.services.retriever import init_db_pool

async def measure_query(query, lang):
    start_time = time.time()
    first_chunk_time = None
    first_text = ""
    
    async for chunk in run_ai_pipeline_generator(query, user_lang=lang):
        if first_chunk_time is None:
            first_chunk_time = time.time() - start_time
            first_text = chunk.get('text_chunk', '')
            break
            
    return first_chunk_time, first_text

async def main():
    await init_db_pool()
    
    test_cases = [
        ("How to play with card 10 and 12?", "English"),
        ("மோடு 1-ல் 4 மற்றும் 56 காம்போ என்ன?", "Tamil"),
        ("महारानी 2 और राजा 15 का क्या होगा?", "Hindi"),
        ("¿Es válido el combo 20 y 45?", "Spanish"),
        ("モード1で10と22の組み合わせはどうですか？", "Japanese")
    ]
    
    print(f"{'Language':<12} | {'Query':<40} | {'Latency':<7} | {'Response (First Chunk)'}")
    print("-" * 100)
    
    for query, lang in test_cases:
        # We append a random salt to bypass query cache for fresh measurement
        salt = f" (Test-{int(time.time())})"
        latency, resp = await measure_query(query + salt, lang)
        print(f"{lang:<12} | {query[:37]+'...':<40} | {latency:.2f}s    | {resp}")

if __name__ == "__main__":
    asyncio.run(main())
